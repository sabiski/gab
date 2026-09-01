from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models import Avg, Count, Sum, Q, Min, Case, When, Value, IntegerField
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404, HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from datetime import datetime, timedelta
from urllib.parse import urlencode
import json

from accounts.two_factor import (
    SESSION_FLASH_KEY,
    clear_pending,
    get_pending_user,
    is_trusted_device,
    pending_context,
    remember_trusted_device,
    resend_code,
    start_pending_login,
    two_factor_enabled,
    verify_code,
)
from core.mail_messages import mail_error_for_user
from accounts.mail import (
    build_credentials_email,
    generate_temp_password,
    send_account_credentials,
)
from accounts.models import User, ClientProfile, CourierProfile, LoyaltyReward, LoyaltyTransaction, PartnerProfile, AuthorityProfile
from backoffice.decorators import (
    admin_roles,
    authority_access_roles,
    authority_roles,
    client_roles,
    courier_roles,
    pharmacy_permission_required,
    pharmacy_roles,
    regional_roles,
    partner_roles,
    portal_permission_required,
    role_required,
    superadmin_roles,
    support_roles,
)
from backoffice.image_validation import IMAGE_RULES, validate_uploaded_image
from backoffice.utils import paginate
from catalog.models import (
    Category,
    Medicine,
    MedicineQuestion,
    PharmacyStock,
    StockInventorySession,
    StockMovement,
)
from core.stock_inventory import (
    InventoryError,
    active_inventory_session,
    cancel_inventory_session,
    complete_inventory_session,
    import_stock_csv,
    quick_stock_movement,
    save_inventory_counts,
    start_inventory_session,
)
from core.cart import ensure_delivery_for_order
from core.courier_gps import CourierGpsRequired, require_gps_from_post
from core.delivery_consistency import (
    pharmacy_active_delivery_filter,
    repair_stale_delivering_orders,
)
from core.order_notifications import (
    notify_delivery_outcome,
    notify_pharmacy_courier_assigned,
)
from core.order_traceability import (
    HandoffError,
    resolve_courier_pharmacy_pickup,
    DeliveryValidationError,
    ensure_pharmacy_handoff_code,
    order_needs_pharmacy_handoff,
    order_shows_client_delivery_qr,
    parse_traceability_qr,
    render_traceability_qr_png,
    resolve_courier_delivery_validation,
    resolve_handoff_order_from_qr,
    validate_pharmacy_handoff,
)
from core.order_evaluation import (
    EvaluationError,
    handle_order_delivered,
    order_needs_courier_rating,
    submit_order_evaluation,
)
from core.order_tracking import order_tracking_steps
from core.loyalty import (
    LoyaltyError,
    active_vouchers,
    compute_loyalty_benefit,
    load_loyalty_settings,
    loyalty_level,
    redeem_reward,
    sum_expiring_points,
    tier_discount_percent,
)
from core.models import Advertisement, PharmacistTip, SiteHero
from deliveries.models import Delivery, DeliveryIncident
from notifications.models import (
    Notification,
    AuditLog,
    SupportTicket,
    PharmacyConversation,
    PharmacyMessage,
    EmergencyAlert,
    NotificationCampaign,
    PlatformNotificationSettings,
)
from notifications.services import notify_user, process_due_campaigns, send_campaign
from orders.models import Order, OrderEvaluation, OrderItem, Prescription, normalize_validation_code
from payments.models import (
    Payment,
    Subscription,
    InsuranceProvider,
    InsuranceClaim,
    OrderSettlement,
    PlatformPaymentSettings,
)
from pharmacies.models import Pharmacy, PharmacyDocument, PharmacyEmployee
from core.pharmacy_compliance import (
    PHARMACY_REQUIRED_DOCUMENTS,
    pharmacy_compliance_summary,
    pharmacy_document_checklist,
)
from core.pharmacy_filters import filter_pharmacy_orders, list_query_params
from core.pharmacy_access import (
    PERM_ORDERS,
    PERM_RX,
    PERM_SETTINGS,
    PERM_STATS,
    PERM_STOCKS,
    PERM_STAFF,
    has_pharmacy_permission,
    pharmacy_permission_flags,
    pharmacy_for_user as _pharmacy_for_access,
    pharmacies_for_user as _pharmacies_for_access,
)
from core.pharmacy_portal import (
    EMERGENCY_ESCALATION_MINUTES,
    PREP_SLA_MINUTES,
    delivery_performance,
    escalate_stale_emergencies,
    estimate_margin,
    mark_preparing,
    nearby_couriers,
    payment_breakdown,
    pending_courier_requests,
    process_order_refund,
    sales_by_category,
    top_sold_products,
)
from core.report_export import (
    pharmacy_stats_csv_response,
    pharmacy_stats_pdf_response,
    pharmacy_stats_xlsx_response,
)
from core.delivery_transfer import (
    accept_handoff_offer,
    admin_prepare_handoff,
    clear_pending_transfer,
    escalate_stale_transfers,
    handoff_offers_for_courier,
    pending_handoff_validations,
    request_delivery_handoff,
    validate_handoff_code,
)
from django.http import HttpResponse
import csv
import secrets

PHARMACY_REFUSAL_REASONS = [
    ("rx_unreadable", "Ordonnance illisible ou incomplète"),
    ("rx_invalid", "Ordonnance expirée ou non conforme"),
    ("rx_suspicious", "Document douteux / non authentique"),
    ("out_of_stock", "Médicament(s) indisponible(s)"),
    ("dosage", "Posologie ou produit non cohérent avec l’ordonnance"),
    ("delivery_zone", "Zone de livraison non couverte"),
    ("other", "Autre motif"),
]


def _pharmacy_refusal_text(reason_code, detail=""):
    label = dict(PHARMACY_REFUSAL_REASONS).get(reason_code)
    if not label:
        return None, "Choisissez un motif de refus."
    detail = (detail or "").strip()
    if reason_code == "other" and not detail:
        return None, "Précisez le motif du refus."
    if detail:
        return f"{label} — {detail}", None
    return label, None


def _restore_order_stock(order, pharmacy):
    for item in order.items.select_related("medicine"):
        stock = PharmacyStock.objects.filter(
            pharmacy=pharmacy, medicine=item.medicine
        ).first()
        if stock:
            stock.quantity += item.quantity
            stock.save(update_fields=["quantity", "updated_at"])


def _notify_client(order, title, message, extra=None, *, notification_type=None):
    data = {"order_id": order.id, "code": order.code}
    if extra:
        data.update(extra)
    notify_user(
        order.client,
        title,
        message,
        notification_type=notification_type or Notification.Type.ORDER,
        data=data,
        transactional=True,
    )


def _notify_order_refused(order, reason_text):
    _notify_client(
        order,
        f"Commande {order.code} refusée",
        f"La pharmacie a refusé votre commande. Motif : {reason_text}",
        {"reason": reason_text},
        notification_type=Notification.Type.ERROR,
    )


def _notify_delivery_failed_client(order, reason_text):
    _notify_client(
        order,
        f"Livraison échouée — {order.code}",
        f"La livraison n'a pas pu être finalisée. {reason_text}",
        {"reason": reason_text, "event": "delivery_failed"},
        notification_type=Notification.Type.ERROR,
    )


def _notify_order_status(order, previous_status=None):
    """Informe le client du nouveau statut de commande."""
    pharmacy_name = order.pharmacy.name if order.pharmacy_id else "la pharmacie"
    status = order.status
    messages_by_status = {
        Order.Status.PREPARING: (
            f"Commande {order.code} en préparation",
            f"{pharmacy_name} prépare votre commande.",
        ),
        Order.Status.CONFIRMED: (
            f"Commande {order.code} confirmée",
            f"{pharmacy_name} a confirmé votre commande.",
        ),
        Order.Status.READY: (
            f"Commande {order.code} prête",
            f"Votre commande est prête"
            + (
                " pour le retrait."
                if order.delivery_mode == Order.DeliveryMode.PICKUP
                else ". Un livreur peut bientôt la prendre en charge."
            ),
        ),
        Order.Status.DELIVERING: (
            f"Commande {order.code} en livraison",
            "Votre commande est en cours de livraison.",
        ),
        Order.Status.DELIVERED: (
            f"Commande {order.code} livrée",
            "Votre commande a été livrée. Merci pour votre confiance.",
        ),
    }
    if status == Order.Status.PREPARING and previous_status == Order.Status.AWAITING_RX:
        _notify_client(
            order,
            f"Ordonnance validée — {order.code}",
            f"{pharmacy_name} a validé votre ordonnance. La commande passe en préparation.",
            {"event": "rx_validated"},
        )
        return
    payload = messages_by_status.get(status)
    if payload:
        ntype = (
            Notification.Type.SUCCESS
            if status == Order.Status.DELIVERED
            else Notification.Type.ORDER
        )
        _notify_client(
            order, payload[0], payload[1], {"status": status}, notification_type=ntype
        )


# Rôles créés uniquement par l'admin (pas d'auto-inscription)
STAFF_CREATE_ROLES = [
    (User.Role.PHARMACIST, "Pharmacien"),
    (User.Role.COURIER, "Livreur"),
    (User.Role.ADMIN, "Administrateur"),
    (User.Role.AUTHORITY, "Autorité Sanitaire"),
    (User.Role.SUPPORT, "Support"),
    (User.Role.REGIONAL_SUPERVISOR, "Superviseur régional"),
]


def _apply_password_change(request, user):
    """Traite un changement de mot de passe depuis POST. Retourne True si traité."""
    if request.POST.get("action") != "change_password":
        return False
    current = request.POST.get("current_password", "")
    new1 = request.POST.get("new_password", "")
    new2 = request.POST.get("new_password2", "")
    if not user.check_password(current):
        messages.error(request, "Mot de passe actuel incorrect.")
    elif len(new1) < 8:
        messages.error(request, "Nouveau mot de passe : 8 caractères minimum.")
    elif new1 != new2:
        messages.error(request, "La confirmation ne correspond pas.")
    else:
        user.set_password(new1)
        user.save(update_fields=["password"])
        # Reconnecter si c'est l'utilisateur courant (session invalidée sinon)
        if request.user.pk == user.pk:
            login(request, user)
        messages.success(request, "Mot de passe mis à jour.")
    return True


def _create_staff_user(request, *, role, username, email, first_name, last_name, phone, city, password=""):
    """Crée un compte pro, génère un MDP si besoin, envoie l'e-mail."""
    plain = (password or "").strip() or generate_temp_password()
    user = User.objects.create_user(
        username=username,
        email=email,
        password=plain,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        role=role,
        status=User.Status.ACTIVE,
        city=city or "Libreville",
    )
    if role == User.Role.COURIER:
        CourierProfile.objects.get_or_create(user=user)
    elif role == User.Role.CLIENT:
        ClientProfile.objects.get_or_create(user=user)

    delivery = None
    # Envoi dès qu'un e-mail est fourni (case décochée = opt-out explicite)
    send_flag = request.POST.get("send_email")
    should_send = bool(email) and send_flag != "off"
    if should_send:
        delivery = send_account_credentials(user, plain, request=request)
    return user, plain, delivery


def _is_authority_portal(request) -> bool:
    return bool(request and request.path.startswith("/espace/autorite/"))


def _is_insurer_portal(request) -> bool:
    return bool(request and request.path.startswith("/espace/assurance/"))


def _nav_role(user, request=None):
    """Rôle de navigation sidebar — l'admin garde toujours son menu complet."""
    mapping = {
        User.Role.SUPERADMIN: "admin",
        User.Role.ADMIN: "admin",
        User.Role.PHARMACIST: "pharmacy",
        User.Role.COURIER: "courier",
        User.Role.AUTHORITY: "authority",
        User.Role.SUPPORT: "support",
        User.Role.REGIONAL_SUPERVISOR: "regional",
        User.Role.PARTNER: "partner",
        User.Role.CLIENT: "client",
    }
    return mapping.get(user.role, "client")


def _ctx(request, section, **extra):
    user = request.user
    authority_portal = _is_authority_portal(request)
    insurer_portal = _is_insurer_portal(request)
    nav_role = _nav_role(user, request)
    data = {
        "bo_section": section,
        "bo_role": nav_role,
        "bo_authority_portal": authority_portal,
        "bo_insurer_portal": insurer_portal,
        "is_superadmin": user.role == User.Role.SUPERADMIN,
    }
    if authority_portal:
        data["bo_authority_section"] = section
        if nav_role == "admin":
            # Évite de surligner Pharmacies, Notifications, etc. du menu admin.
            data["bo_section"] = ""
    if insurer_portal and nav_role == "partner":
        data["bo_insurer_section"] = section
    if insurer_portal and nav_role == "admin":
        data["bo_insurer_section"] = section
        data["bo_section"] = ""
    if authority_portal or insurer_portal:
        data["bo_nav_active_section"] = section
    elif nav_role in {"authority", "partner"}:
        data["bo_nav_active_section"] = section
    else:
        data.setdefault("bo_nav_active_section", "")
    if user.role == User.Role.PHARMACIST:
        from core.pharmacy_access import employee_for

        ph = _pharmacy_for(user, request)
        data["pharmacy"] = ph
        data["pharmacy_permissions"] = pharmacy_permission_flags(user, ph) if ph else {}
        emp = employee_for(user, ph) if ph else None
        data["pharmacy_membership"] = emp
        data["pharmacy_job_label"] = emp.get_job_role_display() if emp else ""
        data["pharmacy_job_role"] = emp.job_role if emp else ""
        data["notif_unread"] = user.notifications.filter(is_read=False).count()
        if ph:
            from django.db.models import Exists, OuterRef
            from notifications.models import PharmacyConversation, PharmacyMessage

            unread_msg = PharmacyMessage.objects.filter(
                conversation=OuterRef("pk"), is_read=False
            ).exclude(sender=user)
            data["messages_unread"] = (
                PharmacyConversation.objects.filter(pharmacy=ph)
                .annotate(_unread=Exists(unread_msg))
                .filter(_unread=True)
                .count()
            )
    if nav_role == "client":
        from core.views import _profile_lang, _profile_theme

        data["profile_lang"] = _profile_lang(request)
        data["profile_theme"] = _profile_theme(request)
    if nav_role == "admin":
        from core.platform_access import (
            admin_module_flags,
            authority_portal_permissions,
            can_edit_access_config,
            partner_portal_permissions,
        )

        data["admin_modules"] = admin_module_flags(user)
        data["can_edit_access_config"] = can_edit_access_config(user)
        data["authority_admin_nav"] = authority_portal_permissions(user)
        data["insurer_admin_nav"] = partner_portal_permissions(user)
        if authority_portal:
            data["portal_permissions"] = authority_portal_permissions(user)
        if insurer_portal:
            data["portal_permissions"] = partner_portal_permissions(user)
    elif authority_portal and nav_role == "authority":
        from core.platform_access import authority_portal_permissions

        data["portal_permissions"] = authority_portal_permissions(user)
    elif nav_role in {"authority", "partner", "courier", "support", "regional"}:
        from core.platform_access import (
            authority_portal_permissions,
            partner_portal_permissions,
            portal_module_flags,
        )

        if nav_role == "authority":
            data["portal_permissions"] = authority_portal_permissions(user)
        elif nav_role == "partner":
            data["portal_permissions"] = partner_portal_permissions(user)
        else:
            data["portal_permissions"] = portal_module_flags(user)
    if nav_role == "authority":
        data["authority_profile"] = getattr(user, "authority_profile", None)
    if nav_role == "partner":
        data["partner_profile"] = getattr(user, "partner_profile", None)
    data.update(extra)
    return data


def _audit(request, action, module, details="", sensitive=False):
    AuditLog.objects.create(
        user=request.user,
        action=action,
        module=module,
        details=details,
        ip_address=request.META.get("REMOTE_ADDR"),
        is_sensitive=sensitive,
    )


def _set_image(instance, field_name, files):
    """Assigne un fichier uploadé si présent."""
    f = files.get(field_name)
    if f:
        setattr(instance, field_name, f)
        return True
    return False


def _ensure_client_profile(user):
    profile, _ = ClientProfile.objects.get_or_create(user=user)
    return profile


def _ensure_courier_profile(user):
    profile, _ = CourierProfile.objects.get_or_create(user=user)
    return profile


def _parse_decimal(value):
    from decimal import Decimal, InvalidOperation

    value = (value or "").strip()
    if not value:
        return None
    try:
        return Decimal(value.replace(",", "."))
    except (InvalidOperation, TypeError):
        return None


def _courier_ajax_json(request, *, ok: bool, message: str):
  if request.headers.get("X-Requested-With") == "XMLHttpRequest":
      return JsonResponse({"ok": ok, "message": message})
  return None


def _courier_next_status(current):
    flow = {
        Delivery.Status.ASSIGNED: Delivery.Status.PICKING_UP,
        Delivery.Status.PICKING_UP: Delivery.Status.PICKED_UP,
        Delivery.Status.PICKED_UP: Delivery.Status.IN_TRANSIT,
        Delivery.Status.IN_TRANSIT: Delivery.Status.DELIVERED,
    }
    return flow.get(current)


def _sync_order_with_delivery(delivery):
    if delivery.status in {Delivery.Status.DELIVERED, Delivery.Status.FAILED}:
        clear_pending_transfer(delivery, save=True)
    order = delivery.order
    mapping = {
        Delivery.Status.PICKING_UP: Order.Status.READY,
        Delivery.Status.PICKED_UP: Order.Status.DELIVERING,
        Delivery.Status.IN_TRANSIT: Order.Status.DELIVERING,
        Delivery.Status.DELIVERED: Order.Status.DELIVERED,
        Delivery.Status.FAILED: Order.Status.CANCELLED,
    }
    new_status = mapping.get(delivery.status)
    if new_status and order.status != new_status:
        prev = order.status
        order.status = new_status
        order.save(update_fields=["status", "updated_at"])
        if new_status == Order.Status.CANCELLED:
            reason = "Échec de livraison"
            order.cancellation_reason = reason
            order.save(update_fields=["cancellation_reason", "updated_at"])
            _notify_delivery_failed_client(order, reason)
            notify_delivery_outcome(delivery, success=False, reason=reason)
        else:
            _notify_order_status(order, previous_status=prev)
            if new_status == Order.Status.DELIVERED:
                notify_delivery_outcome(delivery, success=True)
                handle_order_delivered(order, delivery)


def _active_ads(placement, limit=3):
    now = timezone.now()
    qs = Advertisement.objects.filter(is_active=True, placement=placement).order_by(
        "-priority", "-created_at"
    )
    result = []
    for ad in qs:
        if ad.starts_at and now < ad.starts_at:
            continue
        if ad.ends_at and now > ad.ends_at:
            continue
        result.append(ad)
        if len(result) >= limit:
            break
    return result


def _username_from_email(email: str) -> str:
    """Génère un username unique à partir de l'e-mail (inscription patient)."""
    local = (email or "").split("@")[0].strip().lower()
    base = slugify(local).replace("-", "_")[:40] or "patient"
    base = "".join(c for c in base if c.isalnum() or c in "._")[:40] or "patient"
    username, i = base, 1
    while User.objects.filter(username=username).exists():
        username = f"{base}{i}"
        i += 1
    return username


def login_view(request):
    if request.user.is_authenticated:
        return redirect(request.user.backoffice_home())
    error = None
    if request.method == "POST":
        clear_pending(request)
        login_id = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        username = login_id
        if "@" in login_id:
            match = User.objects.filter(email__iexact=login_id).first()
            if match:
                username = match.username
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.status == User.Status.SUSPENDED:
                error = "Compte suspendu. Contactez le support Gab'Pharma."
            elif user.role == User.Role.AUTHORITY and user.status == User.Status.PENDING:
                error = (
                    "Compte institutionnel en attente de validation par Gab'Pharma. "
                    "Vous serez notifié par e-mail une fois activé."
                )
            else:
                next_url = request.GET.get("next") or request.POST.get("next") or ""
                needs_2fa = two_factor_enabled() and not is_trusted_device(request, user)
                if needs_2fa:
                    send_result = start_pending_login(request, user, next_url=next_url)
                    if send_result.ok:
                        channel = "e-mail" if send_result.method == "email" else "SMS"
                        request.session[SESSION_FLASH_KEY] = (
                            f"Un code a été envoyé par {channel} "
                            f"à {send_result.destination_masked}."
                        )
                        return redirect("two_factor_verify")
                    error = send_result.error or mail_error_for_user(context="login")
                else:
                    login(request, user)
                    if next_url and url_has_allowed_host_and_scheme(
                        next_url, allowed_hosts={request.get_host()}
                    ):
                        return redirect(next_url)
                    return redirect(user.backoffice_home())
        else:
            error = "E-mail / identifiant ou mot de passe incorrect."
    return render(request, "auth/login.html", {"error": error})


def two_factor_verify_view(request):
    import logging

    logger = logging.getLogger("gabpharma.2fa")

    if request.user.is_authenticated:
        return redirect(request.user.backoffice_home())
    if not get_pending_user(request):
        messages.info(request, "Connectez-vous pour recevoir un code de vérification.")
        return redirect("login")

    error = None
    info = request.session.pop(SESSION_FLASH_KEY, None)

    if request.method == "POST":
        try:
            result = verify_code(request, request.POST.get("code", ""))
            if result.ok:
                login(request, result.user)
                next_url = result.next_url
                if next_url and url_has_allowed_host_and_scheme(
                    next_url, allowed_hosts={request.get_host()}
                ):
                    response = redirect(next_url)
                else:
                    response = redirect(result.user.backoffice_home())
                if request.POST.get("remember_device") == "on":
                    remember_trusted_device(response, result.user)
                return response
            error = result.error
            if result.locked:
                messages.error(request, error)
                return redirect("login")
        except Exception:
            logger.exception("Échec validation 2FA")
            error = (
                "Une erreur est survenue lors de la vérification. "
                "Veuillez réessayer ou vous reconnecter."
            )

    ctx = pending_context(request)
    ctx.update({"error": error, "info": info})
    return render(request, "auth/two_factor_verify.html", ctx)


def two_factor_resend_view(request):
    if request.method != "POST":
        return redirect("two_factor_verify")
    if request.user.is_authenticated:
        return redirect(request.user.backoffice_home())
    if not get_pending_user(request):
        messages.info(request, "Connectez-vous pour recevoir un code de vérification.")
        return redirect("login")

    result = resend_code(request)
    if result.ok:
        channel = "e-mail" if result.method == "email" else "SMS"
        request.session[SESSION_FLASH_KEY] = (
            f"Nouveau code envoyé par {channel} à {result.destination_masked}."
        )
    else:
        request.session[SESSION_FLASH_KEY] = (
            result.error
            or mail_error_for_user(context="login")
        )
    return redirect("two_factor_verify")


def register_view(request):
    """Inscription publique : patients / clients uniquement (sans auto-connexion)."""
    if request.user.is_authenticated:
        return redirect(request.user.backoffice_home())
    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        password = request.POST.get("password", "")
        password2 = request.POST.get("password2", "")
        if not email:
            error = "E-mail obligatoire."
        elif not password:
            error = "Mot de passe obligatoire."
        elif password != password2:
            error = "Les mots de passe ne correspondent pas."
        elif len(password) < 8:
            error = "Mot de passe : 8 caractères minimum."
        elif User.objects.filter(email__iexact=email).exists():
            error = "Cet e-mail est déjà utilisé."
        else:
            username = _username_from_email(email)
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                role=User.Role.CLIENT,
                status=User.Status.ACTIVE,
                city="Libreville",
            )
            ClientProfile.objects.get_or_create(user=user)
            messages.success(
                request,
                "Compte créé avec succès ! Connectez-vous avec votre e-mail et votre mot de passe.",
            )
            return redirect("login")
    return render(request, "auth/register.html", {"error": error})


def authority_register_view(request):
    """Demande de création de compte Autorités Sanitaires (CDC §3.2 — validation admin)."""
    if request.user.is_authenticated:
        return redirect(request.user.backoffice_home())
    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        institution = request.POST.get("institution", "").strip()
        if not request.POST.get("accept_terms"):
            error = "Vous devez accepter les conditions d'utilisation."
        elif not email or not institution:
            error = "E-mail et institution sont obligatoires."
        elif User.objects.filter(email__iexact=email).exists():
            error = "Cet e-mail est déjà utilisé."
        else:
            password = request.POST.get("password", "")
            password2 = request.POST.get("password2", "")
            if len(password) < 8:
                error = "Mot de passe : 8 caractères minimum."
            elif password != password2:
                error = "Les mots de passe ne correspondent pas."
            else:
                username = _username_from_email(email)
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=request.POST.get("first_name", "").strip(),
                    last_name=request.POST.get("last_name", "").strip(),
                    phone=request.POST.get("phone", "").strip(),
                    role=User.Role.AUTHORITY,
                    status=User.Status.PENDING,
                    city=request.POST.get("city", "Libreville").strip(),
                    assigned_region=request.POST.get("region", "").strip(),
                )
                AuthorityProfile.objects.create(
                    user=user,
                    institution=institution,
                    department=request.POST.get("department", "").strip(),
                    job_title=request.POST.get("job_title", "").strip(),
                    region=request.POST.get("region", "").strip(),
                    two_factor_method=request.POST.get("two_factor_method")
                    or AuthorityProfile.TwoFactorMethod.EMAIL,
                )
                messages.success(
                    request,
                    "Demande enregistrée. Votre compte reste « en attente » jusqu'à validation "
                    "de votre rattachement institutionnel par l'équipe Gab'Pharma.",
                )
                return redirect("login")
    return render(request, "auth/authority_register.html", {"error": error})


def _password_strong_enough(password: str) -> bool:
    import re

    return bool(
        len(password) >= 8
        and re.search(r"[A-Z]", password)
        and re.search(r"\d", password)
        and re.search(r"[^A-Za-z0-9]", password)
    )


def insurer_register_view(request):
    """Inscription publique — compagnie d'assurance (validation admin)."""
    from payments.models import InsuranceProvider

    if request.user.is_authenticated:
        return redirect(request.user.backoffice_home())
    error = None
    if request.method == "POST":
        company = request.POST.get("company_name", "").strip()
        acronym = request.POST.get("acronym", "").strip()
        email = request.POST.get("company_email", "").strip() or request.POST.get("email", "").strip()
        if not request.POST.get("accept_terms"):
            error = "Vous devez accepter les conditions d'utilisation."
        elif not company or not email:
            error = "Nom de la compagnie et e-mail professionnel sont obligatoires."
        elif User.objects.filter(email__iexact=email).exists():
            error = "Cet e-mail est déjà utilisé."
        else:
            password = request.POST.get("password", "")
            password2 = request.POST.get("password2", "")
            if not _password_strong_enough(password):
                error = (
                    "Mot de passe : 8 caractères minimum, une majuscule, un chiffre "
                    "et un caractère spécial."
                )
            elif password != password2:
                error = "Les mots de passe ne correspondent pas."
            else:
                username = _username_from_email(email)
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=request.POST.get("rep_first_name", "").strip(),
                    last_name=request.POST.get("rep_last_name", "").strip(),
                    phone=request.POST.get("company_phone", "").strip()
                    or request.POST.get("rep_phone", "").strip(),
                    role=User.Role.PARTNER,
                    status=User.Status.PENDING,
                    city="Libreville",
                )
                code_base = (acronym or company[:12]).upper().replace(" ", "-")
                code = code_base
                seq = 1
                while InsuranceProvider.objects.filter(code=code).exists():
                    code = f"{code_base}-{seq}"
                    seq += 1
                provider = InsuranceProvider.objects.create(
                    name=company,
                    code=code[:20],
                    is_active=False,
                )
                PartnerProfile.objects.create(
                    user=user,
                    partner_type=PartnerProfile.PartnerType.INSURER,
                    organization_name=company,
                    acronym=acronym,
                    registration_number=request.POST.get("registration_number", "").strip(),
                    tax_id=request.POST.get("tax_id", "").strip(),
                    country=request.POST.get("country", "Gabon").strip() or "Gabon",
                    headquarters_address=request.POST.get("headquarters_address", "").strip(),
                    rep_job_title=request.POST.get("rep_job_title", "").strip(),
                    insurance_provider=provider,
                )
                messages.success(
                    request,
                    "Demande enregistrée. Votre compte sera activé après validation par Gab'Pharma.",
                )
                return redirect("login")
    return render(request, "auth/insurer_register.html", {"error": error})


def logout_view(request):
    clear_pending(request)
    logout(request)
    return redirect("home")


@login_required(login_url="login")
def backoffice_entry(request):
    if request.user.role == User.Role.PHARMACIST:
        from core.pharmacy_access import PERM_ORDERS, pharmacy_default_route

        ph = _pharmacy_for(request.user, request)
        flags = pharmacy_permission_flags(request.user, ph) if ph else {}
        if ph and flags.get(PERM_ORDERS) and not flags.get(PERM_STATS):
            return redirect(pharmacy_default_route(request.user, ph))
    return redirect(request.user.backoffice_home())


# ─── Super Admin ───────────────────────────────────────────────────
@role_required(*admin_roles)
def admin_dashboard(request):
    today = timezone.now().date()
    incidents_open = 0
    try:
        incidents_open = DeliveryIncident.objects.filter(
            status=DeliveryIncident.Status.OPEN
        ).count()
    except Exception:
        pass
    stats = {
        "users": User.objects.count(),
        "clients": User.objects.filter(role=User.Role.CLIENT).count(),
        "pharmacies": Pharmacy.objects.count(),
        "pharmacies_active": Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).count(),
        "pharmacies_pending": Pharmacy.objects.filter(status=Pharmacy.Status.PENDING).count(),
        "orders": Order.objects.exclude(status=Order.Status.CART).count(),
        "orders_today": Order.objects.filter(created_at__date=today)
        .exclude(status=Order.Status.CART)
        .count(),
        "revenue": Payment.objects.filter(status=Payment.Status.SUCCESS).aggregate(s=Sum("amount"))["s"]
        or 0,
        "couriers": User.objects.filter(role=User.Role.COURIER).count(),
        "medicines": Medicine.objects.count(),
        "incidents_open": incidents_open,
    }
    order_labels, order_data = [], []
    start = today - timedelta(days=13)
    from django.db.models.functions import TruncDate

    rows = (
        Order.objects.exclude(status=Order.Status.CART)
        .filter(created_at__date__gte=start)
        .annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(c=Count("id"))
        .order_by("d")
    )
    by_day = {r["d"]: r["c"] for r in rows if r["d"]}
    for i in range(14):
        day = start + timedelta(days=i)
        order_labels.append(day.strftime("%d/%m"))
        order_data.append(by_day.get(day, 0))

    role_rows = User.objects.values("role").annotate(c=Count("id")).order_by("-c")
    role_labels = [dict(User.Role.choices).get(r["role"], r["role"]) for r in role_rows]
    role_data = [r["c"] for r in role_rows]

    status_rows = (
        Order.objects.exclude(status=Order.Status.CART)
        .values("status")
        .annotate(c=Count("id"))
        .order_by("-c")
    )
    status_labels = [dict(Order.Status.choices).get(r["status"], r["status"]) for r in status_rows]
    status_data = [r["c"] for r in status_rows]

    charts = {
        "orders": {"labels": order_labels, "data": order_data},
        "roles": {"labels": role_labels, "data": role_data},
        "statuses": {"labels": status_labels, "data": status_data},
    }
    return render(
        request,
        "backoffice/admin/dashboard.html",
        _ctx(
            request,
            "dashboard",
            stats=stats,
            charts_json=json.dumps(charts),
            recent_orders=Order.objects.exclude(status=Order.Status.CART)
            .select_related("client", "pharmacy")
            .order_by("-created_at")[:6],
            pending_pharmacies=Pharmacy.objects.filter(status=Pharmacy.Status.PENDING)[:5],
        ),
    )


@role_required(*admin_roles)
def admin_users(request):
    from accounts.user_admin import (
        apply_user_status_change,
        export_users_csv,
        import_users_from_csv,
        send_user_invitation,
        user_verification_label,
        user_verification_state,
    )

    qs = User.objects.all().order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    status = request.GET.get("status", "")
    verification = request.GET.get("verification", "")
    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(phone__icontains=q)
        )
    if role:
        qs = qs.filter(role=role)
    if status:
        qs = qs.filter(status=status)
    if verification == "verified":
        qs = qs.filter(is_email_verified=True, is_phone_verified=True)
    elif verification == "partial":
        qs = qs.filter(Q(is_email_verified=True) | Q(is_phone_verified=True)).exclude(
            is_email_verified=True, is_phone_verified=True
        )
    elif verification == "unverified":
        qs = qs.filter(is_email_verified=False, is_phone_verified=False)

    if request.GET.get("export") == "csv":
        return export_users_csv(qs)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "import_csv":
            f = request.FILES.get("csv_file")
            if not f:
                messages.error(request, "Sélectionnez un fichier CSV.")
            else:
                created, errors = import_users_from_csv(f, request=request)
                if created:
                    messages.success(request, f"{created} compte(s) importé(s).")
                    _audit(request, "import_users", "users", f"{created} importés", True)
                for err in errors[:8]:
                    messages.warning(request, err)
                if len(errors) > 8:
                    messages.warning(request, f"… et {len(errors) - 8} autre(s) avertissement(s).")
        elif action == "create":
            username = request.POST.get("username", "").strip()
            email = request.POST.get("email", "").strip()
            role = request.POST.get("role", User.Role.PHARMACIST)
            allowed = {r[0] for r in STAFF_CREATE_ROLES} | {User.Role.CLIENT}
            if role not in allowed:
                role = User.Role.PHARMACIST
            if not username:
                messages.error(request, "Identifiant obligatoire.")
            elif User.objects.filter(username=username).exists():
                messages.error(request, "Identifiant déjà utilisé.")
            elif not email:
                messages.error(request, "E-mail obligatoire pour envoyer les identifiants.")
            else:
                user, plain, delivery = _create_staff_user(
                    request,
                    role=role,
                    username=username,
                    email=email,
                    first_name=request.POST.get("first_name", ""),
                    last_name=request.POST.get("last_name", ""),
                    phone=request.POST.get("phone", ""),
                    city=request.POST.get("city", "Libreville"),
                    password=request.POST.get("password", ""),
                )
                if _set_image(user, "avatar", request.FILES):
                    user.save(update_fields=["avatar"])
                if role == User.Role.REGIONAL_SUPERVISOR:
                    user.assigned_region = request.POST.get("assigned_region", "").strip()
                    user.save(update_fields=["assigned_region"])
                if role == User.Role.PARTNER:
                    provider_id = request.POST.get("insurance_provider") or None
                    profile = PartnerProfile.objects.create(
                        user=user,
                        partner_type=request.POST.get(
                            "partner_type", PartnerProfile.PartnerType.INSURER
                        ),
                        organization_name=request.POST.get(
                            "organization_name", user.get_full_name() or user.username
                        ),
                        acronym=request.POST.get("acronym", ""),
                        registration_number=request.POST.get("registration_number", ""),
                        tax_id=request.POST.get("tax_id", ""),
                        country=request.POST.get("country", "Gabon"),
                        headquarters_address=request.POST.get("headquarters_address", ""),
                        rep_job_title=request.POST.get("job_title", ""),
                        insurance_provider_id=provider_id,
                    )
                    if user.status == User.Status.ACTIVE:
                        profile.validated_at = timezone.now()
                        profile.validated_by = request.user
                        profile.save(update_fields=["validated_at", "validated_by"])
                        if profile.insurance_provider_id:
                            profile.insurance_provider.is_active = True
                            profile.insurance_provider.save(update_fields=["is_active"])
                elif role == User.Role.AUTHORITY:
                    AuthorityProfile.objects.get_or_create(
                        user=user,
                        defaults={
                            "institution": request.POST.get("institution", "Institution"),
                            "department": request.POST.get("department", ""),
                            "region": request.POST.get("assigned_region", user.city),
                            "two_factor_method": request.POST.get("two_factor_method")
                            or AuthorityProfile.TwoFactorMethod.EMAIL,
                        },
                    )
                _audit(request, "create_user", "users", f"Création {user.username} ({role})", True)
                msg = f"Compte {user.username} créé."
                if delivery:
                    notice = delivery.notice(plain_password=plain)
                    if notice:
                        msg += f" {notice}"
                elif plain:
                    msg += f" Mot de passe temporaire : {plain}"
                messages.success(request, msg)
        elif action == "invite":
            email = request.POST.get("email", "").strip()
            invite_role = request.POST.get("role", User.Role.CLIENT)
            username = request.POST.get("username", "").strip() or email.split("@")[0][:30]
            if not email:
                messages.error(request, "E-mail obligatoire pour l'invitation.")
            elif User.objects.filter(username=username).exists():
                messages.error(request, "Identifiant déjà utilisé.")
            else:
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=generate_temp_password(),
                    role=invite_role,
                    status=User.Status.PENDING,
                    first_name=request.POST.get("first_name", ""),
                    last_name=request.POST.get("last_name", ""),
                )
                ok, err = send_user_invitation(user, request=request)
                if ok:
                    messages.success(request, f"Invitation envoyée à {email}.")
                    _audit(request, "invite_user", "users", email, True)
                else:
                    messages.error(request, f"Échec invitation : {err}")
        elif action == "suspend":
            u = get_object_or_404(User, pk=request.POST.get("user_id"))
            if u == request.user or u.role == User.Role.SUPERADMIN:
                messages.error(request, "Impossible de suspendre ce compte.")
            else:
                prev = u.status
                u.status = User.Status.SUSPENDED
                u.save(update_fields=["status"])
                apply_user_status_change(u, User.Status.SUSPENDED, previous_status=prev)
                _audit(request, "suspend_user", "users", f"Suspension {u.username}", True)
                messages.success(request, f"Compte {u.username} suspendu.")
        elif action == "reactivate":
            u = get_object_or_404(User, pk=request.POST.get("user_id"))
            prev = u.status
            u.status = User.Status.ACTIVE
            u.save(update_fields=["status"])
            apply_user_status_change(u, User.Status.ACTIVE, previous_status=prev)
            _audit(request, "reactivate_user", "users", f"Réactivation {u.username}", True)
            messages.success(request, f"Compte {u.username} réactivé.")
        elif action == "validate_authority":
            u = get_object_or_404(User, pk=request.POST.get("user_id"), role=User.Role.AUTHORITY)
            profile, _ = AuthorityProfile.objects.get_or_create(
                user=u,
                defaults={"institution": "Institution"},
            )
            profile.validated_at = timezone.now()
            profile.validated_by = request.user
            profile.save(update_fields=["validated_at", "validated_by"])
            u.status = User.Status.ACTIVE
            u.save(update_fields=["status"])
            _audit(request, "validate_authority", "users", u.username, True)
            messages.success(request, f"Compte autorité {u.username} validé.")
        elif action == "resend_credentials":
            u = get_object_or_404(User, pk=request.POST.get("user_id"))
            if not u.email:
                messages.error(request, "Cet utilisateur n’a pas d’e-mail.")
            else:
                plain = generate_temp_password()
                u.set_password(plain)
                u.save(update_fields=["password"])
                delivery = send_account_credentials(u, plain, request=request)
                _audit(request, "reset_password", "users", f"Réinit {u.username}", True)
                if delivery.ok:
                    messages.success(
                        request,
                        f"Nouveaux identifiants pour {u.username}. {delivery.notice(plain_password=plain)}",
                    )
                else:
                    messages.warning(
                        request,
                        f"{delivery.notice(plain_password=plain)}",
                    )
        elif action == "update":
            u = get_object_or_404(User, pk=request.POST.get("user_id"))
            new_role = request.POST.get("role", u.role)
            if new_role == User.Role.SUPERADMIN and request.user.role != User.Role.SUPERADMIN:
                messages.error(request, "Seul un super administrateur peut attribuer ce rôle.")
                return redirect("bo_admin_users")
            if u.role != User.Role.SUPERADMIN or request.user.role == User.Role.SUPERADMIN:
                prev_status = u.status
                u.role = new_role
                u.status = request.POST.get("status", u.status)
                u.first_name = request.POST.get("first_name", u.first_name)
                u.last_name = request.POST.get("last_name", u.last_name)
                u.email = request.POST.get("email", u.email)
                u.phone = request.POST.get("phone", u.phone)
                u.city = request.POST.get("city", u.city)
                u.is_email_verified = request.POST.get("is_email_verified") == "on"
                u.is_phone_verified = request.POST.get("is_phone_verified") == "on"
                if u.role == User.Role.REGIONAL_SUPERVISOR or new_role == User.Role.REGIONAL_SUPERVISOR:
                    u.assigned_region = request.POST.get("assigned_region", u.assigned_region).strip()
                _set_image(u, "avatar", request.FILES)
                u.save()
                apply_user_status_change(u, u.status, previous_status=prev_status)
                if u.role == User.Role.PARTNER:
                    profile, _ = PartnerProfile.objects.get_or_create(user=u)
                    profile.partner_type = request.POST.get("partner_type", profile.partner_type)
                    profile.organization_name = request.POST.get(
                        "organization_name", profile.organization_name
                    )
                    provider_id = request.POST.get("insurance_provider")
                    if provider_id:
                        profile.insurance_provider_id = provider_id
                    profile.save()
                _audit(request, "update_user", "users", f"MAJ {u.username}", True)
                messages.success(request, "Utilisateur mis à jour.")
        elif action == "delete":
            u = get_object_or_404(User, pk=request.POST.get("user_id"))
            if u != request.user and u.role != User.Role.SUPERADMIN:
                name = u.username
                u.delete()
                _audit(request, "delete_user", "users", f"Suppression {name}", True)
                messages.success(request, "Utilisateur supprimé.")
            else:
                messages.error(request, "Impossible de supprimer ce compte.")
        return redirect("bo_admin_users")

    role_distribution = []
    for row in User.objects.values("role").annotate(count=Count("id")).order_by("-count"):
        role_distribution.append(
            {
                "role": row["role"],
                "label": dict(User.Role.choices).get(row["role"], row["role"]),
                "count": row["count"],
            }
        )
    pending_authorities = User.objects.filter(
        role=User.Role.AUTHORITY, status=User.Status.PENDING
    ).select_related("authority_profile")[:10]

    edit_obj = None
    view_obj = None
    if request.GET.get("edit"):
        edit_obj = User.objects.filter(pk=request.GET.get("edit")).first()
    if request.GET.get("view"):
        view_obj = User.objects.filter(pk=request.GET.get("view")).select_related("authority_profile").first()
    client_orders = []
    if view_obj and view_obj.role == User.Role.CLIENT:
        client_orders = (
            Order.objects.filter(client=view_obj)
            .exclude(status=Order.Status.CART)
            .select_related("pharmacy")
            .order_by("-created_at")[:15]
        )

    page = paginate(request, qs, 20)
    for u in page.object_list:
        u.verification_state = user_verification_state(u)
        u.verification_label = user_verification_label(u)

    return render(
        request,
        "backoffice/admin/users.html",
        _ctx(
            request,
            "users",
            page_obj=page,
            total_count=qs.count(),
            q=q,
            filter_role=role,
            filter_status=status,
            filter_verification=verification,
            edit_obj=edit_obj,
            view_obj=view_obj,
            client_orders=client_orders,
            roles=User.Role.choices,
            create_roles=STAFF_CREATE_ROLES,
            statuses=User.Status.choices,
            show_create=request.GET.get("new") == "1",
            show_invite=request.GET.get("invite") == "1",
            show_import=request.GET.get("import") == "1",
            role_distribution=role_distribution,
            pending_authorities=pending_authorities,
        ),
    )


@role_required(*admin_roles)
def admin_authorities(request):
    from core.authority_admin import (
        authority_kpis,
        charts_json,
        export_authorities_csv,
        filter_authorities,
        recent_authority_activities,
    )
    from backoffice.utils import paginate

    qs, period_start, period_end = filter_authorities(request)

    if request.GET.get("export") == "csv":
        return export_authorities_csv(qs)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            institution = request.POST.get("institution", "").strip()
            email = request.POST.get("email", "").strip()
            username = request.POST.get("username", "").strip() or _username_from_email(email)
            if not institution or not email:
                messages.error(request, "Institution et e-mail sont obligatoires.")
            elif User.objects.filter(username=username).exists():
                messages.error(request, "Identifiant déjà utilisé.")
            elif User.objects.filter(email__iexact=email).exists():
                messages.error(request, "E-mail déjà utilisé.")
            else:
                user, plain, delivery = _create_staff_user(
                    request,
                    role=User.Role.AUTHORITY,
                    username=username,
                    email=email,
                    first_name=request.POST.get("first_name", ""),
                    last_name=request.POST.get("last_name", ""),
                    phone=request.POST.get("phone", ""),
                    city=request.POST.get("region", "Libreville"),
                    password=request.POST.get("password", ""),
                )
                access_level = request.POST.get(
                    "access_level", AuthorityProfile.AccessLevel.NATIONAL_ADMIN
                )
                status = request.POST.get("status", User.Status.PENDING)
                user.status = status
                user.save(update_fields=["status"])
                profile = AuthorityProfile.objects.create(
                    user=user,
                    institution=institution,
                    department=request.POST.get("department", ""),
                    job_title=request.POST.get("job_title", ""),
                    region=request.POST.get("region", ""),
                    access_level=access_level,
                    two_factor_method=request.POST.get("two_factor_method")
                    or AuthorityProfile.TwoFactorMethod.EMAIL,
                )
                if status == User.Status.ACTIVE:
                    profile.validated_at = timezone.now()
                    profile.validated_by = request.user
                    profile.save(update_fields=["validated_at", "validated_by"])
                _audit(request, "create_authority", "authorities", profile.display_code, True)
                msg = f"Autorité {profile.display_code} créée."
                if delivery:
                    notice = delivery.notice(plain_password=plain)
                    if notice:
                        msg += f" {notice}"
                messages.success(request, msg)
        elif action == "update":
            u = get_object_or_404(User, pk=request.POST.get("user_id"), role=User.Role.AUTHORITY)
            profile, _ = AuthorityProfile.objects.get_or_create(
                user=u, defaults={"institution": "Institution"}
            )
            u.first_name = request.POST.get("first_name", u.first_name)
            u.last_name = request.POST.get("last_name", u.last_name)
            u.email = request.POST.get("email", u.email)
            u.phone = request.POST.get("phone", u.phone)
            u.status = request.POST.get("status", u.status)
            u.save()
            profile.institution = request.POST.get("institution", profile.institution)
            profile.department = request.POST.get("department", profile.department)
            profile.job_title = request.POST.get("job_title", profile.job_title)
            profile.region = request.POST.get("region", profile.region)
            profile.access_level = request.POST.get("access_level", profile.access_level)
            profile.two_factor_method = request.POST.get(
                "two_factor_method", profile.two_factor_method
            )
            profile.save()
            _audit(request, "update_authority", "authorities", profile.display_code, True)
            messages.success(request, "Autorité mise à jour.")
        elif action == "validate":
            u = get_object_or_404(User, pk=request.POST.get("user_id"), role=User.Role.AUTHORITY)
            profile, _ = AuthorityProfile.objects.get_or_create(
                user=u, defaults={"institution": "Institution"}
            )
            profile.validated_at = timezone.now()
            profile.validated_by = request.user
            profile.save(update_fields=["validated_at", "validated_by"])
            u.status = User.Status.ACTIVE
            u.save(update_fields=["status"])
            _audit(request, "validate_authority", "authorities", profile.display_code, True)
            messages.success(request, f"Autorité {profile.institution} validée.")
        elif action == "suspend":
            u = get_object_or_404(User, pk=request.POST.get("user_id"), role=User.Role.AUTHORITY)
            if u != request.user:
                u.status = User.Status.SUSPENDED
                u.save(update_fields=["status"])
                code = getattr(u.authority_profile, "display_code", u.username)
                _audit(request, "suspend_authority", "authorities", code, True)
                messages.success(request, f"Autorité {u.get_full_name() or u.username} suspendue.")
        elif action == "reactivate":
            u = get_object_or_404(User, pk=request.POST.get("user_id"), role=User.Role.AUTHORITY)
            u.status = User.Status.ACTIVE
            u.save(update_fields=["status"])
            code = getattr(u.authority_profile, "display_code", u.username)
            _audit(request, "reactivate_authority", "authorities", code, True)
            messages.success(request, "Autorité réactivée.")
        return redirect("bo_admin_authorities")

    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    region = request.GET.get("region", "")
    access_level = request.GET.get("access_level", "")

    edit_obj = None
    view_obj = None
    if request.GET.get("edit"):
        edit_obj = (
            User.objects.filter(pk=request.GET.get("edit"), role=User.Role.AUTHORITY)
            .select_related("authority_profile")
            .first()
        )
    if request.GET.get("view"):
        view_obj = (
            User.objects.filter(pk=request.GET.get("view"), role=User.Role.AUTHORITY)
            .select_related("authority_profile", "authority_profile__validated_by")
            .first()
        )

    page = paginate(request, qs, 8)
    kpis = authority_kpis(request)
    from core.gabon_regions import GABON_PROVINCES

    region_choices = [("national", "National")] + [
        (p["name"], p["name"]) for p in GABON_PROVINCES
    ]

    return render(
        request,
        "backoffice/admin/authorities.html",
        _ctx(
            request,
            "authorities",
            page_obj=page,
            total_count=qs.count(),
            q=q,
            filter_status=status,
            filter_region=region,
            filter_access_level=access_level,
            period_start=period_start.strftime("%d/%m/%Y"),
            period_end=period_end.strftime("%d/%m/%Y"),
            kpis=kpis,
            charts_json=charts_json(),
            recent_activities=recent_authority_activities(),
            statuses=User.Status.choices,
            access_levels=AuthorityProfile.AccessLevel.choices,
            two_factor_methods=AuthorityProfile.TwoFactorMethod.choices,
            region_choices=region_choices,
            show_create=request.GET.get("new") == "1",
            edit_obj=edit_obj,
            view_obj=view_obj,
        ),
    )


@role_required(*admin_roles)
def admin_pharmacies(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = request.POST.get("name", "").strip()
            code = request.POST.get("code", "").strip() or f"PH-{Pharmacy.objects.count()+1:03d}"
            if name:
                p = Pharmacy.objects.create(
                    code=code,
                    name=name,
                    slug=slugify(name)[:50] or code.lower(),
                    phone=request.POST.get("phone", ""),
                    address=request.POST.get("address", ""),
                    city=request.POST.get("city", "Libreville"),
                    district=request.POST.get("district", ""),
                    region=request.POST.get("region", "Estuaire"),
                    status=request.POST.get("status", Pharmacy.Status.PENDING),
                    is_24h=bool(request.POST.get("is_24h")),
                    is_on_duty=bool(request.POST.get("is_on_duty")),
                )
                if _set_image(p, "logo", request.FILES):
                    p.save(update_fields=["logo"])
                _audit(request, "create_pharmacy", "pharmacies", name)
                messages.success(request, "Pharmacie créée.")
        elif action == "update":
            p = get_object_or_404(Pharmacy, pk=request.POST.get("pharmacy_id"))
            p.name = request.POST.get("name", p.name)
            p.phone = request.POST.get("phone", p.phone)
            p.address = request.POST.get("address", p.address)
            p.city = request.POST.get("city", p.city)
            p.district = request.POST.get("district", p.district)
            p.region = request.POST.get("region", p.region)
            p.status = request.POST.get("status", p.status)
            p.compliance_score = int(request.POST.get("compliance_score") or p.compliance_score)
            p.is_24h = bool(request.POST.get("is_24h"))
            p.is_on_duty = bool(request.POST.get("is_on_duty"))
            _set_image(p, "logo", request.FILES)
            p.save()
            _audit(request, "update_pharmacy", "pharmacies", p.name)
            messages.success(request, "Pharmacie mise à jour.")
        elif action == "delete":
            p = get_object_or_404(Pharmacy, pk=request.POST.get("pharmacy_id"))
            name = p.name
            p.delete()
            _audit(request, "delete_pharmacy", "pharmacies", name, True)
            messages.success(request, "Pharmacie supprimée.")
        return redirect("bo_admin_pharmacies")

    qs = Pharmacy.objects.all().order_by("-created_at")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(code__icontains=q) | Q(city__icontains=q) | Q(district__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    edit_obj = Pharmacy.objects.filter(pk=request.GET.get("edit")).first() if request.GET.get("edit") else None

    return render(
        request,
        "backoffice/admin/pharmacies.html",
        _ctx(
            request,
            "pharmacies",
            page_obj=paginate(request, qs, 20),
            total_count=qs.count(),
            q=q,
            filter_status=status,
            edit_obj=edit_obj,
            statuses=Pharmacy.Status.choices,
            show_create=request.GET.get("new") == "1",
        ),
    )


@role_required(*admin_roles)
def admin_medicines(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = request.POST.get("name", "").strip()
            dosage = request.POST.get("dosage", "").strip()
            if name:
                base = slugify(f"{name}-{dosage}") or "med"
                slug = base
                i = 1
                while Medicine.objects.filter(slug=slug).exists():
                    slug = f"{base}-{i}"
                    i += 1
                cat_id = request.POST.get("category") or None
                m = Medicine.objects.create(
                    name=name,
                    slug=slug,
                    dosage=dosage,
                    dci=request.POST.get("dci", ""),
                    laboratory=request.POST.get("laboratory", ""),
                    form=request.POST.get("form", Medicine.Form.TABLET),
                    category_id=cat_id or None,
                    requires_prescription=bool(request.POST.get("requires_prescription")),
                    is_featured=bool(request.POST.get("is_featured")),
                    description=request.POST.get("description", "").strip(),
                    presentation=request.POST.get("presentation", "").strip(),
                    composition=request.POST.get("composition", "").strip(),
                    usage_advice=request.POST.get("usage_advice", "").strip(),
                    pharmacist_advice=request.POST.get("pharmacist_advice", "").strip(),
                    recommended_by=request.POST.get("recommended_by", "").strip(),
                )
                if _set_image(m, "image", request.FILES):
                    m.save(update_fields=["image"])
                messages.success(request, "Médicament ajouté.")
        elif action == "update":
            m = get_object_or_404(Medicine, pk=request.POST.get("medicine_id"))
            m.name = request.POST.get("name", m.name)
            m.dosage = request.POST.get("dosage", m.dosage)
            m.dci = request.POST.get("dci", m.dci)
            m.laboratory = request.POST.get("laboratory", m.laboratory)
            m.form = request.POST.get("form", m.form)
            cat = request.POST.get("category")
            m.category_id = cat or None
            m.requires_prescription = bool(request.POST.get("requires_prescription"))
            m.is_featured = bool(request.POST.get("is_featured"))
            m.description = request.POST.get("description", m.description).strip()
            m.presentation = request.POST.get("presentation", m.presentation).strip()
            m.composition = request.POST.get("composition", m.composition).strip()
            m.usage_advice = request.POST.get("usage_advice", m.usage_advice).strip()
            m.pharmacist_advice = request.POST.get("pharmacist_advice", m.pharmacist_advice).strip()
            m.recommended_by = request.POST.get("recommended_by", m.recommended_by).strip()
            _set_image(m, "image", request.FILES)
            m.save()
            messages.success(request, "Médicament mis à jour.")
        elif action == "delete":
            m = get_object_or_404(Medicine, pk=request.POST.get("medicine_id"))
            m.delete()
            messages.success(request, "Médicament supprimé.")
        return redirect("bo_admin_medicines")

    qs = Medicine.objects.select_related("category").order_by("name")
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(dci__icontains=q) | Q(laboratory__icontains=q) | Q(dosage__icontains=q)
        )
    if category:
        qs = qs.filter(category_id=category)
    edit_obj = Medicine.objects.filter(pk=request.GET.get("edit")).first() if request.GET.get("edit") else None

    return render(
        request,
        "backoffice/admin/medicines.html",
        _ctx(
            request,
            "medicines",
            page_obj=paginate(request, qs, 20),
            total_count=qs.count(),
            q=q,
            filter_category=category,
            edit_obj=edit_obj,
            categories=Category.objects.filter(is_active=True),
            forms=Medicine.Form.choices,
            show_create=request.GET.get("new") == "1",
        ),
    )


@role_required(*admin_roles)
def admin_orders(request):
    if request.method == "POST":
        o = get_object_or_404(Order, pk=request.POST.get("order_id"))
        new_status = request.POST.get("status")
        if new_status in dict(Order.Status.choices):
            o.status = new_status
            o.save(update_fields=["status", "updated_at"])
            messages.success(request, f"Commande {o.code} → {o.get_status_display()}")
        return redirect("bo_admin_orders")

    qs = (
        Order.objects.exclude(status=Order.Status.CART)
        .select_related("client", "pharmacy")
        .order_by("-created_at")
    )
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(
            Q(code__icontains=q)
            | Q(client__username__icontains=q)
            | Q(client__first_name__icontains=q)
            | Q(pharmacy__name__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)

    return render(
        request,
        "backoffice/admin/orders.html",
        _ctx(
            request,
            "orders",
            page_obj=paginate(request, qs, 20),
            total_count=qs.count(),
            q=q,
            filter_status=status,
            statuses=Order.Status.choices,
        ),
    )


def _save_courier_admin_profile(user, post, files):
    """Applique profil livreur complet (admin création / édition)."""
    from core.courier_portal import (
        apply_courier_admin_meta,
        apply_courier_documents_upload,
        apply_courier_vehicle_post,
        courier_must_complete_documents,
    )

    profile = _ensure_courier_profile(user)
    apply_courier_vehicle_post(profile, post)
    apply_courier_admin_meta(profile, post)
    apply_courier_documents_upload(profile, files)
    if courier_must_complete_documents(profile):
        profile.courier_status = CourierProfile.CourierStatus.OFFLINE
    elif (
        profile.courier_status == CourierProfile.CourierStatus.ONLINE
        and not profile.eligibility_approved
    ):
        profile.courier_status = CourierProfile.CourierStatus.OFFLINE
    profile.save()
    return profile


@role_required(*admin_roles)
def admin_couriers(request):
    from core.courier_portal import (
        courier_admin_document_cards,
        courier_eligibility,
        courier_vehicle_display,
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            username = request.POST.get("username", "").strip()
            email = request.POST.get("email", "").strip()
            if not username:
                messages.error(request, "Identifiant obligatoire.")
            elif User.objects.filter(username=username).exists():
                messages.error(request, "Identifiant déjà utilisé.")
            elif not email:
                messages.error(request, "E-mail obligatoire pour envoyer les identifiants.")
            else:
                user, plain, delivery = _create_staff_user(
                    request,
                    role=User.Role.COURIER,
                    username=username,
                    email=email,
                    first_name=request.POST.get("first_name", ""),
                    last_name=request.POST.get("last_name", ""),
                    phone=request.POST.get("phone", ""),
                    city=request.POST.get("city", "Libreville"),
                    password=request.POST.get("password", ""),
                )
                user.district = request.POST.get("district", "").strip()
                user.latitude = _parse_decimal(request.POST.get("latitude"))
                user.longitude = _parse_decimal(request.POST.get("longitude"))
                user.status = request.POST.get("status", user.status)
                _set_image(user, "avatar", request.FILES)
                user.save()
                _save_courier_admin_profile(user, request.POST, request.FILES)
                msg = f"Livreur créé — identifiant {user.username}."
                if delivery:
                    notice = delivery.notice(plain_password=plain)
                    if notice:
                        msg += f" {notice}"
                elif plain:
                    msg += f" Mot de passe temporaire : {plain}"
                messages.success(request, msg)
                messages.info(
                    request,
                    "Le livreur devra compléter ses documents réglementaires pour être éligible aux missions.",
                )
                return redirect(f"{reverse('bo_admin_couriers')}?edit={user.pk}")
        elif action == "update":
            u = get_object_or_404(User, pk=request.POST.get("user_id"), role=User.Role.COURIER)
            u.status = request.POST.get("status", u.status)
            u.phone = request.POST.get("phone", u.phone)
            u.first_name = request.POST.get("first_name", u.first_name)
            u.last_name = request.POST.get("last_name", u.last_name)
            u.email = request.POST.get("email", u.email)
            u.city = request.POST.get("city", u.city)
            u.district = request.POST.get("district", u.district)
            u.latitude = _parse_decimal(request.POST.get("latitude"))
            u.longitude = _parse_decimal(request.POST.get("longitude"))
            _set_image(u, "avatar", request.FILES)
            u.save()
            _save_courier_admin_profile(u, request.POST, request.FILES)
            messages.success(request, "Livreur mis à jour.")
            tab = request.POST.get("active_tab", "").strip()
            url = reverse("bo_admin_couriers") + f"?edit={u.pk}"
            if tab:
                url += f"&tab={tab}"
            return redirect(url)
        elif action == "approve_eligibility":
            u = get_object_or_404(User, pk=request.POST.get("user_id"), role=User.Role.COURIER)
            profile = _ensure_courier_profile(u)
            from core.courier_portal import approve_courier_eligibility

            try:
                approve_courier_eligibility(profile, request.user)
                messages.success(request, f"{u.get_full_name() or u.username} est maintenant éligible aux missions.")
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect(reverse("bo_admin_couriers") + f"?edit={u.pk}&tab=documents")
        elif action == "revoke_eligibility":
            u = get_object_or_404(User, pk=request.POST.get("user_id"), role=User.Role.COURIER)
            profile = _ensure_courier_profile(u)
            from core.courier_portal import revoke_courier_eligibility

            revoke_courier_eligibility(profile)
            messages.success(request, f"Éligibilité retirée pour {u.get_full_name() or u.username}.")
            return redirect(reverse("bo_admin_couriers") + f"?edit={u.pk}&tab=documents")
        elif action == "resend_credentials":
            u = get_object_or_404(User, pk=request.POST.get("user_id"), role=User.Role.COURIER)
            if not u.email:
                messages.error(request, "Ajoutez un e-mail au livreur avant de renvoyer les accès.")
            else:
                plain = generate_temp_password()
                u.set_password(plain)
                u.save(update_fields=["password"])
                try:
                    send_account_credentials(u, plain, request=request)
                    messages.success(request, f"Identifiants renvoyés à {u.email}.")
                    messages.info(
                        request,
                        f"Identifiant : {u.username} · Mot de passe temporaire : {plain} "
                        f"(aussi affiché dans la console du serveur).",
                    )
                except Exception as exc:  # noqa: BLE001
                    messages.warning(
                        request,
                        f"E-mail en échec ({exc}). Identifiant : {u.username} · MDP : {plain}",
                    )
        return redirect("bo_admin_couriers")

    qs = (
        User.objects.filter(role=User.Role.COURIER)
        .select_related(
            "courier_profile",
            "courier_profile__pharmacy",
            "courier_profile__eligibility_approved_by",
        )
        .order_by("-date_joined")
    )
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(phone__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    edit_obj = qs.filter(pk=request.GET.get("edit")).first() if request.GET.get("edit") else None
    edit_profile = _ensure_courier_profile(edit_obj) if edit_obj else None
    blank_user = User(role=User.Role.COURIER, status=User.Status.ACTIVE, city="Libreville")
    blank_profile = CourierProfile(courier_status=CourierProfile.CourierStatus.OFFLINE)

    return render(
        request,
        "backoffice/admin/couriers.html",
        _ctx(
            request,
            "couriers",
            page_obj=paginate(request, qs, 20),
            total_count=qs.count(),
            q=q,
            filter_status=status,
            edit_obj=edit_obj,
            edit_profile=edit_profile,
            edit_vehicle=courier_vehicle_display(edit_profile) if edit_profile else courier_vehicle_display(blank_profile),
            edit_documents=courier_admin_document_cards(edit_profile),
            blank_user=blank_user,
            blank_profile=blank_profile,
            blank_vehicle=courier_vehicle_display(blank_profile),
            create_documents=courier_admin_document_cards(None),
            blank_eligibility=courier_eligibility(None),
            edit_eligibility=courier_eligibility(edit_profile) if edit_profile else courier_eligibility(None),
            pharmacies=Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).order_by("name"),
            courier_statuses=CourierProfile.CourierStatus.choices,
            courier_levels=CourierProfile.Level.choices,
            statuses=User.Status.choices,
            show_create=request.GET.get("new") == "1",
            active_tab=request.GET.get("tab", "account"),
        ),
    )


@role_required(*admin_roles)
def admin_payments(request):
    from notifications.models import AuditLog

    settings_obj = PlatformPaymentSettings.load()

    if request.method == "POST" and request.POST.get("action") == "save_settings":
        settings_obj.platform_commission_rate = request.POST.get("platform_commission_rate") or 5
        settings_obj.payout_delay_days = max(0, int(request.POST.get("payout_delay_days") or 2))
        settings_obj.daily_transaction_cap = max(
            1000, int(request.POST.get("daily_transaction_cap") or 500_000)
        )
        settings_obj.cod_deposit_rate = max(1, min(100, int(request.POST.get("cod_deposit_rate") or 20)))
        settings_obj.cod_deposit_min = max(0, int(request.POST.get("cod_deposit_min") or 500))
        settings_obj.courier_base_fee = max(0, int(request.POST.get("courier_base_fee") or 1500))
        settings_obj.courier_per_km_fee = max(0, int(request.POST.get("courier_per_km_fee") or 200))
        settings_obj.courier_express_bonus = max(0, int(request.POST.get("courier_express_bonus") or 500))
        settings_obj.save()
        AuditLog.objects.create(
            user=request.user,
            action="Mise à jour paramètres paiement",
            module="payments",
            details=(
                f"Commission {settings_obj.platform_commission_rate}% · "
                f"Plafond {settings_obj.daily_transaction_cap} F · "
                f"Versement J+{settings_obj.payout_delay_days}"
            ),
            is_sensitive=True,
        )
        messages.success(request, "Paramètres financiers enregistrés et journalisés.")
        return redirect("bo_admin_payments")

    qs = Payment.objects.select_related("order", "order__settlement").order_by("-created_at")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(order__code__icontains=q))
    if status:
        qs = qs.filter(status=status)
    return render(
        request,
        "backoffice/admin/payments.html",
        _ctx(
            request,
            "payments",
            page_obj=paginate(request, qs, 20),
            total_count=qs.count(),
            q=q,
            filter_status=status,
            statuses=Payment.Status.choices,
            total=Payment.objects.filter(status=Payment.Status.SUCCESS).aggregate(s=Sum("amount"))["s"]
            or 0,
            settings_obj=settings_obj,
            commission_total=OrderSettlement.objects.aggregate(s=Sum("platform_commission"))["s"] or 0,
        ),
    )


@role_required(*support_roles)
def admin_incidents(request):
    from core.delivery_transfer import admin_prepare_handoff, escalate_stale_transfers
    from core.incident_admin import (
        incident_resolution_stats,
        nearby_couriers_for_incident,
    )

    escalate_stale_transfers()

    if request.method == "POST":
        action = request.POST.get("action") or "update"
        inc = get_object_or_404(
            DeliveryIncident.objects.select_related(
                "delivery",
                "delivery__order",
                "delivery__order__client",
                "delivery__order__pharmacy",
                "delivery__courier",
            ),
            pk=request.POST.get("incident_id"),
        )
        if action == "reassign":
            courier = get_object_or_404(
                User, pk=request.POST.get("courier_id"), role=User.Role.COURIER
            )
            delivery = inc.delivery
            admin_prepare_handoff(delivery, courier, incident=inc)
            inc.status = DeliveryIncident.Status.IN_PROGRESS
            inc.save(update_fields=["status"])
            messages.success(
                request,
                f"Transfert initié vers {courier.get_full_name() or courier.username}. "
                f"Le remplaçant devra saisir le code sur place — le client sera notifié.",
            )
        elif action == "cancel_order":
            order = inc.delivery.order
            order.status = Order.Status.CANCELLED
            order.cancellation_reason = request.POST.get("reason", "Annulée suite à incident livraison.")
            order.save(update_fields=["status", "cancellation_reason", "updated_at"])
            inc.status = DeliveryIncident.Status.RESOLVED
            inc.resolved_at = timezone.now()
            inc.save(update_fields=["status", "resolved_at"])
            messages.success(request, f"Commande {order.code} annulée — incident clos.")
        elif action == "resolve":
            inc.status = DeliveryIncident.Status.RESOLVED
            inc.resolved_at = timezone.now()
            inc.save(update_fields=["status", "resolved_at"])
            messages.success(request, "Incident marqué comme résolu.")
        else:
            inc.status = request.POST.get("status", inc.status)
            inc.priority = request.POST.get("priority", inc.priority)
            if inc.status == DeliveryIncident.Status.RESOLVED:
                inc.resolved_at = timezone.now()
            inc.save()
            messages.success(request, "Incident mis à jour.")
        return redirect("bo_admin_incidents")

    qs = DeliveryIncident.objects.select_related(
        "delivery",
        "delivery__order",
        "delivery__order__client",
        "delivery__order__pharmacy",
        "delivery__courier",
        "reported_by",
    ).order_by("-created_at")
    status = request.GET.get("status", "")
    priority = request.GET.get("priority", "")
    if status:
        qs = qs.filter(status=status)
    if priority:
        qs = qs.filter(priority=priority)

    page = paginate(request, qs, 15)
    for inc in page.object_list:
        if inc.status != DeliveryIncident.Status.RESOLVED:
            inc.nearby_couriers = nearby_couriers_for_incident(inc)
        else:
            inc.nearby_couriers = []

    return render(
        request,
        "backoffice/admin/incidents.html",
        _ctx(
            request,
            "incidents",
            page_obj=page,
            total_count=qs.count(),
            filter_status=status,
            filter_priority=priority,
            statuses=DeliveryIncident.Status.choices,
            priorities=DeliveryIncident.Priority.choices,
            incident_types=DeliveryIncident.Type.choices,
            resolution_stats=incident_resolution_stats(),
        ),
    )


@role_required(*support_roles)
def admin_notifications(request):
    """Centre de notifications — campagnes et paramètres (CDC §3.1 / §4.11)."""
    from datetime import datetime

    process_due_campaigns()
    settings_obj = PlatformNotificationSettings.load()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_settings":
            settings_obj.max_daily_per_user = max(
                1, int(request.POST.get("max_daily_per_user") or 8)
            )
            settings_obj.quiet_hours_start = request.POST.get("quiet_hours_start") or "21:00"
            settings_obj.quiet_hours_end = request.POST.get("quiet_hours_end") or "07:00"
            settings_obj.channel_sms_enabled = request.POST.get("channel_sms_enabled") == "on"
            settings_obj.channel_email_enabled = request.POST.get("channel_email_enabled") == "on"
            settings_obj.channel_push_enabled = request.POST.get("channel_push_enabled") == "on"
            settings_obj.channel_whatsapp_enabled = (
                request.POST.get("channel_whatsapp_enabled") == "on"
            )
            settings_obj.save()
            messages.success(request, "Paramètres notifications enregistrés.")
        elif action == "create_campaign":
            channels = request.POST.getlist("channels") or [Notification.Channel.IN_APP]
            scheduled_raw = (request.POST.get("scheduled_at") or "").strip()
            scheduled_at = None
            if scheduled_raw:
                try:
                    scheduled_at = timezone.make_aware(
                        datetime.fromisoformat(scheduled_raw),
                        timezone.get_current_timezone(),
                    )
                except ValueError:
                    messages.error(request, "Date de planification invalide.")
                    return redirect("bo_admin_notifications")
            campaign = NotificationCampaign.objects.create(
                title=request.POST.get("title", "").strip()[:200],
                message=request.POST.get("message", "").strip(),
                notification_type=request.POST.get("notification_type")
                or Notification.Type.INFO,
                audience=request.POST.get("audience") or NotificationCampaign.Audience.ALL,
                channels=channels,
                status=NotificationCampaign.Status.SCHEDULED
                if scheduled_at
                else NotificationCampaign.Status.DRAFT,
                scheduled_at=scheduled_at,
                created_by=request.user,
            )
            if request.POST.get("send_now") == "1":
                send_campaign(campaign)
                messages.success(request, f"Campagne « {campaign.title} » envoyée.")
            else:
                messages.success(request, f"Campagne « {campaign.title} » enregistrée.")
        elif action == "send_campaign":
            campaign = get_object_or_404(NotificationCampaign, pk=request.POST.get("campaign_id"))
            count = send_campaign(campaign)
            messages.success(request, f"Campagne envoyée à {count} utilisateur(s).")
        return redirect("bo_admin_notifications")

    campaigns = NotificationCampaign.objects.select_related("created_by").order_by("-created_at")[:30]
    stats = {
        "total": Notification.objects.count(),
        "unread": Notification.objects.filter(is_read=False).count(),
        "sent_today": Notification.objects.filter(
            created_at__date=timezone.localdate()
        ).count(),
    }
    return render(
        request,
        "backoffice/admin/notifications.html",
        _ctx(
            request,
            "notifications",
            campaigns=campaigns,
            settings_obj=settings_obj,
            stats=stats,
            audiences=NotificationCampaign.Audience.choices,
            notif_types=Notification.Type.choices,
            channels=Notification.Channel.choices,
        ),
    )


@role_required(*admin_roles)
def admin_access_config(request):
    """Configuration rôles & permissions (CDC §4.2 — p.27)."""
    from core.platform_access import (
        ADMIN_MODULE_LABELS,
        ALL_ADMIN_MODULES,
        CDC_ROLE_PROFILES,
        MOD_ACCESS_CONFIG,
        PHARMACY_JOB_LABELS,
        PHARMACY_PERM_LABELS,
        PORTAL_MODULE_LABELS,
        admin_module_flags,
        can_edit_pharmacy_permissions,
        can_edit_platform_modules,
        can_edit_portal_permissions,
        get_pharmacy_role_permissions_map,
        get_platform_role_modules,
        get_portal_role_modules,
        platform_roles_for_config_ui,
        portal_roles_for_config_ui,
        save_pharmacy_role_permissions,
        set_platform_role_modules,
        set_portal_role_modules,
    )

    flags = admin_module_flags(request.user)
    if not flags.get(MOD_ACCESS_CONFIG, True):
        messages.error(request, "Vous n'avez pas accès à la configuration des rôles.")
        return redirect("bo_admin_dashboard")

    editable_pharmacy = can_edit_pharmacy_permissions(request.user)
    editable_platform = can_edit_platform_modules(request.user)
    editable_portal = can_edit_portal_permissions(request.user)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_platform_modules":
            if not editable_platform:
                messages.error(request, "Seul un super administrateur peut modifier les modules admin.")
                return redirect("bo_admin_access_config")
            role = request.POST.get("platform_role")
            if role in dict(platform_roles_for_config_ui()):
                modules = [m for m in request.POST.getlist("modules") if m in ALL_ADMIN_MODULES]
                set_platform_role_modules(role, modules)
                _audit(request, "update_platform_modules", "access_config", role, True)
                messages.success(request, f"Modules mis à jour pour {role}.")
        elif action == "save_pharmacy_permissions":
            if not editable_pharmacy:
                messages.error(request, "Vous n'avez pas le droit de modifier les permissions pharmacie.")
                return redirect("bo_admin_access_config")
            mapping = {}
            for job in PharmacyEmployee.JobRole.values:
                mapping[job] = [
                    p for p in request.POST.getlist(f"perm_{job}") if p in PHARMACY_PERM_LABELS
                ]
            save_pharmacy_role_permissions(mapping)
            _audit(request, "update_pharmacy_permissions", "access_config", "matrice ERP", True)
            messages.success(request, "Permissions pharmacie enregistrées.")
        elif action == "save_portal_modules":
            if not editable_portal:
                messages.error(request, "Seul un super administrateur peut modifier les portails métier.")
                return redirect("bo_admin_access_config")
            role = request.POST.get("portal_role")
            role_labels = dict(portal_roles_for_config_ui())
            module_labels = PORTAL_MODULE_LABELS.get(role, {})
            if role in role_labels:
                modules = [m for m in request.POST.getlist("modules") if m in module_labels]
                set_portal_role_modules(role, modules)
                _audit(request, "update_portal_modules", "access_config", role, True)
                messages.success(request, f"Modules portail mis à jour pour {role_labels[role]}.")
        return redirect("bo_admin_access_config")

    perm_map = get_pharmacy_role_permissions_map()
    pharmacy_matrix = [
        {
            "role": job,
            "label": PHARMACY_JOB_LABELS.get(job, job),
            "perms_active": perm_map.get(job, set()),
        }
        for job in PharmacyEmployee.JobRole.values
    ]
    platform_matrix = [
        {
            "role": role,
            "label": label,
            "modules_active": get_platform_role_modules(role),
        }
        for role, label in platform_roles_for_config_ui()
    ]
    portal_matrix = []
    for role, label in portal_roles_for_config_ui():
        active = set(get_portal_role_modules(role))
        portal_matrix.append(
            {
                "role": role,
                "label": label,
                "module_labels": PORTAL_MODULE_LABELS.get(role, {}),
                "modules_active": active,
            }
        )

    return render(
        request,
        "backoffice/admin/access_config.html",
        _ctx(
            request,
            "access_config",
            cdc_roles=CDC_ROLE_PROFILES,
            pharmacy_matrix=pharmacy_matrix,
            platform_matrix=platform_matrix,
            portal_matrix=portal_matrix,
            perm_labels=PHARMACY_PERM_LABELS,
            module_labels=ADMIN_MODULE_LABELS,
            editable_pharmacy=editable_pharmacy,
            editable_platform=editable_platform,
            editable_portal=editable_portal,
        ),
    )


@role_required(*admin_roles)
def admin_platform_settings(request):
    """Paramètres généraux de la plateforme (maquette super admin)."""
    import json

    from accounts.models import PlatformSettings
    from django.core.cache import cache
    from django.http import JsonResponse
    from notifications.models import PlatformNotificationSettings
    from core.platform_access import MOD_PLATFORM_SETTINGS, admin_module_flags

    flags = admin_module_flags(request.user)
    if not (flags.get(MOD_PLATFORM_SETTINGS, False) or request.user.role == User.Role.SUPERADMIN):
        messages.error(request, "Vous n'avez pas accès aux paramètres plateforme.")
        return redirect("bo_admin_dashboard")

    settings_obj = PlatformSettings.load()
    notif_settings = PlatformNotificationSettings.load()

    if request.GET.get("export") == "1":
        payload = {
            "platform_name": settings_obj.platform_name,
            "slogan": settings_obj.slogan,
            "currency": settings_obj.currency,
            "country": settings_obj.country,
            "default_language": settings_obj.default_language,
            "date_format": settings_obj.date_format,
            "timezone": settings_obj.timezone,
            "time_format": settings_obj.time_format,
            "security": {
                "two_factor_required": settings_obj.two_factor_required,
                "session_expiry_minutes": settings_obj.session_expiry_minutes,
                "password_complexity": settings_obj.password_complexity,
                "login_attempt_limit": settings_obj.login_attempt_limit,
                "activity_logging": settings_obj.activity_logging,
            },
            "notifications": {
                "email": notif_settings.channel_email_enabled,
                "push": notif_settings.channel_push_enabled,
                "sms": notif_settings.channel_sms_enabled,
            },
            "backup": {
                "auto_backup": settings_obj.auto_backup,
                "backup_frequency": settings_obj.backup_frequency,
                "backup_retention_days": settings_obj.backup_retention_days,
                "scheduled_maintenance": settings_obj.scheduled_maintenance,
            },
            "integrations": settings_obj.integrations or {},
        }
        response = JsonResponse(payload, json_dumps_params={"indent": 2, "ensure_ascii": False})
        response["Content-Disposition"] = 'attachment; filename="gabpharma-parametres.json"'
        return response

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "general":
            settings_obj.platform_name = request.POST.get("platform_name", settings_obj.platform_name)[:120]
            settings_obj.slogan = request.POST.get("slogan", settings_obj.slogan)[:200]
            settings_obj.currency = request.POST.get("currency", settings_obj.currency)
            settings_obj.country = request.POST.get("country", settings_obj.country)[:80]
            settings_obj.default_language = request.POST.get("default_language", settings_obj.default_language)
            settings_obj.date_format = request.POST.get("date_format", settings_obj.date_format)
            settings_obj.timezone = request.POST.get("timezone", settings_obj.timezone)[:64]
            settings_obj.time_format = request.POST.get("time_format", settings_obj.time_format)
            settings_obj.save()
            _audit(request, "update_platform_settings", "platform_settings", "general", True)
            messages.success(request, "Informations générales enregistrées.")
        elif action == "security" and request.user.role == User.Role.SUPERADMIN:
            settings_obj.two_factor_required = request.POST.get("two_factor_required") == "on"
            settings_obj.password_complexity = request.POST.get("password_complexity") == "on"
            settings_obj.activity_logging = request.POST.get("activity_logging") == "on"
            try:
                settings_obj.session_expiry_minutes = max(
                    5, int(request.POST.get("session_expiry_minutes", 30))
                )
                settings_obj.login_attempt_limit = max(
                    3, int(request.POST.get("login_attempt_limit", 5))
                )
            except (TypeError, ValueError):
                pass
            settings_obj.save()
            _audit(request, "update_platform_settings", "platform_settings", "security", True)
            messages.success(request, "Paramètres de sécurité enregistrés.")
        elif action == "notifications":
            notif_settings.channel_email_enabled = request.POST.get("channel_email") == "on"
            notif_settings.channel_push_enabled = request.POST.get("channel_push") == "on"
            notif_settings.channel_sms_enabled = request.POST.get("channel_sms") == "on"
            notif_settings.save()
            settings_obj.integrations = settings_obj.integrations or {}
            settings_obj.integrations["auto_reminders"] = request.POST.get("auto_reminders") == "on"
            settings_obj.save(update_fields=["integrations", "updated_at"])
            messages.success(request, "Paramètres de notifications enregistrés.")
        elif action == "backup" and request.user.role == User.Role.SUPERADMIN:
            settings_obj.auto_backup = request.POST.get("auto_backup") == "on"
            settings_obj.scheduled_maintenance = request.POST.get("scheduled_maintenance") == "on"
            settings_obj.backup_frequency = request.POST.get(
                "backup_frequency", settings_obj.backup_frequency
            )
            try:
                settings_obj.backup_retention_days = max(
                    7, int(request.POST.get("backup_retention_days", 30))
                )
            except (TypeError, ValueError):
                pass
            settings_obj.save()
            messages.success(request, "Paramètres de sauvegarde enregistrés.")
        elif action == "clear_cache" and request.user.role == User.Role.SUPERADMIN:
            cache.clear()
            messages.success(request, "Cache applicatif réinitialisé.")
        return redirect("bo_admin_platform_settings")

    nav_categories = [
        {"id": "general", "label": "Général", "icon": "settings", "href": "#general"},
        {"id": "security", "label": "Sécurité", "icon": "shield", "href": "#security"},
        {"id": "users", "label": "Utilisateurs & rôles", "icon": "group", "href": "#users"},
        {"id": "pharmacies", "label": "Pharmacies", "icon": "local_pharmacy", "href": "#pharmacies"},
        {"id": "payments", "label": "Paiements & abonnements", "icon": "payments", "href": "#payments"},
        {"id": "insurance", "label": "Assurances", "icon": "health_and_safety", "href": "#insurance"},
        {"id": "delivery", "label": "Livraison", "icon": "local_shipping", "href": "#delivery"},
        {"id": "notifications", "label": "Notifications", "icon": "notifications", "href": "#notifications"},
        {"id": "medicines", "label": "Médicaments", "icon": "medication", "href": "#medicines"},
        {"id": "integrations", "label": "Intégrations", "icon": "hub", "href": "#integrations"},
    ]
    settings_shortcuts = [
        {
            "id": "users",
            "title": "Utilisateurs & rôles",
            "description": "Matrice des rôles plateforme, permissions ERP pharmacie et portails métier.",
            "icon": "group",
            "manage_url": reverse("bo_admin_access_config"),
            "manage_label": "Gérer les rôles et permissions",
        },
        {
            "id": "pharmacies",
            "title": "Pharmacies",
            "description": "Officines inscrites, validation, statuts et conformité réglementaire.",
            "icon": "local_pharmacy",
            "manage_url": reverse("bo_admin_pharmacies"),
            "manage_label": "Gérer les pharmacies",
        },
        {
            "id": "payments",
            "title": "Paiements & abonnements",
            "description": "Commissions, règlements Mobile Money, forfaits et abonnements plateforme.",
            "icon": "payments",
            "manage_url": reverse("bo_admin_payments"),
            "manage_label": "Paramètres paiement",
            "secondary_url": reverse("bo_admin_subscriptions"),
            "secondary_label": "Abonnements",
        },
        {
            "id": "insurance",
            "title": "Assurances",
            "description": "Partenaires CNAMGS, ASCOMA et prises en charge.",
            "icon": "health_and_safety",
            "manage_url": reverse("bo_admin_insurance"),
            "manage_label": "Gérer les assurances",
        },
        {
            "id": "delivery",
            "title": "Livraison",
            "description": "Livreurs, rémunération par course et service de livraison national.",
            "icon": "local_shipping",
            "manage_url": reverse("bo_admin_couriers"),
            "manage_label": "Gérer la livraison",
        },
        {
            "id": "medicines",
            "title": "Médicaments",
            "description": "Référentiel national, catégories et médicaments essentiels.",
            "icon": "medication",
            "manage_url": reverse("bo_admin_medicines"),
            "manage_label": "Gérer les médicaments",
        },
    ]
    quick_actions = [
        {"type": "link", "url": reverse("bo_admin_access_config"), "label": "Gérer les rôles", "icon": "admin_panel_settings"},
        {"type": "link", "url": reverse("bo_admin_audit"), "label": "Journal d'audit", "icon": "history"},
        {"type": "post", "action": "clear_cache", "label": "Nettoyage des données", "icon": "cleaning_services"},
        {"type": "export", "url": reverse("bo_admin_platform_settings") + "?export=1", "label": "Exporter les données", "icon": "download"},
        {"type": "post", "action": "clear_cache", "label": "Réinitialiser le cache", "icon": "cached"},
        {"type": "anchor", "url": "#about", "label": "À propos", "icon": "info"},
    ]
    integrations = settings_obj.integrations or {}
    auto_reminders = bool(integrations.get("auto_reminders", True))
    integration_manage_urls = {
        "airtel_money": reverse("bo_admin_payments"),
        "moov_money": reverse("bo_admin_payments"),
        "cnamgs": reverse("bo_admin_insurance"),
        "ascoma": reverse("bo_admin_insurance"),
        "delivery_service": reverse("bo_admin_couriers"),
    }
    integration_items = []
    for key, data in integrations.items():
        if key == "auto_reminders" or not isinstance(data, dict) or not data.get("label"):
            continue
        integration_items.append(
            {
                "key": key,
                "label": data["label"],
                "connected": data.get("connected", False),
                "manage_url": integration_manage_urls.get(key, reverse("bo_admin_payments")),
                "status_label": "Activé" if key == "delivery_service" and data.get("connected") else (
                    "Connecté" if data.get("connected") else "Non connecté"
                ),
            }
        )

    return render(
        request,
        "backoffice/admin/platform_settings.html",
        _ctx(
            request,
            "platform_settings",
            settings=settings_obj,
            notif_settings=notif_settings,
            nav_categories=nav_categories,
            settings_shortcuts=settings_shortcuts,
            quick_actions=quick_actions,
            integrations=integrations,
            integration_items=integration_items,
            auto_reminders=auto_reminders,
            timezone_choices=[
                ("Africa/Libreville", "(GMT+1) Afrique de l'Ouest"),
                ("Africa/Lagos", "(GMT+1) Lagos"),
                ("UTC", "(GMT+0) UTC"),
            ],
            settings_period_start="01/05/2025",
            settings_period_end="31/05/2025",
        ),
    )


@role_required(*superadmin_roles)
def admin_audit(request):
    qs = AuditLog.objects.select_related("user").order_by("-created_at")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(module__icontains=q) | Q(details__icontains=q))
    return render(
        request,
        "backoffice/admin/audit.html",
        _ctx(
            request,
            "audit",
            page_obj=paginate(request, qs, 25),
            total_count=qs.count(),
            q=q,
        ),
    )


# ─── Pharmacie ─────────────────────────────────────────────────────
def _pharmacies_for(user):
    """Pharmacies gérées (titulaire ou membre du personnel)."""
    return _pharmacies_for_access(user)


def _pharmacy_for(user, request=None):
    """Pharmacie active pour l'utilisateur."""
    return _pharmacy_for_access(user, request)

def _unique_medicine_slug(base: str) -> str:
    base = slugify(base)[:45] or "med"
    slug, i = base, 1
    while Medicine.objects.filter(slug=slug).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


def _unique_category_slug(base: str) -> str:
    base = slugify(base)[:45] or "cat"
    slug, i = base, 1
    while Category.objects.filter(slug=slug).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


CONTENANCE_PRESETS = [
    "50 ml",
    "100 ml",
    "150 ml",
    "200 ml",
    "300 ml",
    "400 ml",
    "500 ml",
    "8 comprimés",
    "16 comprimés",
    "20 comprimés",
    "30 comprimés",
]


def _contenance_from_post(request):
    preset = request.POST.get("contenance_preset", "").strip()
    custom = request.POST.get("contenance_custom", "").strip()
    if preset and preset != "custom":
        return preset
    return custom


def _sync_medicine_product_line(medicine, field_names):
    """Partage le contenu fiche entre toutes les contenances d'un même produit."""
    updates = {f: getattr(medicine, f) for f in field_names}
    Medicine.objects.filter(name=medicine.name).exclude(pk=medicine.pk).update(**updates)


def _log_stock_movement(stock, movement_type, delta, user, note=""):
    StockMovement.objects.create(
        stock=stock,
        movement_type=movement_type,
        quantity_delta=delta,
        quantity_after=stock.quantity,
        note=note,
        created_by=user,
    )


def _pharmacy_stock_redirect(request):
    params = {"tab": request.POST.get("tab") or "all"}
    for key in ("q", "category", "visibility", "sort"):
        val = request.POST.get(key) or ""
        if val and not (key == "sort" and val == "name"):
            params[key] = val
    return redirect(f"{reverse('bo_pharmacy_stocks')}?{urlencode(params)}")


def _pharmacy_orders_redirect(request):
    target = (request.POST.get("next") or "").strip()
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect("bo_pharmacy_orders")


def _client_initials(user):
    parts = [user.first_name or "", user.last_name or ""]
    initials = "".join(p[:1].upper() for p in parts if p)
    return initials or (user.username[:2].upper() if user.username else "?")


def _client_age_years(user):
    profile = getattr(user, "client_profile", None)
    if profile is None:
        try:
            profile = ClientProfile.objects.filter(user=user).first()
        except Exception:
            profile = None
    dob = getattr(profile, "date_of_birth", None) if profile else None
    if not dob:
        return None
    today = timezone.now().date()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age if age >= 0 else None


def _format_file_size(size):
    if not size:
        return ""
    if size < 1024:
        return f"{size} o"
    if size < 1024 * 1024:
        return f"{size // 1024} Ko"
    return f"{size / (1024 * 1024):.1f} Mo"


def _rx_detail_context(request, order, rx):
    client = order.client
    rx_code = f"ORD-{rx.created_at.year}-{rx.id:04d}"
    badge_map = {
        Prescription.Status.PENDING: "pending",
        Prescription.Status.DRAFT: "pending",
        Prescription.Status.VALIDATED: "validated",
        Prescription.Status.USED: "used",
        Prescription.Status.REJECTED: "rejected",
    }
    rx_pending = rx.status in {
        Prescription.Status.PENDING,
        Prescription.Status.DRAFT,
    } and order.status == Order.Status.AWAITING_RX

    timeline = [
        {
            "label": "Ordonnance reçue",
            "time": rx.created_at.strftime("%d/%m/%Y %H:%M"),
            "done": True,
            "current": False,
        },
    ]
    if rx.status in {Prescription.Status.PENDING, Prescription.Status.DRAFT}:
        timeline.append(
            {
                "label": "En attente de validation",
                "time": "",
                "done": False,
                "current": True,
            }
        )
    if rx.status in {Prescription.Status.VALIDATED, Prescription.Status.USED}:
        t = rx.reviewed_at or rx.created_at
        timeline.append(
            {
                "label": "Ordonnance validée",
                "time": t.strftime("%d/%m/%Y %H:%M") if t else "",
                "done": True,
                "current": False,
            }
        )
    if rx.status == Prescription.Status.REJECTED:
        t = rx.reviewed_at or rx.created_at
        timeline.append(
            {
                "label": "Ordonnance refusée",
                "time": t.strftime("%d/%m/%Y %H:%M") if t else "",
                "done": True,
                "current": False,
            }
        )

    file_name = rx.file.name.split("/")[-1] if rx.file else "Ordonnance"
    try:
        file_size = rx.file.size if rx.file else 0
    except Exception:
        file_size = 0

    doctor = (rx.doctor_name or "").strip()
    doctor_parts = doctor.replace("Dr.", "").replace("Dr ", "").strip().split()
    doctor_initials = "".join(p[:1].upper() for p in doctor_parts[:2]) or "DR"

    return {
        "order": order,
        "rx": rx,
        "rx_code": rx_code,
        "rx_badge": badge_map.get(rx.status, "pending"),
        "rx_pending": rx_pending,
        "rx_items": order.items.select_related("medicine").all(),
        "client": client,
        "client_initials": _client_initials(client),
        "client_age": _client_age_years(client),
        "client_location": client.display_location,
        "doctor_initials": doctor_initials,
        "file_url": reverse("bo_prescription_file", args=[rx.id]),
        "file_name": file_name,
        "file_size_label": _format_file_size(file_size),
        "rx_timeline": timeline,
        "order_filter_query": request.GET.urlencode(),
    }


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_ORDERS)
def pharmacy_prescription_detail(request, pk):
    """Détail ordonnance — maquette responsive (mobile / tablette / desktop)."""
    pharmacy = _pharmacy_for(request.user, request)
    order = get_object_or_404(
        Order.objects.select_related("client", "linked_prescription", "pharmacy")
        .prefetch_related("items__medicine"),
        pk=pk,
        pharmacy=pharmacy,
    )
    rx = order.linked_prescription
    if not rx:
        messages.error(request, "Cette commande n'a pas d'ordonnance associée.")
        return redirect("bo_pharmacy_orders")

    if request.method == "POST":
        action = request.POST.get("action")
        if action in {"validate_rx", "reject_rx"}:
            pharmacies = list(_pharmacies_for(request.user))
            o = get_object_or_404(
                Order.objects.select_related("linked_prescription", "client"),
                pk=request.POST.get("order_id"),
                pharmacy_id__in=[p.id for p in pharmacies],
            )
            pharmacy = o.pharmacy
            if action == "validate_rx":
                if not has_pharmacy_permission(request.user, pharmacy, PERM_RX):
                    messages.error(request, "Seul un pharmacien habilité peut valider une ordonnance.")
                elif o.status != Order.Status.AWAITING_RX:
                    messages.error(request, "Cette commande n'attend pas de validation d'ordonnance.")
                elif not o.linked_prescription:
                    messages.error(request, "Aucune ordonnance jointe.")
                else:
                    rx_post = o.linked_prescription
                    prev = o.status
                    rx_post.status = Prescription.Status.VALIDATED
                    rx_post.review_notes = request.POST.get("review_notes", "").strip()
                    rx_post.reviewed_at = timezone.now()
                    rx_post.pharmacy = pharmacy
                    rx_post.save()
                    o.status = Order.Status.PREPARING
                    o.save(update_fields=["status", "updated_at"])
                    mark_preparing(o)
                    _notify_order_status(o, previous_status=prev)
                    messages.success(request, f"Ordonnance validée — {o.code} passe en préparation.")
            elif action == "reject_rx":
                if not has_pharmacy_permission(request.user, pharmacy, PERM_RX):
                    messages.error(request, "Seul un pharmacien habilité peut refuser une ordonnance.")
                else:
                    reason_text, err = _pharmacy_refusal_text(
                        request.POST.get("refusal_reason"),
                        request.POST.get("review_notes"),
                    )
                    if err:
                        messages.error(request, err)
                    elif o.status == Order.Status.AWAITING_RX and o.linked_prescription:
                        rx_post = o.linked_prescription
                        rx_post.status = Prescription.Status.REJECTED
                        rx_post.review_notes = reason_text
                        rx_post.reviewed_at = timezone.now()
                        rx_post.save()
                        o.status = Order.Status.CANCELLED
                        o.cancellation_reason = reason_text
                        o.save(update_fields=["status", "cancellation_reason", "updated_at"])
                        _restore_order_stock(o, pharmacy)
                        process_order_refund(o)
                        _notify_order_refused(o, reason_text)
                        messages.success(request, f"Ordonnance refusée — {o.code} annulée.")
                    else:
                        messages.error(request, "Impossible de refuser cette ordonnance.")
        return _pharmacy_orders_redirect(request)

    ctx = _ctx(
        request,
        "prescription_detail",
        refusal_reasons=PHARMACY_REFUSAL_REASONS,
        can_validate_rx=has_pharmacy_permission(request.user, pharmacy, PERM_RX) if pharmacy else False,
        **_rx_detail_context(request, order, rx),
    )
    return render(request, "backoffice/pharmacy/prescription_detail.html", ctx)


@role_required(*pharmacy_roles)
def pharmacy_dashboard(request):
    pharmacy = _pharmacy_for(request.user, request)
    from core.pharmacy_portal import emergency_alerts_for_dashboard

    pending_emergencies = emergency_alerts_for_dashboard(pharmacy)
    today = timezone.now().date()
    orders_qs = (
        Order.objects.filter(pharmacy=pharmacy).exclude(status=Order.Status.CART)
        if pharmacy
        else Order.objects.none()
    )
    stocks_qs = PharmacyStock.objects.filter(pharmacy=pharmacy) if pharmacy else PharmacyStock.objects.none()
    unread_notifs = request.user.notifications.filter(is_read=False)
    soon = today + timedelta(days=60)
    month_start = today.replace(day=1)
    stats = {
        "orders_today": orders_qs.filter(created_at__date=today).count(),
        "received_today": orders_qs.filter(created_at__date=today).count(),
        "preparing": orders_qs.filter(status=Order.Status.PREPARING).count(),
        "to_prepare": orders_qs.filter(
            status__in={Order.Status.PENDING, Order.Status.CONFIRMED, Order.Status.AWAITING_RX}
        ).count(),
        "delivering": orders_qs.filter(status=Order.Status.DELIVERING).count(),
        "emergencies": len(pending_emergencies),
        "urgent_orders": orders_qs.filter(is_urgent=True)
        .exclude(status__in=[Order.Status.DELIVERED, Order.Status.CANCELLED])
        .count(),
        "new_orders": orders_qs.filter(
            status__in={
                Order.Status.PENDING,
                Order.Status.AWAITING_RX,
                Order.Status.CONFIRMED,
            }
        ).count(),
        "unread_alerts": unread_notifs.count(),
        "unread_order_alerts": unread_notifs.filter(
            notification_type=Notification.Type.ORDER
        ).count(),
        "low_stock": sum(1 for s in stocks_qs if 0 < s.quantity <= s.low_stock_threshold),
        "out_stock": stocks_qs.filter(quantity=0).count(),
        "ca_today": orders_qs.filter(created_at__date=today).aggregate(s=Sum("total"))["s"] or 0,
        "ca_month": orders_qs.filter(created_at__date__gte=month_start).aggregate(s=Sum("total"))["s"]
        or 0,
        "stock_refs": stocks_qs.count(),
        "expiring": stocks_qs.filter(
            expiry_date__isnull=False, expiry_date__lte=soon, expiry_date__gte=today
        ).count(),
        "deliveries_done": orders_qs.filter(status=Order.Status.DELIVERED).count(),
        "clients_served": orders_qs.values("client_id").distinct().count(),
    }

    start = today - timedelta(days=13)
    daily = (
        orders_qs.filter(created_at__date__gte=start)
        .annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(c=Count("id"), ca=Sum("total"))
        .order_by("d")
    )
    by_day = {str(r["d"]): r for r in daily if r["d"]}
    labels, orders_data, ca_data = [], [], []
    for i in range(14):
        d = start + timedelta(days=i)
        key = str(d)
        labels.append(d.strftime("%d/%m"))
        row = by_day.get(key, {})
        orders_data.append(row.get("c", 0))
        ca_data.append(row.get("ca", 0) or 0)

    status_rows = orders_qs.values("status").annotate(c=Count("id")).order_by("-c")
    status_map = dict(Order.Status.choices)
    pay_rows = payment_breakdown(pharmacy, since=timezone.now() - timedelta(days=30))
    charts = {
        "orders": {"labels": labels, "data": orders_data},
        "ca": {"labels": labels, "data": ca_data},
        "statuses": {
            "labels": [status_map.get(r["status"], r["status"]) for r in status_rows],
            "data": [r["c"] for r in status_rows],
        },
        "payments": {
            "labels": [p["label"] for p in pay_rows],
            "data": [p["total"] for p in pay_rows],
        },
    }

    courier_requests = list(pending_courier_requests(pharmacy))[:6]
    couriers_nearby = nearby_couriers(pharmacy, limit=5)

    from notifications.routing import annotate_pharmacy_notification

    recent_notifs = [
        annotate_pharmacy_notification(
            n,
            open_url=reverse("bo_pharmacy_notifications") + f"?open={n.id}",
        )
        for n in request.user.notifications.all()[:5]
    ]

    from core.pharmacy_subscription import subscription_summary

    sub_summary = subscription_summary(pharmacy) if pharmacy else {}
    return render(
        request,
        "backoffice/pharmacy/dashboard.html",
        _ctx(
            request,
            "dashboard",
            pharmacy=pharmacy,
            stats=stats,
            sub_summary=sub_summary,
            recent=orders_qs.select_related("client")
            .prefetch_related("payments", "delivery")
            .order_by("-created_at")[:10],
            low_stocks=stocks_qs.filter(quantity__gt=0)
            .select_related("medicine")
            .order_by("quantity")[:6],
            pending_emergencies=pending_emergencies,
            courier_requests=courier_requests,
            couriers_nearby=couriers_nearby,
            payment_breakdown=pay_rows,
            escalation_minutes=EMERGENCY_ESCALATION_MINUTES,
            charts_json=json.dumps(charts),
            recent_notifs=recent_notifs,
        ),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_ORDERS)
def pharmacy_notifications(request):
    from notifications.routing import annotate_pharmacy_notification, resolve_notification_url

    open_id = request.GET.get("open")
    if open_id:
        n = get_object_or_404(Notification, pk=open_id, user=request.user)
        if not n.is_read:
            n.is_read = True
            n.save(update_fields=["is_read"])
        target = resolve_notification_url(n) or reverse("bo_pharmacy_notifications")
        return redirect(target)

    notifs = [
        annotate_pharmacy_notification(
            n,
            open_url=reverse("bo_pharmacy_notifications") + f"?open={n.id}",
        )
        for n in request.user.notifications.all()[:60]
    ]
    return render(
        request,
        "backoffice/pharmacy/notifications.html",
        _ctx(request, "notifications", notifs=notifs),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_STATS)
def pharmacy_stats(request):
    pharmacy = _pharmacy_for(request.user, request)
    period = request.GET.get("period", "30")
    try:
        days = int(period)
    except ValueError:
        days = 30
    days = max(7, min(days, 365))
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    since = timezone.make_aware(datetime.combine(start, datetime.min.time()))

    orders_qs = (
        Order.objects.filter(pharmacy=pharmacy).exclude(status=Order.Status.CART)
        if pharmacy
        else Order.objects.none()
    )
    period_orders = orders_qs.filter(created_at__gte=since)
    daily = (
        period_orders.annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(c=Count("id"), ca=Sum("total"))
        .order_by("d")
    )
    by_day = {str(r["d"]): r for r in daily if r["d"]}
    labels, orders_data, ca_data = [], [], []
    for i in range(days):
        d = start + timedelta(days=i)
        key = str(d)
        labels.append(d.strftime("%d/%m"))
        row = by_day.get(key, {})
        orders_data.append(row.get("c", 0))
        ca_data.append(row.get("ca", 0) or 0)

    top_meds = top_sold_products(pharmacy, since=since, limit=5)
    cat_rows = sales_by_category(pharmacy, since=since)
    pay_rows = payment_breakdown(pharmacy, since=since)
    deliv = delivery_performance(pharmacy)
    from core.payment_settlement import pharmacy_settlement_summary

    settlement = pharmacy_settlement_summary(pharmacy, since=since) if pharmacy else {}
    ca_period = sum(ca_data)
    orders_period = sum(orders_data)
    margin = estimate_margin(pharmacy, since=since)
    kpis = {
        "orders_total": orders_qs.count(),
        "ca_total": orders_qs.aggregate(s=Sum("total"))["s"] or 0,
        "ca_period": ca_period,
        "orders_period": orders_period,
        "avg_basket": int(ca_period / max(orders_period, 1)),
        "margin": margin,
        "margin_pct": int((margin / max(ca_period, 1)) * 100),
        "clients_served": period_orders.values("client_id").distinct().count(),
        "deliveries_done": orders_qs.filter(status=Order.Status.DELIVERED).count(),
        "stock_value": sum((s.quantity * s.price) for s in PharmacyStock.objects.filter(pharmacy=pharmacy))
        if pharmacy
        else 0,
        "deliv_avg_min": deliv.get("avg_delivery_min"),
        "deliv_on_time": deliv.get("on_time_rate"),
        "settlement_net": settlement.get("net", 0),
        "settlement_commission": settlement.get("commission", 0),
        "settlement_pending": settlement.get("pending_payout", 0),
    }
    status_rows = period_orders.values("status").annotate(c=Count("id")).order_by("-c")
    status_map = dict(Order.Status.choices)
    status_export = [
        {"label": status_map.get(r["status"], r["status"]), "count": r["c"]}
        for r in status_rows
    ]
    from core.stats_ui import status_cards_from_rows

    status_cards = status_cards_from_rows(status_rows, status_map)
    charts = {
        "orders": {"labels": labels, "data": orders_data},
        "ca": {"labels": labels, "data": ca_data},
        "categories": {
            "labels": [c["category"] for c in cat_rows],
            "data": [c["total"] for c in cat_rows],
        },
        "payments": {
            "labels": [p["label"] for p in pay_rows],
            "data": [p["total"] for p in pay_rows],
        },
        "statuses": {
            "labels": [status_map.get(r["status"], r["status"]) for r in status_rows],
            "data": [r["c"] for r in status_rows],
        },
    }

    export = request.GET.get("export")
    if export in {"csv", "xlsx", "pdf"}:
        export_payload = {
            "pharmacy_name": pharmacy.name if pharmacy else "",
            "period_days": days,
            "start_date": start,
            "end_date": today,
            "generated_at": timezone.now(),
            "kpis": kpis,
            "daily_labels": labels,
            "orders_data": orders_data,
            "ca_data": ca_data,
            "pay_rows": pay_rows,
            "cat_rows": cat_rows,
            "top_meds": top_meds,
            "deliv": deliv,
            "status_rows": status_export,
        }
        if export == "xlsx":
            return pharmacy_stats_xlsx_response(export_payload, pharmacy)
        if export == "pdf":
            return pharmacy_stats_pdf_response(export_payload, pharmacy)
        return pharmacy_stats_csv_response(export_payload, pharmacy)

    return render(
        request,
        "backoffice/pharmacy/stats.html",
        _ctx(
            request,
            "stats",
            pharmacy=pharmacy,
            kpis=kpis,
            period=days,
            charts_json=json.dumps(charts),
            top_meds=top_meds,
            pay_rows=pay_rows,
            cat_rows=cat_rows,
            deliv=deliv,
            status_cards=status_cards,
        ),
    )


@login_required
def preview_credentials_email(request):
    """Aperçu du template e-mail identifiants (dev / admin)."""
    if not settings.DEBUG and request.user.role not in {
        User.Role.SUPERADMIN,
        User.Role.ADMIN,
    }:
        raise Http404()
    demo_password = request.GET.get("mdp", "DemoPass2026!")
    _, _, html_body, _ = build_credentials_email(
        request.user, demo_password, request=request
    )
    return HttpResponse(html_body)


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_ORDERS)
def pharmacy_orders(request):
    pharmacy = _pharmacy_for(request.user, request)
    pharmacies = list(_pharmacies_for(request.user))
    order_filter_extra = list_query_params(
        request, "q", "status", "date_from", "date_to", "delivery_mode", "payment", "urgent"
    )
    if request.method == "POST" and pharmacies:
        action = request.POST.get("action") or "status"

        if action == "create_order" and pharmacy:
            client = get_object_or_404(User, pk=request.POST.get("client_id"), role=User.Role.CLIENT)
            stock = get_object_or_404(
                PharmacyStock, pk=request.POST.get("stock_id"), pharmacy=pharmacy
            )
            try:
                qty = max(1, int(request.POST.get("quantity") or 1))
            except ValueError:
                qty = 1
            is_urgent = request.POST.get("is_urgent") == "1"
            mode = request.POST.get("delivery_mode") or Order.DeliveryMode.PICKUP
            if mode not in dict(Order.DeliveryMode.choices):
                mode = Order.DeliveryMode.PICKUP
            if stock.quantity < qty:
                messages.error(request, f"Stock insuffisant ({stock.quantity} disponible).")
            else:
                fee = 0 if mode == Order.DeliveryMode.PICKUP else 1500
                order = Order.objects.create(
                    client=client,
                    pharmacy=pharmacy,
                    status=Order.Status.PREPARING,
                    delivery_mode=mode,
                    delivery_fee=fee,
                    is_urgent=is_urgent,
                    notes=request.POST.get("notes", "").strip(),
                    preparing_at=timezone.now(),
                )
                OrderItem.objects.create(
                    order=order,
                    medicine=stock.medicine,
                    quantity=qty,
                    unit_price=stock.display_price,
                    medicine_name=str(stock.medicine),
                )
                stock.quantity = max(0, stock.quantity - qty)
                stock.save(update_fields=["quantity", "updated_at"])
                from core.pharmacy_notifications import check_stock_alert

                check_stock_alert(stock, previous_qty=stock.quantity + qty)
                order.recalculate_totals()
                Payment.objects.create(
                    order=order,
                    method=Payment.Method.PHARMACY,
                    amount=order.total,
                    status=Payment.Status.SUCCESS,
                    reference=f"PH-{order.code}",
                )
                messages.success(
                    request,
                    f"Commande {'urgente ' if is_urgent else ''}{order.code} créée — à préparer.",
                )
            return _pharmacy_orders_redirect(request)

        o = get_object_or_404(
            Order.objects.select_related("linked_prescription", "client"),
            pk=request.POST.get("order_id"),
            pharmacy_id__in=[p.id for p in pharmacies],
        )
        pharmacy = o.pharmacy
        if action == "validate_rx":
            if not has_pharmacy_permission(request.user, pharmacy, PERM_RX):
                messages.error(request, "Seul un pharmacien habilité peut valider une ordonnance.")
                return _pharmacy_orders_redirect(request)
            if o.status != Order.Status.AWAITING_RX:
                messages.error(request, "Cette commande n’attend pas de validation d’ordonnance.")
            elif not o.linked_prescription:
                messages.error(request, "Aucune ordonnance jointe.")
            else:
                rx = o.linked_prescription
                prev = o.status
                rx.status = Prescription.Status.VALIDATED
                rx.review_notes = request.POST.get("review_notes", "").strip()
                rx.reviewed_at = timezone.now()
                rx.pharmacy = pharmacy
                rx.save()
                o.status = Order.Status.PREPARING
                o.save(update_fields=["status", "updated_at"])
                mark_preparing(o)
                _notify_order_status(o, previous_status=prev)
                messages.success(
                    request,
                    f"Ordonnance validée — {o.code} passe en préparation.",
                )
        elif action == "reject_rx":
            if not has_pharmacy_permission(request.user, pharmacy, PERM_RX):
                messages.error(request, "Seul un pharmacien habilité peut refuser une ordonnance.")
                return _pharmacy_orders_redirect(request)
            reason_text, err = _pharmacy_refusal_text(
                request.POST.get("refusal_reason"),
                request.POST.get("review_notes"),
            )
            if err:
                messages.error(request, err)
            elif o.status == Order.Status.AWAITING_RX and o.linked_prescription:
                rx = o.linked_prescription
                rx.status = Prescription.Status.REJECTED
                rx.review_notes = reason_text
                rx.reviewed_at = timezone.now()
                rx.save()
                o.status = Order.Status.CANCELLED
                o.cancellation_reason = reason_text
                o.save(update_fields=["status", "cancellation_reason", "updated_at"])
                _restore_order_stock(o, pharmacy)
                process_order_refund(o)
                _notify_order_refused(o, reason_text)
                messages.success(request, f"Ordonnance refusée — {o.code} annulée.")
            else:
                messages.error(request, "Impossible de refuser cette ordonnance.")
        elif action == "refuse_order":
            reason_text, err = _pharmacy_refusal_text(
                request.POST.get("refusal_reason"),
                request.POST.get("review_notes"),
            )
            refusables = {
                Order.Status.PENDING,
                Order.Status.CONFIRMED,
                Order.Status.AWAITING_RX,
            }
            if err:
                messages.error(request, err)
            elif o.status not in refusables:
                messages.error(request, "Cette commande ne peut plus être refusée.")
            else:
                if o.linked_prescription and o.status == Order.Status.AWAITING_RX:
                    rx = o.linked_prescription
                    rx.status = Prescription.Status.REJECTED
                    rx.review_notes = reason_text
                    rx.reviewed_at = timezone.now()
                    rx.save(
                        update_fields=["status", "review_notes", "reviewed_at"]
                    )
                o.status = Order.Status.CANCELLED
                o.cancellation_reason = reason_text
                o.save(update_fields=["status", "cancellation_reason", "updated_at"])
                _restore_order_stock(o, pharmacy)
                refunded = process_order_refund(o)
                _notify_order_refused(o, reason_text)
                messages.success(
                    request,
                    f"Commande {o.code} refusée."
                    + (f" Remboursement initié ({refunded} paiement(s))." if refunded else ""),
                )
        elif action == "status":
            st = request.POST.get("status")
            # Bloquer l'avancement tant que RX non validée
            if o.status == Order.Status.AWAITING_RX:
                messages.error(
                    request,
                    "Validez ou refusez d’abord l’ordonnance avant de changer le statut.",
                )
            elif st == Order.Status.CANCELLED:
                messages.error(
                    request,
                    "Utilisez « Refuser la commande » et indiquez un motif.",
                )
            elif st in dict(Order.Status.choices) and st != Order.Status.CART:
                from core.pharmacy_order_workflow import pharmacy_can_set_status

                if not pharmacy_can_set_status(o, st):
                    messages.error(
                        request,
                        "Ce statut n'est pas autorisé à cette étape "
                        "(Livrée et Remboursée sont gérées automatiquement).",
                    )
                else:
                    prev = o.status
                    o.status = st
                    o.save(update_fields=["status", "updated_at"])
                    if st == Order.Status.PREPARING:
                        mark_preparing(o)
                    if st == Order.Status.READY:
                        ensure_delivery_for_order(o)
                        ensure_pharmacy_handoff_code(o)
                        messages.success(
                            request,
                            f"{o.code} prête — visible pour les livreurs disponibles.",
                        )
                    else:
                        messages.success(request, f"{o.code} → {o.get_status_display()}")
                    if prev != st:
                        _notify_order_status(o, previous_status=prev)
                    if st == Order.Status.DELIVERED:
                        handle_order_delivered(o)
        elif action == "request_courier":
            if o.status != Order.Status.READY:
                messages.error(request, "La commande doit être « Prête » pour demander un livreur.")
            else:
                delivery = ensure_delivery_for_order(o)
                if delivery and delivery.courier_id:
                    messages.info(
                        request,
                        f"Un livreur est déjà assigné ({delivery.courier}).",
                    )
                else:
                    if delivery:
                        delivery.status = Delivery.Status.PENDING
                        delivery.courier = None
                        delivery.save(update_fields=["status", "courier", "updated_at"])
                    messages.success(
                        request,
                        f"Demande de livreur relancée pour {o.code} — priorité aux livreurs proches.",
                    )
        elif action == "validate_handoff":
            scan_raw = (request.POST.get("qr_payload") or "").strip()
            code = (request.POST.get("handoff_code") or "").strip()
            target = o
            try:
                if scan_raw:
                    payload = parse_traceability_qr(scan_raw)
                    target = resolve_handoff_order_from_qr(
                        payload, pharmacy_id=pharmacy.id
                    )
                    validate_pharmacy_handoff(
                        target, request.user, qr_payload=payload
                    )
                else:
                    validate_pharmacy_handoff(target, request.user, code=code)
                messages.success(
                    request,
                    f"Remise validée — {target.code}. Le livreur peut retirer le colis.",
                )
            except HandoffError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Action non reconnue.")
        return _pharmacy_orders_redirect(request)

    pharmacy_ids = [p.id for p in pharmacies]

    if pharmacy_ids:
        repair_stale_delivering_orders(
            pharmacy_id=pharmacy.id if pharmacy and len(pharmacies) == 1 else None
        )
    orders = (
        Order.objects.filter(pharmacy_id__in=pharmacy_ids)
        .exclude(status=Order.Status.CART)
        .select_related("client", "linked_prescription", "pharmacy")
        .prefetch_related("items", "payments", "delivery")
        .annotate(
            rx_priority=Case(
                When(status=Order.Status.AWAITING_RX, then=Value(0)),
                When(is_urgent=True, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .order_by("rx_priority", "-created_at")
        if pharmacy_ids
        else Order.objects.none()
    )
    order_filter = request.GET.get("filter", "all")
    prep_statuses = {
        Order.Status.PENDING,
        Order.Status.CONFIRMED,
        Order.Status.PREPARING,
        Order.Status.AWAITING_RX,
        Order.Status.READY,
    }
    searched_orders = filter_pharmacy_orders(orders, request)
    order_kpis = {
        "all": searched_orders.count(),
        "prep": searched_orders.filter(status__in=prep_statuses).count(),
        "deliver": pharmacy_active_delivery_filter(searched_orders).count(),
        "done": searched_orders.filter(status=Order.Status.DELIVERED).count(),
        "cancelled": searched_orders.filter(status=Order.Status.CANCELLED).count(),
        "urgent": searched_orders.filter(is_urgent=True)
        .exclude(status=Order.Status.CANCELLED)
        .count(),
    }
    orders = searched_orders
    if order_filter == "prep":
        orders = orders.filter(status__in=prep_statuses)
    elif order_filter == "deliver":
        orders = pharmacy_active_delivery_filter(searched_orders)
    elif order_filter == "done":
        orders = orders.filter(status=Order.Status.DELIVERED)
    elif order_filter == "cancelled":
        orders = orders.filter(status=Order.Status.CANCELLED)
    elif order_filter == "urgent":
        orders = orders.filter(is_urgent=True).exclude(status=Order.Status.CANCELLED)
    orders_count = orders.count()
    for _o in orders:
        if order_needs_pharmacy_handoff(_o) and _o.status in {
            Order.Status.READY,
            Order.Status.DELIVERING,
        }:
            ensure_pharmacy_handoff_code(_o)
    highlight_order_id = None
    order_param = request.GET.get("order", "").strip()
    if order_param.isdigit():
        highlight_order_id = int(order_param)
        focus = (
            Order.objects.filter(pk=highlight_order_id, pharmacy_id__in=pharmacy_ids)
            .only("id", "status", "linked_prescription_id")
            .first()
        )
        if focus and (
            focus.linked_prescription_id or focus.status == Order.Status.AWAITING_RX
        ):
            return redirect("bo_pharmacy_prescription_detail", pk=focus.pk)
    awaiting_rx_count = (
        Order.objects.filter(pharmacy_id__in=pharmacy_ids, status=Order.Status.AWAITING_RX).count()
        if pharmacy_ids
        else 0
    )
    from core.pharmacy_order_workflow import PHARMACY_MANUAL_ORDER_STATUSES

    status_choices = [
        c
        for c in Order.Status.choices
        if c[0]
        not in {
            Order.Status.CART,
            Order.Status.AWAITING_RX,
            Order.Status.CANCELLED,
            Order.Status.DELIVERING,
            *PHARMACY_MANUAL_ORDER_STATUSES,
        }
    ]
    return render(
        request,
        "backoffice/pharmacy/orders.html",
        _ctx(
            request,
            "orders",
            orders=orders,
            pharmacy=pharmacy,
            pharmacies=pharmacies,
            awaiting_rx_count=awaiting_rx_count,
            statuses=status_choices,
            refusal_reasons=PHARMACY_REFUSAL_REASONS,
            order_filter=order_filter,
            order_kpis=order_kpis,
            search_q=request.GET.get("q", "").strip(),
            search_status=request.GET.get("status", "").strip(),
            search_date_from=request.GET.get("date_from", "").strip(),
            search_date_to=request.GET.get("date_to", "").strip(),
            search_delivery=request.GET.get("delivery_mode", "").strip(),
            search_payment=request.GET.get("payment", "").strip(),
            search_urgent=request.GET.get("urgent") == "1",
            orders_count=orders_count,
            order_filter_extra=order_filter_extra,
            all_statuses=Order.Status.choices,
            clients=User.objects.filter(role=User.Role.CLIENT).order_by("-date_joined")[:40],
            pharmacy_stocks=PharmacyStock.objects.filter(pharmacy=pharmacy, quantity__gt=0)
            .select_related("medicine")[:80]
            if pharmacy
            else [],
            prep_sla_minutes=PREP_SLA_MINUTES,
            highlight_order_id=highlight_order_id,
            payment_methods=Payment.Method.choices,
            can_validate_rx=has_pharmacy_permission(request.user, pharmacy, PERM_RX) if pharmacy else False,
        ),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_ORDERS)
def pharmacy_order_qr(request, pk):
    pharmacy = _pharmacy_for(request.user, request)
    order = get_object_or_404(Order, pk=pk, pharmacy=pharmacy)
    if not order_needs_pharmacy_handoff(order):
        return HttpResponse(status=404)
    ensure_pharmacy_handoff_code(order)
    return HttpResponse(render_traceability_qr_png(order), content_type="image/png")


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_ORDERS)
def pharmacy_order_print(request, pk):
    pharmacy = _pharmacy_for(request.user, request)
    order = get_object_or_404(
        Order.objects.prefetch_related("items", "payments"),
        pk=pk,
        pharmacy=pharmacy,
    )
    if order_needs_pharmacy_handoff(order):
        ensure_pharmacy_handoff_code(order)
    return render(
        request,
        "backoffice/pharmacy/order_print.html",
        _ctx(request, "orders", order=order),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_STOCKS)
def pharmacy_stocks(request):
    pharmacy = _pharmacy_for(request.user, request)

    if request.GET.get("export") == "csv" and pharmacy:
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="inventaire-{pharmacy.slug}.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["Médicament", "Catégorie", "Stock", "Seuil min", "Prix vente F", "Prix achat F", "Lot", "Expiration", "Statut"]
        )
        for s in PharmacyStock.objects.filter(pharmacy=pharmacy).select_related("medicine__category"):
            writer.writerow(
                [
                    str(s.medicine),
                    s.medicine.category.name if s.medicine.category_id else "",
                    s.quantity,
                    s.low_stock_threshold,
                    s.price,
                    s.purchase_price or "",
                    s.lot_number,
                    s.expiry_date.isoformat() if s.expiry_date else "",
                    s.availability_label,
                ]
            )
        return response

    if request.method == "POST" and pharmacy:
        action = request.POST.get("action")
        if action == "start_inventory":
            try:
                start_inventory_session(
                    pharmacy, request.user, request.POST.get("inventory_note", "")
                )
                messages.success(request, "Inventaire démarré — saisissez les quantités comptées.")
            except InventoryError as exc:
                messages.error(request, str(exc))
            return redirect(f"{reverse('bo_pharmacy_stocks')}?tab=inventory")
        elif action == "save_inventory":
            session = active_inventory_session(pharmacy)
            if not session:
                messages.error(request, "Aucun inventaire en cours.")
            else:
                counts = {}
                for key, val in request.POST.items():
                    if key.startswith("counted_"):
                        counts[key.replace("counted_", "")] = val
                save_inventory_counts(session, counts)
                messages.success(request, "Comptages enregistrés.")
            return redirect(f"{reverse('bo_pharmacy_stocks')}?tab=inventory")
        elif action == "complete_inventory":
            session = active_inventory_session(pharmacy)
            if not session:
                messages.error(request, "Aucun inventaire en cours.")
            else:
                try:
                    applied = complete_inventory_session(session, request.user)
                    messages.success(
                        request,
                        f"Inventaire validé — {applied} écart(s) appliqué(s) au stock.",
                    )
                except InventoryError as exc:
                    messages.error(request, str(exc))
            return redirect(f"{reverse('bo_pharmacy_stocks')}?tab=inventory")
        elif action == "cancel_inventory":
            session = active_inventory_session(pharmacy)
            if session:
                try:
                    cancel_inventory_session(session, request.user)
                    messages.success(request, "Inventaire annulé.")
                except InventoryError as exc:
                    messages.error(request, str(exc))
            return redirect(f"{reverse('bo_pharmacy_stocks')}?tab=inventory")
        elif action == "quick_movement":
            s = get_object_or_404(PharmacyStock, pk=request.POST.get("stock_id"), pharmacy=pharmacy)
            mtype = request.POST.get("movement_type")
            try:
                qty = int(request.POST.get("quantity") or 0)
            except ValueError:
                qty = 0
            try:
                if mtype not in {
                    StockMovement.MovementType.IN,
                    StockMovement.MovementType.OUT,
                }:
                    raise InventoryError("Type de mouvement invalide.")
                quick_stock_movement(
                    s,
                    mtype,
                    qty,
                    request.user,
                    request.POST.get("movement_note", "").strip(),
                )
                messages.success(request, f"Mouvement enregistré — {s.medicine.name}.")
            except InventoryError as exc:
                messages.error(request, str(exc))
            return _pharmacy_stock_redirect(request)
        elif action == "import_csv":
            upload = request.FILES.get("csv_file")
            if not upload:
                messages.error(request, "Choisissez un fichier CSV.")
            else:
                try:
                    result = import_stock_csv(pharmacy, request.user, upload)
                    messages.success(
                        request,
                        f"Import terminé — {result['updated']} ligne(s) mise(s) à jour, "
                        f"{result['skipped']} ignorée(s).",
                    )
                except InventoryError as exc:
                    messages.error(request, str(exc))
            return _pharmacy_stock_redirect(request)
        elif action == "create_product":
            # Pharmacie crée médicament (+ catégorie) si absent du catalogue
            name = request.POST.get("new_name", "").strip()
            dosage = request.POST.get("new_dosage", "").strip()
            contenance = _contenance_from_post(request)
            if not name:
                messages.error(request, "Nom du médicament obligatoire.")
            elif not contenance:
                messages.error(request, "Indiquez une contenance (ex. 300 ml).")
            else:
                cat = None
                cat_id = request.POST.get("category_id") or ""
                new_cat = request.POST.get("new_category", "").strip()
                if new_cat:
                    cat = Category.objects.create(
                        name=new_cat,
                        slug=_unique_category_slug(new_cat),
                        is_active=True,
                        order=99,
                    )
                elif cat_id:
                    cat = Category.objects.filter(pk=cat_id).first()
                med_slug = _unique_medicine_slug(f"{name}-{contenance}-{pharmacy.code}")
                medicine = Medicine.objects.create(
                    name=name,
                    slug=med_slug,
                    dosage=dosage,
                    contenance=contenance,
                    tagline=request.POST.get("tagline", "").strip(),
                    dci=request.POST.get("new_dci", "").strip(),
                    laboratory=request.POST.get("new_laboratory", "").strip(),
                    form=request.POST.get("new_form", Medicine.Form.TABLET),
                    category=cat,
                    created_by_pharmacy=pharmacy,
                    requires_prescription=request.POST.get("requires_prescription") == "on",
                    barcode=request.POST.get("barcode", "").strip(),
                    description=request.POST.get("description", "").strip(),
                    presentation=request.POST.get("presentation", "").strip(),
                    composition=request.POST.get("composition", "").strip(),
                    usage_advice=request.POST.get("usage_advice", "").strip(),
                    pharmacist_advice=request.POST.get("pharmacist_advice", "").strip(),
                    recommended_by=request.POST.get("recommended_by", "").strip(),
                )
                if _set_image(medicine, "image", request.FILES):
                    medicine.save(update_fields=["image"])
                try:
                    price = int(request.POST.get("price") or 0)
                    qty = int(request.POST.get("quantity") or 0)
                except ValueError:
                    price, qty = 0, 0
                stock = PharmacyStock.objects.create(
                    pharmacy=pharmacy,
                    medicine=medicine,
                    quantity=max(0, qty),
                    price=max(price, 1),
                    lot_number=request.POST.get("lot_number", "").strip(),
                    expiry_date=request.POST.get("expiry_date") or None,
                    low_stock_threshold=int(request.POST.get("low_stock_threshold") or 10),
                    is_visible=request.POST.get("is_visible") == "on",
                    pharmacist_advice=request.POST.get("stock_pharmacist_advice", "").strip(),
                    pharmacist_name=request.POST.get("pharmacist_name", "").strip(),
                    pharmacist_title=request.POST.get("pharmacist_title", "Docteur en pharmacie").strip(),
                    pharmacist_rpps=request.POST.get("pharmacist_rpps", "").strip(),
                    delivery_promise=request.POST.get("delivery_promise", "").strip(),
                )
                _log_stock_movement(stock, StockMovement.MovementType.IN, qty, request.user, "Création fiche + entrée stock")
                messages.success(request, f"{medicine} créé et ajouté à votre stock.")
        elif action == "create":
            medicine = Medicine.objects.filter(pk=request.POST.get("medicine_id")).first()
            if not medicine:
                messages.error(request, "Choisissez un médicament du catalogue, ou créez-en un nouveau.")
            elif PharmacyStock.objects.filter(pharmacy=pharmacy, medicine=medicine).exists():
                messages.error(request, "Ce médicament est déjà en stock — modifiez la ligne existante.")
            else:
                try:
                    price = int(request.POST.get("price") or 0)
                    qty = int(request.POST.get("quantity") or 0)
                    threshold = int(request.POST.get("low_stock_threshold") or 10)
                except ValueError:
                    price, qty, threshold = 0, 0, 10
                if price <= 0:
                    messages.error(request, "Indiquez un prix en FCFA.")
                else:
                    stock = PharmacyStock.objects.create(
                        pharmacy=pharmacy,
                        medicine=medicine,
                        quantity=max(0, qty),
                        price=price,
                        lot_number=request.POST.get("lot_number", "").strip(),
                        expiry_date=request.POST.get("expiry_date") or None,
                        low_stock_threshold=threshold,
                        is_visible=request.POST.get("is_visible") == "on",
                    )
                    _log_stock_movement(stock, StockMovement.MovementType.IN, qty, request.user, "Ajout catalogue")
                    messages.success(request, f"{medicine} ajouté au stock.")
        elif action == "update":
            s = get_object_or_404(PharmacyStock, pk=request.POST.get("stock_id"), pharmacy=pharmacy)
            old_qty = s.quantity
            s.quantity = int(request.POST.get("quantity") or s.quantity)
            s.price = int(request.POST.get("price") or s.price)
            purchase = request.POST.get("purchase_price")
            s.purchase_price = int(purchase) if purchase else None
            promo = request.POST.get("promotional_price")
            s.promotional_price = int(promo) if promo else None
            s.lot_number = request.POST.get("lot_number", s.lot_number)
            s.expiry_date = request.POST.get("expiry_date") or None
            s.low_stock_threshold = int(request.POST.get("low_stock_threshold") or s.low_stock_threshold)
            s.is_visible = request.POST.get("is_visible") == "on"
            s.pharmacist_advice = request.POST.get("pharmacist_advice", s.pharmacist_advice).strip()
            s.save()
            delta = s.quantity - old_qty
            if delta:
                mtype = (
                    StockMovement.MovementType.IN
                    if delta > 0
                    else StockMovement.MovementType.OUT
                )
                _log_stock_movement(s, mtype, delta, request.user, "Mise à jour manuelle")
            from core.pharmacy_notifications import check_stock_alert

            check_stock_alert(s, previous_qty=old_qty)
            messages.success(request, "Stock mis à jour.")
        elif action == "delete":
            s = get_object_or_404(PharmacyStock, pk=request.POST.get("stock_id"), pharmacy=pharmacy)
            name = str(s.medicine)
            s.delete()
            messages.success(request, f"{name} retiré du stock.")
        return _pharmacy_stock_redirect(request)

    tab = request.GET.get("tab", "all")
    q = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "").strip()
    visibility = request.GET.get("visibility", "").strip()
    sort = request.GET.get("sort", "name")
    if sort not in {"name", "price_asc", "price_desc", "qty_asc", "qty_desc"}:
        sort = "name"

    qs = (
        PharmacyStock.objects.filter(pharmacy=pharmacy)
        .select_related("medicine", "medicine__category")
        if pharmacy
        else PharmacyStock.objects.none()
    )
    if q:
        qs = qs.filter(
            Q(medicine__name__icontains=q)
            | Q(medicine__dci__icontains=q)
            | Q(medicine__laboratory__icontains=q)
            | Q(lot_number__icontains=q)
        )
    if category_id:
        qs = qs.filter(medicine__category_id=category_id)
    if visibility == "visible":
        qs = qs.filter(is_visible=True)
    elif visibility == "hidden":
        qs = qs.filter(is_visible=False)

    if sort == "price_asc":
        qs = qs.order_by("price", "medicine__name")
    elif sort == "price_desc":
        qs = qs.order_by("-price", "medicine__name")
    elif sort == "qty_asc":
        qs = qs.order_by("quantity", "medicine__name")
    elif sort == "qty_desc":
        qs = qs.order_by("-quantity", "medicine__name")
    else:
        qs = qs.order_by("medicine__name")

    today = timezone.localdate()
    soon = today + timedelta(days=60)
    all_rows = list(qs)
    low_rows = [s for s in all_rows if 0 < s.quantity <= s.low_stock_threshold]
    out_rows = [s for s in all_rows if s.quantity <= 0]
    expiring_rows = [s for s in all_rows if s.expiry_date and today <= s.expiry_date <= soon]
    counts = {
        "all": len(all_rows),
        "low": len(low_rows),
        "out": len(out_rows),
        "expiring": len(expiring_rows),
    }
    if tab == "low":
        stocks = low_rows
    elif tab == "out":
        stocks = out_rows
    elif tab == "expiring":
        stocks = expiring_rows
    elif tab in {"inventory", "movements"}:
        stocks = []
    else:
        stocks = all_rows

    inventory_session = active_inventory_session(pharmacy)
    inventory_lines = []
    if inventory_session:
        inventory_lines = list(
            inventory_session.lines.select_related("stock__medicine").order_by("stock__medicine__name")
        )
    past_inventories = (
        StockInventorySession.objects.filter(pharmacy=pharmacy)
        .exclude(status=StockInventorySession.Status.IN_PROGRESS)
        .select_related("started_by", "completed_by")[:10]
        if pharmacy
        else []
    )

    movements_qs = (
        StockMovement.objects.filter(stock__pharmacy=pharmacy)
        .select_related("stock__medicine", "created_by")
        if pharmacy
        else StockMovement.objects.none()
    )
    movements_page = paginate(request, movements_qs, 25) if tab == "movements" else None

    existing_ids = list(
        PharmacyStock.objects.filter(pharmacy=pharmacy).values_list("medicine_id", flat=True)
    ) if pharmacy else []
    available_medicines = Medicine.objects.exclude(id__in=existing_ids).order_by("name")[:300]
    categories = Category.objects.filter(is_active=True).order_by("order", "name")
    recent_movements = (
        StockMovement.objects.filter(stock__pharmacy=pharmacy)
        .select_related("stock__medicine", "created_by")[:15]
        if pharmacy
        else []
    )

    return render(
        request,
        "backoffice/pharmacy/stocks.html",
        _ctx(
            request,
            "stocks",
            pharmacy=pharmacy,
            stocks=stocks,
            tab=tab,
            counts=counts,
            available_medicines=available_medicines,
            show_create=request.GET.get("new") == "1",
            create_mode=request.GET.get("mode", "catalog"),  # catalog | product
            today=today,
            q=q,
            filter_category=category_id,
            filter_visibility=visibility,
            sort=sort,
            categories=categories,
            forms=Medicine.Form.choices,
            recent_movements=recent_movements,
            contenance_presets=CONTENANCE_PRESETS,
            inventory_session=inventory_session,
            inventory_lines=inventory_lines,
            past_inventories=past_inventories,
            movements_page=movements_page,
            page_obj=movements_page,
            movement_types=StockMovement.MovementType.choices,
        ),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_STOCKS)
def pharmacy_medicine_edit(request, stock_id):
    """Fiche produit enrichie (présentation, composition, Q&R…) visible sur le site client."""
    pharmacy = _pharmacy_for(request.user, request)
    stock = get_object_or_404(
        PharmacyStock.objects.select_related("medicine", "medicine__category"),
        pk=stock_id,
        pharmacy=pharmacy,
    )
    medicine = stock.medicine
    questions = MedicineQuestion.objects.filter(
        medicine__name=medicine.name, is_published=True
    ).order_by("order", "id")
    variant_stocks = (
        PharmacyStock.objects.filter(
            pharmacy=pharmacy,
            medicine__name=medicine.name,
            is_visible=True,
        )
        .select_related("medicine")
        .order_by("medicine__contenance", "medicine__dosage")
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_medicine":
            medicine.tagline = request.POST.get("tagline", "").strip()
            medicine.description = request.POST.get("description", "").strip()
            medicine.presentation = request.POST.get("presentation", "").strip()
            medicine.composition = request.POST.get("composition", "").strip()
            medicine.usage_advice = request.POST.get("usage_advice", "").strip()
            medicine.pharmacist_advice = request.POST.get("pharmacist_advice", "").strip()
            medicine.recommended_by = request.POST.get("recommended_by", "").strip()
            medicine.barcode = request.POST.get("barcode", medicine.barcode).strip()
            medicine.form = request.POST.get("form", medicine.form)
            contenance = _contenance_from_post(request) or medicine.contenance
            medicine.contenance = contenance
            if _set_image(medicine, "image", request.FILES):
                pass
            medicine.save()
            _sync_medicine_product_line(
                medicine,
                [
                    "tagline",
                    "description",
                    "presentation",
                    "composition",
                    "usage_advice",
                    "pharmacist_advice",
                    "recommended_by",
                    "form",
                ],
            )
            stock.pharmacist_advice = request.POST.get("stock_pharmacist_advice", "").strip()
            stock.pharmacist_name = request.POST.get("pharmacist_name", "").strip()
            stock.pharmacist_title = request.POST.get(
                "pharmacist_title", "Docteur en pharmacie"
            ).strip()
            stock.pharmacist_rpps = request.POST.get("pharmacist_rpps", "").strip()
            stock.delivery_promise = request.POST.get("delivery_promise", "").strip()
            stock.save(
                update_fields=[
                    "pharmacist_advice",
                    "pharmacist_name",
                    "pharmacist_title",
                    "pharmacist_rpps",
                    "delivery_promise",
                    "updated_at",
                ]
            )
            messages.success(request, "Fiche produit enregistrée.")
        elif action == "add_variant":
            contenance = _contenance_from_post(request)
            if not contenance:
                messages.error(request, "Choisissez une contenance pour la variante.")
            elif Medicine.objects.filter(name=medicine.name, contenance=contenance).exists():
                messages.error(request, f"La contenance « {contenance} » existe déjà pour ce produit.")
            else:
                try:
                    price = int(request.POST.get("variant_price") or 0)
                    qty = int(request.POST.get("variant_quantity") or 0)
                except ValueError:
                    price, qty = 0, 0
                if price <= 0:
                    messages.error(request, "Indiquez un prix pour la variante.")
                else:
                    new_med = Medicine.objects.create(
                        name=medicine.name,
                        slug=_unique_medicine_slug(f"{medicine.name}-{contenance}-{pharmacy.code}"),
                        dosage=medicine.dosage,
                        contenance=contenance,
                        tagline=medicine.tagline,
                        dci=medicine.dci,
                        laboratory=medicine.laboratory,
                        form=medicine.form,
                        category=medicine.category,
                        created_by_pharmacy=medicine.created_by_pharmacy or pharmacy,
                        requires_prescription=medicine.requires_prescription,
                        barcode=medicine.barcode,
                        description=medicine.description,
                        presentation=medicine.presentation,
                        composition=medicine.composition,
                        usage_advice=medicine.usage_advice,
                        pharmacist_advice=medicine.pharmacist_advice,
                        recommended_by=medicine.recommended_by,
                        image=medicine.image,
                    )
                    new_stock = PharmacyStock.objects.create(
                        pharmacy=pharmacy,
                        medicine=new_med,
                        quantity=max(0, qty),
                        price=price,
                        is_visible=True,
                        pharmacist_advice=stock.pharmacist_advice,
                        pharmacist_name=stock.pharmacist_name,
                        pharmacist_title=stock.pharmacist_title,
                        pharmacist_rpps=stock.pharmacist_rpps,
                        delivery_promise=stock.delivery_promise,
                    )
                    _log_stock_movement(
                        new_stock, StockMovement.MovementType.IN, qty, request.user, "Variante contenance"
                    )
                    messages.success(request, f"Variante {contenance} ajoutée.")
                    return redirect("bo_pharmacy_medicine_edit", stock_id=new_stock.id)
        elif action == "add_question":
            q_text = request.POST.get("question", "").strip()
            a_text = request.POST.get("answer", "").strip()
            if q_text and a_text:
                MedicineQuestion.objects.create(
                    medicine=medicine,
                    question=q_text,
                    answer=a_text,
                    order=int(request.POST.get("order") or 0),
                    is_published=True,
                )
                messages.success(request, "Question ajoutée.")
        elif action == "delete_question":
            q = get_object_or_404(MedicineQuestion, pk=request.POST.get("question_id"), medicine=medicine)
            q.delete()
            messages.success(request, "Question supprimée.")
        return redirect("bo_pharmacy_medicine_edit", stock_id=stock.id)

    return render(
        request,
        "backoffice/pharmacy/medicine_edit.html",
        _ctx(
            request,
            "stocks",
            pharmacy=pharmacy,
            stock=stock,
            medicine=medicine,
            questions=questions,
            variant_stocks=variant_stocks,
            contenance_presets=CONTENANCE_PRESETS,
            forms=Medicine.Form.choices,
            preview_url=reverse("product_detail", args=[stock.id]),
        ),
    )


# ─── Livreur ───────────────────────────────────────────────────────
def _courier_available_deliveries(profile):
    repair_stale_delivering_orders()
    qs = Delivery.objects.filter(
        status=Delivery.Status.PENDING,
        courier__isnull=True,
        order__status=Order.Status.READY,
    ).select_related("order", "order__pharmacy", "order__client")
    if profile.pharmacy_id:
        qs = qs.filter(order__pharmacy_id=profile.pharmacy_id)
    return qs.order_by("created_at")


@role_required(*courier_roles)
@portal_permission_required("dashboard")
def courier_dashboard(request):
    profile = _ensure_courier_profile(request.user)

    repair_stale_delivering_orders()
    deliveries = Delivery.objects.filter(courier=request.user).select_related(
        "order", "order__pharmacy", "order__client"
    )
    today = timezone.now().date()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "toggle_status":
            if profile.courier_status == CourierProfile.CourierStatus.ONLINE:
                profile.courier_status = CourierProfile.CourierStatus.OFFLINE
                profile.save(update_fields=["courier_status"])
            else:
                from core.courier_portal import courier_eligibility_message, courier_must_complete_documents

                if courier_must_complete_documents(profile):
                    messages.error(
                        request,
                        courier_eligibility_message(profile)
                        + " Rendez-vous dans Véhicules & documents.",
                    )
                    return redirect("bo_courier_vehicles")
                try:
                    require_gps_from_post(request)
                except CourierGpsRequired as exc:
                    messages.error(request, str(exc))
                    return redirect("bo_courier_dashboard")
                profile.courier_status = CourierProfile.CourierStatus.ONLINE
                profile.save(update_fields=["courier_status"])
            messages.success(request, f"Statut : {profile.get_courier_status_display()}")
        elif action == "claim":
            from core.courier_portal import courier_eligibility_message, courier_must_complete_documents

            if courier_must_complete_documents(profile):
                messages.error(request, courier_eligibility_message(profile))
                return redirect("bo_courier_vehicles")
            if profile.courier_status == CourierProfile.CourierStatus.OFFLINE:
                messages.error(request, "Passez en ligne pour prendre une course.")
            else:
                claim_qs = _courier_available_deliveries(profile)
                d = claim_qs.filter(pk=request.POST.get("delivery_id")).first()
                if not d:
                    messages.error(request, "Course indisponible.")
                else:
                    try:
                        require_gps_from_post(request, delivery=d)
                    except CourierGpsRequired as exc:
                        messages.error(request, str(exc))
                        return redirect("bo_courier_dashboard")
                    d.courier = request.user
                    d.status = Delivery.Status.ASSIGNED
                    d.save(update_fields=["courier", "status", "updated_at", "courier_lat", "courier_lng"])
                    profile.courier_status = CourierProfile.CourierStatus.BUSY
                    profile.save(update_fields=["courier_status"])
                    courier_name = (
                        request.user.get_full_name() or request.user.username
                    )
                    _notify_client(
                        d.order,
                        f"Livreur assigné — {d.order.code}",
                        f"{courier_name} a pris en charge votre livraison.",
                        {"event": "courier_assigned", "delivery_id": d.id},
                    )
                    notify_pharmacy_courier_assigned(d)
                    messages.success(request, f"Course {d.order.code} prise en charge.")
        elif action == "update_gps":
            lat = _parse_decimal(request.POST.get("latitude"))
            lng = _parse_decimal(request.POST.get("longitude"))
            if lat is not None and lng is not None:
                request.user.latitude = lat
                request.user.longitude = lng
                request.user.save(update_fields=["latitude", "longitude", "updated_at"])
                messages.success(request, "Position GPS mise à jour.")
            else:
                messages.error(request, "Coordonnées GPS invalides.")
        elif action == "accept_handoff":
            d = get_object_or_404(Delivery, pk=request.POST.get("delivery_id"))
            try:
                require_gps_from_post(request, delivery=d)
            except CourierGpsRequired as exc:
                messages.error(request, str(exc))
                return redirect("bo_courier_dashboard")
            try:
                accept_handoff_offer(d, request.user)
                messages.success(request, f"Reprise acceptée — {d.order.code}. Récupérez le colis et saisissez le code de transfert.")
            except ValueError as exc:
                messages.error(request, str(exc))
        elif action == "validate_handoff":
            d = get_object_or_404(Delivery, pk=request.POST.get("delivery_id"))
            try:
                validate_handoff_code(d, request.user, request.POST.get("transfer_code", ""))
                messages.success(request, f"Transfert validé — {d.order.code} est maintenant sous votre responsabilité.")
            except ValueError as exc:
                messages.error(request, str(exc))
        return redirect("bo_courier_dashboard")

    stats = {
        "today": deliveries.filter(created_at__date=today).count(),
        "in_progress": deliveries.filter(
            status__in=[
                Delivery.Status.ASSIGNED,
                Delivery.Status.PICKING_UP,
                Delivery.Status.PICKED_UP,
                Delivery.Status.IN_TRANSIT,
            ]
        ).count(),
        "done_today": deliveries.filter(
            status=Delivery.Status.DELIVERED, delivered_at__date=today
        ).count(),
        "pending": deliveries.filter(status=Delivery.Status.PENDING).count(),
        "total": profile.total_deliveries or deliveries.filter(status=Delivery.Status.DELIVERED).count(),
        "rating": profile.rating,
        "level": profile.get_level_display(),
    }
    active = deliveries.filter(
        status__in=[
            Delivery.Status.ASSIGNED,
            Delivery.Status.PICKING_UP,
            Delivery.Status.PICKED_UP,
            Delivery.Status.IN_TRANSIT,
        ]
    ).order_by("-updated_at")[:10]
    available = []
    if profile.courier_status != CourierProfile.CourierStatus.OFFLINE:
        available = _courier_available_deliveries(profile)[:15]
    handoff_offers = handoff_offers_for_courier(request.user)
    pending_handoffs = pending_handoff_validations(request.user)
    from core.courier_portal import (
        courier_active_urgent_delivery,
        courier_eligibility,
        urgent_mission_context,
    )
    from core.payment_settlement import courier_earnings_summary
    from payments.models import CourierEarning

    urgent_delivery = courier_active_urgent_delivery(request.user)
    urgent_ctx = urgent_mission_context(urgent_delivery) if urgent_delivery else None

    since_month = timezone.now() - timedelta(days=30)
    earnings = courier_earnings_summary(request.user, since=since_month)
    recent_earnings = (
        CourierEarning.objects.filter(courier=request.user)
        .select_related("delivery", "delivery__order")
        .order_by("-created_at")[:8]
    )
    return render(
        request,
        "backoffice/courier/dashboard.html",
        _ctx(
            request,
            "dashboard",
            stats=stats,
            active=active,
            available=available,
            handoff_offers=handoff_offers,
            pending_handoffs=pending_handoffs,
            earnings=earnings,
            recent_earnings=recent_earnings,
            profile=profile,
            eligibility=courier_eligibility(profile),
            urgent_mission=urgent_ctx,
            next_labels={
                Delivery.Status.ASSIGNED: "Partir en retrait",
                Delivery.Status.PICKING_UP: "Colis retiré",
                Delivery.Status.PICKED_UP: "En route client",
                Delivery.Status.IN_TRANSIT: "Livrer (code)",
            },
        ),
    )


@role_required(*courier_roles)
@portal_permission_required("deliveries")
def courier_deliveries(request):
    profile = _ensure_courier_profile(request.user)

    if request.method == "POST":
        action = request.POST.get("action")
        delivery_id = request.POST.get("delivery_id")

        if action == "accept_handoff":
            delivery = get_object_or_404(Delivery, pk=delivery_id)
            try:
                require_gps_from_post(request, delivery=delivery)
            except CourierGpsRequired as exc:
                messages.error(request, str(exc))
                return redirect("bo_courier_deliveries")
            try:
                accept_handoff_offer(delivery, request.user)
                messages.success(
                    request,
                    f"Reprise acceptée — {delivery.order.code}. Saisissez le code remis sur place.",
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("bo_courier_deliveries")

        if action == "validate_handoff":
            delivery = get_object_or_404(Delivery, pk=delivery_id)
            try:
                validate_handoff_code(
                    delivery, request.user, request.POST.get("transfer_code", "")
                )
                messages.success(
                    request,
                    f"Transfert validé — {delivery.order.code} vous est assignée.",
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("bo_courier_deliveries")

        delivery = get_object_or_404(Delivery, pk=delivery_id, courier=request.user)

        if action == "validate_pharmacy_pickup":
            if delivery.status != Delivery.Status.PICKING_UP:
                msg = "Cette étape n'est plus disponible pour cette livraison."
                ajax = _courier_ajax_json(request, ok=False, message=msg)
                if ajax:
                    return ajax
                messages.error(request, msg)
            else:
                order = delivery.order
                if order_needs_pharmacy_handoff(order):
                    try:
                        resolve_courier_pharmacy_pickup(
                            order,
                            manual_code=request.POST.get("pharmacy_handoff_code"),
                            qr_raw=request.POST.get("pharmacy_qr_payload"),
                        )
                        msg = f"Code validé — vous pouvez récupérer la commande {order.code}."
                        ajax = _courier_ajax_json(request, ok=True, message=msg)
                        if ajax:
                            return ajax
                        messages.success(request, msg)
                    except HandoffError as exc:
                        ajax = _courier_ajax_json(request, ok=False, message=str(exc))
                        if ajax:
                            return ajax
                        messages.error(request, str(exc))
                else:
                    msg = "Aucun code requis pour cette commande."
                    ajax = _courier_ajax_json(request, ok=True, message=msg)
                    if ajax:
                        return ajax
                    messages.info(request, msg)
            return redirect(f"{reverse('bo_courier_deliveries')}#delivery-{delivery.id}")

        if action == "validate_client_code":
            if delivery.status != Delivery.Status.IN_TRANSIT:
                msg = "Validez le code une fois arrivé chez le client."
                ajax = _courier_ajax_json(request, ok=False, message=msg)
                if ajax:
                    return ajax
                messages.error(request, msg)
            else:
                manual = (
                    request.POST.get("validation_code")
                    or request.POST.get("validation_code_manual")
                    or ""
                ).strip()
                try:
                    resolve_courier_delivery_validation(
                        delivery,
                        manual_code=manual,
                        qr_raw=request.POST.get("client_qr_payload"),
                    )
                    request.session[f"courier_code_ok_{delivery.id}"] = True
                    msg = "Code valide — faites signer le client puis terminez la livraison."
                    ajax = _courier_ajax_json(request, ok=True, message=msg)
                    if ajax:
                        return ajax
                    messages.success(request, f"Code client validé pour {delivery.order.code}.")
                except DeliveryValidationError as exc:
                    ajax = _courier_ajax_json(request, ok=False, message=str(exc))
                    if ajax:
                        return ajax
                    messages.error(request, str(exc))
            return redirect(f"{reverse('bo_courier_deliveries')}#delivery-{delivery.id}")

        if action == "advance":
            nxt = _courier_next_status(delivery.status)
            if not nxt:
                messages.error(request, "Aucun statut suivant pour cette livraison.")
            elif nxt == Delivery.Status.DELIVERED:
                if delivery.status != Delivery.Status.IN_TRANSIT:
                    messages.error(request, "Confirmez d'abord que vous êtes en route chez le client.")
                else:
                    code_ok = request.session.get(f"courier_code_ok_{delivery.id}")
                    if not code_ok:
                        try:
                            resolve_courier_delivery_validation(
                                delivery,
                                manual_code=(
                                    request.POST.get("validation_code")
                                    or request.POST.get("validation_code_manual")
                                ),
                                qr_raw=request.POST.get("client_qr_payload"),
                            )
                        except DeliveryValidationError as exc:
                            messages.error(request, str(exc))
                            return redirect("bo_courier_deliveries")
                    delivery.status = Delivery.Status.DELIVERED
                    delivery.delivered_at = timezone.now()
                    delivery.save(update_fields=["status", "delivered_at", "updated_at"])
                    from core.delivery_transfer import log_delivery_step

                    method = "scan QR client" if request.POST.get("client_qr_payload") else "code saisi"
                    log_delivery_step(delivery, f"Livraison validée ({method})", delivery.status)
                    _sync_order_with_delivery(delivery)
                    profile.total_deliveries = (profile.total_deliveries or 0) + 1
                    profile.courier_status = CourierProfile.CourierStatus.ONLINE
                    profile.save(update_fields=["total_deliveries", "courier_status"])
                    messages.success(request, f"{delivery.order.code} livrée.")
                    request.session.pop(f"courier_code_ok_{delivery.id}", None)
            else:
                delivery.status = nxt
                fields = ["status", "updated_at"]
                if nxt == Delivery.Status.PICKED_UP:
                    order = delivery.order
                    if order_needs_pharmacy_handoff(order):
                        try:
                            resolve_courier_pharmacy_pickup(
                                order,
                                manual_code=request.POST.get("pharmacy_handoff_code"),
                                qr_raw=request.POST.get("pharmacy_qr_payload"),
                            )
                        except HandoffError as exc:
                            messages.error(request, str(exc))
                            return redirect("bo_courier_deliveries")
                    delivery.picked_up_at = timezone.now()
                    fields.append("picked_up_at")
                delivery.save(update_fields=fields)
                _sync_order_with_delivery(delivery)
                profile.courier_status = CourierProfile.CourierStatus.BUSY
                profile.save(update_fields=["courier_status"])
                messages.success(request, f"{delivery.order.code} → {delivery.get_status_display()}")

        elif action == "fail":
            delivery.status = Delivery.Status.FAILED
            delivery.save(update_fields=["status", "updated_at"])
            _sync_order_with_delivery(delivery)
            profile.courier_status = CourierProfile.CourierStatus.ONLINE
            profile.save(update_fields=["courier_status"])
            messages.success(request, f"{delivery.order.code} marquée comme échouée.")

        elif action == "request_handoff":
            itype = request.POST.get("incident_type") or DeliveryIncident.Type.OTHER
            if itype not in dict(DeliveryIncident.Type.choices):
                itype = DeliveryIncident.Type.OTHER
            priority = request.POST.get("priority") or DeliveryIncident.Priority.MEDIUM
            try:
                code, _incident = request_delivery_handoff(
                    delivery,
                    request.user,
                    incident_type=itype,
                    description=request.POST.get("description", "").strip(),
                    priority=priority,
                )
                messages.success(
                    request,
                    f"Demande de transfert envoyée. Code à communiquer au remplaçant : {code}",
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            if request.POST.get("return_to") == "incident":
                return redirect(
                    f"{reverse('bo_courier_incident', args=[delivery.id])}?tab=transfer"
                )

        elif action == "incident":
            from core.incident_admin import incident_priority_for_type, notify_support_on_incident

            itype = request.POST.get("incident_type") or DeliveryIncident.Type.OTHER
            if itype not in dict(DeliveryIncident.Type.choices):
                itype = DeliveryIncident.Type.OTHER
            priority = incident_priority_for_type(itype)
            incident = DeliveryIncident.objects.create(
                delivery=delivery,
                reported_by=request.user,
                incident_type=itype,
                description=request.POST.get("description", "").strip(),
                priority=priority,
            )
            if request.FILES.get("photo"):
                incident.photo = request.FILES["photo"]
                incident.save(update_fields=["photo"])
            notify_support_on_incident(incident)
            messages.success(request, "Incident déclaré — le support a été notifié.")
            if request.POST.get("return_to") == "incident":
                return redirect(
                    f"{reverse('bo_courier_incident', args=[delivery.id])}?tab=incident"
                )

        elif action == "share_gps":
            lat = _parse_decimal(request.POST.get("latitude"))
            lng = _parse_decimal(request.POST.get("longitude"))
            if lat is not None and lng is not None:
                delivery.courier_lat = lat
                delivery.courier_lng = lng
                delivery.save(update_fields=["courier_lat", "courier_lng", "updated_at"])
                request.user.latitude = lat
                request.user.longitude = lng
                request.user.save(update_fields=["latitude", "longitude", "updated_at"])
                messages.success(request, "Position partagée sur la livraison.")
            else:
                messages.error(request, "GPS invalide.")

        return redirect("bo_courier_deliveries")

    from core.order_tracking import build_order_tracking_payload

    deliveries = list(
        Delivery.objects.filter(courier=request.user)
        .select_related("order", "order__pharmacy", "order__client")
        .prefetch_related("order__items", "order__payments")
        .order_by("-created_at")[:40]
    )
    for delivery in deliveries:
        delivery.track_script_id = f"courier-track-{delivery.id}"
        delivery.client_code_prevalidated = bool(
            request.session.get(f"courier_code_ok_{delivery.id}")
        )
        if delivery.status in {
            Delivery.Status.PICKED_UP,
            Delivery.Status.IN_TRANSIT,
        }:
            delivery.tracking_payload = build_order_tracking_payload(
                delivery.order, delivery
            )
        else:
            delivery.tracking_payload = None

    return render(
        request,
        "backoffice/courier/deliveries.html",
        _ctx(
            request,
            "deliveries",
            deliveries=deliveries,
            handoff_offers=handoff_offers_for_courier(request.user),
            pending_handoffs=pending_handoff_validations(request.user),
            incident_types=DeliveryIncident.Type.choices,
            next_labels={
                Delivery.Status.ASSIGNED: "Partir en retrait",
                Delivery.Status.PICKING_UP: "Colis retiré",
                Delivery.Status.PICKED_UP: "En route client",
                Delivery.Status.IN_TRANSIT: "Confirmer livraison",
            },
        ),
    )


@role_required(*courier_roles)
@portal_permission_required("profile")
def courier_settings(request):
    profile = _ensure_courier_profile(request.user)
    if request.method == "POST":
        if _apply_password_change(request, request.user):
            return redirect("bo_courier_settings")
        u = request.user
        u.first_name = request.POST.get("first_name", u.first_name)
        u.last_name = request.POST.get("last_name", u.last_name)
        u.email = request.POST.get("email", u.email)
        u.phone = request.POST.get("phone", u.phone)
        u.city = request.POST.get("city", u.city)
        u.district = request.POST.get("district", u.district)
        u.latitude = _parse_decimal(request.POST.get("latitude"))
        u.longitude = _parse_decimal(request.POST.get("longitude"))
        _set_image(u, "avatar", request.FILES)
        u.save()
        profile.zone = request.POST.get("zone", profile.zone).strip()
        profile.save()
        messages.success(request, "Profil livreur enregistré.")
        return redirect("bo_courier_settings")
    from core.courier_portal import courier_eligibility

    return render(
        request,
        "backoffice/courier/settings.html",
        _ctx(request, "courier_settings", profile=profile, eligibility=courier_eligibility(profile)),
    )


@role_required(*courier_roles)
@portal_permission_required("profile")
def courier_vehicles(request):
    profile = _ensure_courier_profile(request.user)
    from core.courier_portal import (
        courier_admin_document_cards,
        courier_eligibility,
        courier_vehicle_display,
    )

    if request.method == "POST":
        action = request.POST.get("action", "vehicle")
        if action == "vehicle":
            from core.courier_portal import apply_courier_vehicle_post

            apply_courier_vehicle_post(profile, request.POST)
            profile.save()
            messages.success(request, "Véhicule enregistré.")
        elif action == "documents":
            from core.courier_portal import apply_courier_documents_upload, courier_eligibility

            apply_courier_documents_upload(profile, request.FILES)
            profile.save()
            if courier_eligibility(profile)["documents_complete"]:
                if courier_eligibility(profile)["is_eligible"]:
                    messages.success(request, "Documents complets — livreur éligible aux missions.")
                else:
                    messages.success(
                        request,
                        "Documents complets — en attente de validation par l'administration.",
                    )
            else:
                messages.success(request, "Documents mis à jour.")
        return redirect("bo_courier_vehicles")
    return render(
        request,
        "backoffice/courier/vehicles.html",
        _ctx(
            request,
            "courier_vehicles",
            profile=profile,
            vehicle=courier_vehicle_display(profile),
            documents=courier_admin_document_cards(profile),
            eligibility=courier_eligibility(profile),
            active_tab=request.GET.get("tab", "vehicle"),
        ),
    )


@role_required(*courier_roles)
@portal_permission_required("deliveries")
def courier_incident(request, delivery_id):
    profile = _ensure_courier_profile(request.user)
    from core.courier_portal import INCIDENT_TYPE_MAP, INCIDENT_UI_TYPES, urgent_mission_context

    delivery = get_object_or_404(
        Delivery.objects.select_related("order", "order__pharmacy", "order__client").prefetch_related(
            "order__items", "order__payments"
        ),
        pk=delivery_id,
        courier=request.user,
    )
    if delivery.status in {Delivery.Status.DELIVERED, Delivery.Status.FAILED, Delivery.Status.TRANSFERRED}:
        messages.error(request, "Cette livraison est déjà terminée.")
        return redirect("bo_courier_deliveries")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "incident":
            from core.incident_admin import incident_priority_for_type, notify_support_on_incident

            raw_type = request.POST.get("incident_type") or "other"
            itype = INCIDENT_TYPE_MAP.get(raw_type, raw_type)
            if itype not in dict(DeliveryIncident.Type.choices):
                itype = DeliveryIncident.Type.OTHER
            priority = incident_priority_for_type(itype)
            incident = DeliveryIncident.objects.create(
                delivery=delivery,
                reported_by=request.user,
                incident_type=itype,
                description=request.POST.get("description", "").strip(),
                priority=priority,
            )
            if request.FILES.get("photo"):
                incident.photo = request.FILES["photo"]
                incident.save(update_fields=["photo"])
            notify_support_on_incident(incident)
            messages.success(request, "Signalement envoyé — le support vous recontactera.")
            return redirect(f"{reverse('bo_courier_incident', args=[delivery.id])}?tab=incident")
        if action == "request_handoff":
            raw_type = request.POST.get("incident_type") or DeliveryIncident.Type.OTHER
            itype = INCIDENT_TYPE_MAP.get(raw_type, raw_type)
            if itype not in dict(DeliveryIncident.Type.choices):
                itype = DeliveryIncident.Type.OTHER
            priority = request.POST.get("priority") or DeliveryIncident.Priority.MEDIUM
            try:
                code, _incident = request_delivery_handoff(
                    delivery,
                    request.user,
                    incident_type=itype,
                    description=request.POST.get("description", "").strip(),
                    priority=priority,
                )
                messages.success(
                    request,
                    f"Transfert demandé. Code à communiquer au remplaçant : {code}",
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect(f"{reverse('bo_courier_incident', args=[delivery.id])}?tab=transfer")

    mission = urgent_mission_context(delivery)
    return render(
        request,
        "backoffice/courier/incident.html",
        _ctx(
            request,
            "courier_incident",
            delivery=delivery,
            mission=mission,
            profile=profile,
            incident_types=INCIDENT_UI_TYPES,
            active_tab=request.GET.get("tab", "incident"),
            support_phone="+241 01 00 00 00",
        ),
    )


# ─── Autorité sanitaire (stats santé publique) ─────────────────────
def _authority_placeholder(request, section: str, title: str, subtitle: str = ""):
    return render(
        request,
        "backoffice/authority/placeholder.html",
        _ctx(
            request,
            section,
            page_title=title,
            page_subtitle=subtitle or "Cette section sera complétée prochainement.",
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("dashboard")
def authority_dashboard(request):
    from core.authority_analytics import (
        authority_dashboard_widgets,
        authority_map_context,
        national_stock_stats,
        region_availability_rows,
    )

    stats_data = national_stock_stats()
    out_meds = stats_data.pop("out_queryset")
    stats = stats_data
    return render(
        request,
        "backoffice/authority/dashboard.html",
        _ctx(
            request,
            "dashboard",
            stats=stats,
            widgets=authority_dashboard_widgets(),
            by_region=region_availability_rows(),
            map_data=authority_map_context(),
            compliance=Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).order_by("compliance_score")[
                :8
            ],
            critical_ruptures=out_meds.select_related("category")[:10],
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("map")
def authority_map(request):
    from core.authority_analytics import authority_map_context

    map_data = authority_map_context()
    return render(
        request,
        "backoffice/authority/map.html",
        _ctx(request, "map", map_data=map_data),
    )


@role_required(*authority_access_roles)
@portal_permission_required("stocks")
def authority_stocks(request):
    from core.authority_analytics import authority_stocks_dashboard

    category = request.GET.get("category", "").strip()
    region = request.GET.get("region", "").strip()
    stock_data = authority_stocks_dashboard(category=category, region=region)
    regions = (
        Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
        .values_list("region", flat=True)
        .distinct()
        .order_by("region")
    )
    return render(
        request,
        "backoffice/authority/stocks.html",
        _ctx(
            request,
            "stocks",
            stock=stock_data,
            filter_category=category,
            filter_region=region,
            regions=regions,
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("stocks")
def authority_ruptures(request):
    return authority_stocks(request)


@role_required(*authority_access_roles)
@portal_permission_required("pharmacies")
def authority_pharmacies(request):
    from core.authority_portal_stats import authority_pharmacies_dashboard
    from core.gabon_regions import GABON_PROVINCES

    region = request.GET.get("region", "").strip()
    status = request.GET.get("status", "").strip()
    compliance = request.GET.get("compliance", "").strip()
    structure_type = request.GET.get("type", "").strip()
    search = request.GET.get("q", "").strip()
    pharma_data = authority_pharmacies_dashboard(
        region=region,
        status=status,
        compliance=compliance,
        structure_type=structure_type,
        search=search,
    )
    return render(
        request,
        "backoffice/authority/pharmacies.html",
        _ctx(
            request,
            "pharmacies",
            pharma=pharma_data,
            filter_region=region,
            filter_status=status,
            filter_compliance=compliance,
            filter_type=structure_type,
            filter_search=search,
            regions=GABON_PROVINCES,
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("pharmacies")
def authority_compliance(request):
    return authority_pharmacies(request)


@role_required(*authority_access_roles)
@portal_permission_required("reports")
def authority_reports(request):
    from core.authority_portal_stats import authority_reports_dashboard
    from core.gabon_regions import GABON_PROVINCES

    period_days = int(request.GET.get("period", 30) or 30)
    period_days = max(7, min(period_days, 365))
    report_type = request.GET.get("type", "overview").strip()
    region = request.GET.get("region", "").strip()
    report = authority_reports_dashboard(
        period_days=period_days,
        report_type=report_type,
        region=region,
    )

    export_fmt = request.GET.get("export")
    if export_fmt == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="gabpharma-indicateurs-{period_days}j.csv"'
        )
        response.write("\ufeff")
        writer = csv.writer(response, delimiter=";")
        writer.writerow(["Gab'Pharma — Rapports & indicateurs (OMS/ODD/DHIS2)"])
        writer.writerow(["Période (jours)", period_days])
        writer.writerow([])
        writer.writerow(["Référentiel", "Pilier", "Code", "Indicateur", "Valeur", "Cible", "Statut"])
        for row in report["frameworks"]:
            writer.writerow(
                [
                    row["framework"],
                    row["pillar"],
                    row["code"],
                    row["indicator"],
                    row["value"],
                    row["target"],
                    row["status"],
                ]
            )
        writer.writerow([])
        writer.writerow(["Province", "Pharmacies", "Densité/100k", "Commandes"])
        for r in report["region_table"]:
            writer.writerow([r["name"], r["pharmacies"], r["density"], r["orders"]])
        return response

    return render(
        request,
        "backoffice/authority/reports.html",
        _ctx(
            request,
            "reports",
            report=report,
            regions=GABON_PROVINCES,
            filter_region=region,
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("alerts")
def authority_alerts(request):
    from core.authority_analytics import authority_alerts_dashboard

    if request.method == "POST" and request.POST.get("action") == "activate_crisis_cell":
        messages.success(
            request,
            "Cellule de crise régionale activée — les responsables régionaux et pharmacies "
            "concernées ont été alertés.",
        )
        return redirect(f"{request.path}?level=high&action=crisis&cell=active")

    level = request.GET.get("level", "").strip()
    if level not in {"", "high", "medium", "low"}:
        level = ""
    crisis_mode = request.GET.get("action") == "crisis"
    crisis_active = request.GET.get("cell") == "active"
    alerts_data = authority_alerts_dashboard(level=level)
    return render(
        request,
        "backoffice/authority/alerts.html",
        _ctx(
            request,
            "alerts",
            alerts=alerts_data,
            filter_level=level,
            crisis_mode=crisis_mode,
            crisis_active=crisis_active,
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("trends")
def authority_trends(request):
    from core.authority_portal_stats import authority_trends_dashboard
    from core.gabon_regions import GABON_PROVINCES

    region = request.GET.get("region", "").strip()
    disease = request.GET.get("disease", "").strip()
    trends_data = authority_trends_dashboard(region=region, disease=disease)
    return render(
        request,
        "backoffice/authority/trends.html",
        _ctx(
            request,
            "trends",
            trends=trends_data,
            filter_region=region,
            filter_disease=disease,
            regions=GABON_PROVINCES,
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("patients")
def authority_patients(request):
    from core.authority_portal_stats import authority_patients_dashboard
    from core.gabon_regions import GABON_PROVINCES

    region = request.GET.get("region", "").strip()
    patients_data = authority_patients_dashboard(region=region)
    return render(
        request,
        "backoffice/authority/patients.html",
        _ctx(
            request,
            "patients",
            patients=patients_data,
            filter_region=region,
            regions=GABON_PROVINCES,
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("deliveries")
def authority_deliveries(request):
    from core.authority_portal_stats import authority_deliveries_dashboard

    status = request.GET.get("status", "").strip()
    search = request.GET.get("q", "").strip()
    delivery_data = authority_deliveries_dashboard(status=status, search=search)
    return render(
        request,
        "backoffice/authority/deliveries.html",
        _ctx(
            request,
            "deliveries",
            delivery=delivery_data,
            filter_status=status,
            filter_search=search,
        ),
    )


@role_required(User.Role.SUPERADMIN)
def authority_disputes(request):
    from core.authority_portal_stats import authority_disputes_dashboard

    status = request.GET.get("status", "").strip()
    priority = request.GET.get("priority", "").strip()
    search = request.GET.get("q", "").strip()
    dispute_data = authority_disputes_dashboard(
        status=status,
        priority=priority,
        search=search,
    )
    return render(
        request,
        "backoffice/authority/disputes.html",
        _ctx(
            request,
            "disputes",
            dispute=dispute_data,
            filter_status=status,
            filter_priority=priority,
            filter_search=search,
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("campaigns")
def authority_campaigns(request):
    from core.authority_portal_stats import authority_campaigns_dashboard
    from core.gabon_regions import GABON_PROVINCES
    from core.models import HealthCampaign
    from django.utils.dateparse import parse_date

    if request.method == "POST" and request.POST.get("action") == "create_campaign":
        title = request.POST.get("title", "").strip()
        theme = request.POST.get("theme", HealthCampaign.Theme.VACCINATION)
        status = request.POST.get("status", HealthCampaign.Status.PLANNED)
        partner = request.POST.get("partner", "").strip()
        start = parse_date(request.POST.get("start_date", ""))
        end = parse_date(request.POST.get("end_date", ""))
        target = int(request.POST.get("target_population") or 0)
        region_slugs = request.POST.getlist("regions")
        if title and start and end and end >= start:
            if theme not in dict(HealthCampaign.Theme.choices):
                theme = HealthCampaign.Theme.VACCINATION
            if status not in dict(HealthCampaign.Status.choices):
                status = HealthCampaign.Status.PLANNED
            HealthCampaign.objects.create(
                title=title,
                theme=theme,
                status=status,
                partner=partner or "MSNS — Gabon",
                description=request.POST.get("description", "").strip(),
                region_slugs=region_slugs,
                start_date=start,
                end_date=end,
                target_population=max(target, 0),
                created_by=request.user,
            )
            messages.success(request, f"Campagne « {title} » enregistrée.")
        else:
            messages.error(request, "Vérifiez le titre et les dates de la campagne.")
        return redirect("bo_authority_campaigns")

    theme = request.GET.get("theme", "").strip()
    status = request.GET.get("status", "").strip()
    region = request.GET.get("region", "").strip()
    campaign_data = authority_campaigns_dashboard(
        theme=theme,
        status=status,
        region=region,
    )
    return render(
        request,
        "backoffice/authority/campaigns.html",
        _ctx(
            request,
            "campaigns",
            campaign=campaign_data,
            filter_theme=theme,
            filter_status=status,
            filter_region=region,
            regions=GABON_PROVINCES,
            theme_choices=HealthCampaign.Theme.choices,
            status_choices=HealthCampaign.Status.choices,
        ),
    )


@role_required(*authority_access_roles)
@portal_permission_required("notifications")
def authority_notifications(request):
    return _authority_placeholder(
        request,
        "notifications",
        "Notifications",
        "Alertes et communications du réseau national.",
    )


@role_required(*authority_access_roles)
@portal_permission_required("settings")
def authority_settings(request):
    return _authority_placeholder(
        request,
        "settings",
        "Paramètres",
        "Préférences du compte et de l'institution.",
    )


@role_required(*authority_access_roles)
@portal_permission_required("decision")
def authority_decision(request):
    from core.authority_portal_stats import authority_decision_dashboard

    decision_data = authority_decision_dashboard()
    return render(
        request,
        "backoffice/authority/decision.html",
        _ctx(request, "decision", decision=decision_data),
    )


# ─── Support (profil support uniquement) ───────────────────────────
@role_required(*support_roles)
@portal_permission_required("dashboard")
def support_dashboard(request):
    # Admin/superadmin → page incidents admin (garde le menu complet)
    if request.user.role in admin_roles:
        return redirect("bo_admin_incidents")
    tickets_open = SupportTicket.objects.exclude(
        status__in=[SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED]
    ).count()
    return render(
        request,
        "backoffice/support/dashboard.html",
        _ctx(
            request,
            "dashboard",
            stats={
                "incidents": DeliveryIncident.objects.filter(status=DeliveryIncident.Status.OPEN).count(),
                "tickets": tickets_open,
                "orders_pending": Order.objects.filter(status=Order.Status.PENDING).count(),
                "notif_unread": Notification.objects.filter(is_read=False).count(),
                "pharmacies_pending": Pharmacy.objects.filter(status=Pharmacy.Status.PENDING).count(),
            },
            incidents=DeliveryIncident.objects.select_related("delivery", "reported_by").order_by(
                "-created_at"
            )[:8],
            tickets=SupportTicket.objects.select_related("client", "order").order_by("-created_at")[:8],
        ),
    )


# ─── Client ────────────────────────────────────────────────────────
@role_required(*client_roles)
def client_dashboard(request):
    orders = (
        request.user.orders.exclude(status=Order.Status.CART)
        .select_related("pharmacy")
        .order_by("-created_at")
    )
    notifs = request.user.notifications.all()[:8]
    fav_qs = request.user.favorites.select_related("stock__medicine", "stock__pharmacy")
    return render(
        request,
        "backoffice/client/dashboard.html",
        _ctx(
            request,
            "dashboard",
            orders=orders[:8],
            favorites=fav_qs[:6],
            favorites_count=fav_qs.count(),
            notifs=notifs,
            unread_notifs=request.user.notifications.filter(is_read=False).count(),
            notifs_count=request.user.notifications.count(),
            prescriptions_count=request.user.prescriptions.count(),
            profile=_ensure_client_profile(request.user),
            orders_count=orders.count(),
        ),
    )


@role_required(*client_roles)
def client_orders(request):
    orders = (
        request.user.orders.exclude(status=Order.Status.CART)
        .select_related("pharmacy")
        .prefetch_related("items", "delivery_evaluation")
        .order_by("-created_at")
    )
    return render(
        request,
        "backoffice/client/orders.html",
        _ctx(request, "orders", orders=orders),
    )


@role_required(*client_roles)
def client_order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related("pharmacy", "linked_prescription", "delivery_evaluation")
        .prefetch_related("items"),
        pk=pk,
        client=request.user,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "evaluate":
            try:
                submit_order_evaluation(
                    order,
                    request.user,
                    pharmacy_rating=request.POST.get("pharmacy_rating"),
                    pharmacy_comment=request.POST.get("pharmacy_comment", ""),
                    courier_rating=request.POST.get("courier_rating"),
                    courier_comment=request.POST.get("courier_comment", ""),
                )
                messages.success(request, "Merci pour votre évaluation !")
            except EvaluationError as exc:
                messages.error(request, str(exc))
            return redirect("bo_client_order_detail", pk=order.pk)
        if action == "insurance_claim":
            if order.status != Order.Status.DELIVERED:
                messages.error(request, "La demande d'assurance n'est possible qu'après livraison.")
            elif order.insurance_claims.exists():
                messages.error(request, "Une demande existe déjà pour cette commande.")
            else:
                provider = get_object_or_404(
                    InsuranceProvider, pk=request.POST.get("provider_id"), is_active=True
                )
                amount = min(int(order.total or 0), int(order.total * provider.coverage_rate / 100))
                InsuranceClaim.objects.create(
                    client=request.user,
                    provider=provider,
                    order=order,
                    amount=max(amount, 0),
                )
                messages.success(request, f"Demande envoyée à {provider.name}.")
            return redirect("bo_client_order_detail", pk=order.pk)

    delivery = Delivery.objects.filter(order=order).select_related("courier").first()
    steps_done = order_tracking_steps(order, delivery)
    from core.insurance import order_awaiting_insurance

    insurance_pending = order_awaiting_insurance(order)
    active_statuses = {
        Order.Status.PENDING,
        Order.Status.AWAITING_RX,
        Order.Status.CONFIRMED,
        Order.Status.PREPARING,
        Order.Status.READY,
        Order.Status.DELIVERING,
    }
    return render(
        request,
        "backoffice/client/order_detail.html",
        _ctx(
            request,
            "orders",
            order=order,
            delivery=delivery,
            steps_done=steps_done,
            show_validation_code=order.status in active_statuses and not insurance_pending,
            insurance_pending=insurance_pending,
            show_client_delivery_qr=order_shows_client_delivery_qr(order),
            client_delivery_qr_url=(
                reverse("bo_client_order_delivery_qr", kwargs={"code": order.code})
                if order_shows_client_delivery_qr(order)
                else ""
            ),
            show_courier_map=(
                bool(delivery)
                and delivery.courier_lat is not None
                and delivery.courier_lng is not None
                and order.status == Order.Status.DELIVERING
            ),
            insurance_providers=InsuranceProvider.objects.filter(is_active=True),
            insurance_claims=order.insurance_claims.select_related("provider"),
            evaluation=getattr(order, "delivery_evaluation", None),
            needs_courier_rating=order_needs_courier_rating(order) and bool(delivery and delivery.courier_id),
        ),
    )


@role_required(*client_roles)
def client_prescriptions(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "upload":
            f = request.FILES.get("file")
            if not f:
                messages.error(request, "Choisissez un fichier (PDF, JPG, PNG).")
            else:
                Prescription.objects.create(
                    client=request.user,
                    file=f,
                    doctor_name=request.POST.get("doctor_name", "").strip(),
                    notes=request.POST.get("notes", "").strip(),
                    status=Prescription.Status.DRAFT,
                )
                notify_user(
                    request.user,
                    "Ordonnance enregistrée",
                    (
                        "Votre ordonnance est dans votre dossier. "
                        "Elle sera proposée automatiquement quand vous commanderez un médicament sur ordonnance (ex. antibiotique)."
                    ),
                    notification_type=Notification.Type.ORDER,
                    transactional=True,
                )
                messages.success(
                    request,
                    "Ordonnance enregistrée dans votre dossier. "
                    "À la commande d’un médicament sur ordonnance, le système vous demandera de la choisir.",
                )
        elif action == "delete":
            rx = get_object_or_404(Prescription, pk=request.POST.get("rx_id"), client=request.user)
            if rx.status in {Prescription.Status.DRAFT, Prescription.Status.PENDING}:
                rx.delete()
                messages.success(request, "Ordonnance supprimée.")
            else:
                messages.error(request, "Cette ordonnance ne peut plus être retirée.")
        return redirect("bo_client_prescriptions")
    return render(
        request,
        "backoffice/client/prescriptions.html",
        _ctx(
            request,
            "prescriptions",
            prescriptions=request.user.prescriptions.all()[:40],
        ),
    )


@role_required(*client_roles)
def client_notifications(request):
    from notifications.routing import resolve_notification_url

    open_id = request.GET.get("open")
    if open_id:
        n = get_object_or_404(Notification, pk=open_id, user=request.user)
        if not n.is_read:
            n.is_read = True
            n.save(update_fields=["is_read"])
        target = resolve_notification_url(n) or reverse("bo_client_notifications")
        return redirect(target)

    notifs = list(request.user.notifications.all()[:50])
    for n in notifs:
        n.open_url = reverse("bo_client_notifications") + f"?open={n.id}"
    return render(
        request,
        "backoffice/client/notifications.html",
        _ctx(
            request,
            "notifications",
            notifs=notifs,
        ),
    )


@role_required(*client_roles)
def client_settings(request):
    """Profil patient — maquette intégrée à l'espace."""
    from core.views import _profile_hub_context

    ctx = _ctx(request, "client_settings", **_profile_hub_context(request, in_espace=True))
    return render(request, "backoffice/client/profile.html", ctx)


@role_required(*client_roles)
def client_profile_personal(request):
    from core.views import _ensure_client_profile

    profile = _ensure_client_profile(request.user)
    if request.method == "POST":
        u = request.user
        u.first_name = request.POST.get("first_name", u.first_name).strip()
        u.last_name = request.POST.get("last_name", u.last_name).strip()
        u.email = request.POST.get("email", u.email).strip()
        u.phone = request.POST.get("phone", u.phone).strip()
        _set_image(u, "avatar", request.FILES)
        u.save()
        profile.date_of_birth = request.POST.get("date_of_birth") or None
        profile.insurance_number = request.POST.get("insurance_number", profile.insurance_number).strip()
        provider_id = request.POST.get("insurance_provider")
        if provider_id:
            profile.insurance_provider_id = provider_id
        profile.emergency_contact = request.POST.get("emergency_contact", profile.emergency_contact).strip()
        profile.save()
        messages.success(request, "Informations enregistrées.")
        return redirect("bo_client_profile_personal")
    return render(
        request,
        "backoffice/client/profile_personal.html",
        _ctx(
            request,
            "profile_personal",
            page_title="Informations personnelles",
            profile=profile,
            insurance_providers=InsuranceProvider.objects.filter(is_active=True).order_by("name"),
        ),
    )


@role_required(*client_roles)
def client_profile_address(request):
    from core.views import _ensure_client_profile, _parse_profile_decimal

    profile = _ensure_client_profile(request.user)
    if request.method == "POST":
        u = request.user
        u.city = request.POST.get("city", u.city).strip()
        u.district = request.POST.get("district", u.district).strip()
        u.latitude = _parse_profile_decimal(request.POST.get("latitude"))
        u.longitude = _parse_profile_decimal(request.POST.get("longitude"))
        u.save()
        profile.address = request.POST.get("address", profile.address).strip()
        profile.save()
        messages.success(request, "Adresse de livraison enregistrée.")
        return redirect("bo_client_profile_address")
    return render(
        request,
        "backoffice/client/profile_address.html",
        _ctx(request, "profile_address", page_title="Adresse de livraison", profile=profile),
    )


@role_required(*client_roles)
def client_profile_payment(request):
    from core.payments_web import PAYMENT_METHODS
    from core.views import _get_payment_method, _set_payment_method

    if request.method == "POST" and request.POST.get("action") == "payment":
        _set_payment_method(request, request.POST.get("payment_method", ""))
        messages.success(request, "Mode de paiement enregistré.")
        return redirect("bo_client_profile_payment")
    return render(
        request,
        "backoffice/client/profile_payment.html",
        _ctx(
            request,
            "profile_payment",
            page_title="Moyens de paiement",
            payment_methods=PAYMENT_METHODS,
            payment_method=_get_payment_method(request),
        ),
    )


@role_required(*client_roles)
def client_profile_preferences(request):
    from core.views import (
        PROFILE_LANG_KEY,
        PROFILE_THEME_KEY,
        _profile_lang,
        _profile_theme,
    )

    if request.method == "POST":
        lang = request.POST.get("language", "fr")
        theme = request.POST.get("theme", "light")
        if lang == "fr":
            request.session[PROFILE_LANG_KEY] = lang
        if theme == "light":
            request.session[PROFILE_THEME_KEY] = theme
        request.session.modified = True
        messages.success(request, "Préférences enregistrées.")
        return redirect("bo_client_profile_preferences")
    return render(
        request,
        "backoffice/client/profile_preferences.html",
        _ctx(
            request,
            "profile_preferences",
            page_title="Préférences",
            language=_profile_lang(request),
            theme=_profile_theme(request),
        ),
    )


@role_required(*client_roles)
def client_profile_security(request):
    if request.method == "POST":
        if _apply_password_change(request, request.user):
            return redirect("bo_client_profile_security")
    return render(
        request,
        "backoffice/client/profile_security.html",
        _ctx(request, "profile_security", page_title="Sécurité du compte"),
    )


@role_required(*client_roles)
def client_profile_privacy(request):
    return render(
        request,
        "backoffice/client/profile_privacy.html",
        _ctx(request, "profile_privacy", page_title="Confidentialité"),
    )


@role_required(*client_roles)
def client_parametres(request):
    from core.views import (
        PROFILE_DATA_SAVER_KEY,
        PROFILE_DEFAULT_PASS_KEY,
        PROFILE_SAVE_SEARCHES_KEY,
        _profile_hub_context,
        _settings_context,
    )
    from payments.models import PatientAccessPurchase

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_searches":
            request.session[PROFILE_SAVE_SEARCHES_KEY] = request.POST.get("enabled") == "1"
            request.session.modified = True
            messages.success(request, "Préférence enregistrée.")
        elif action == "data_saver":
            request.session[PROFILE_DATA_SAVER_KEY] = request.POST.get("enabled") == "1"
            request.session.modified = True
            messages.success(request, "Mode économie de données mis à jour.")
        elif action == "default_pass":
            plan = request.POST.get("plan", "")
            if plan in PatientAccessPurchase.Plan.values:
                request.session[PROFILE_DEFAULT_PASS_KEY] = plan
                request.session.modified = True
                messages.success(request, "Forfait par défaut enregistré.")
        return redirect("bo_client_parametres")

    ctx = _ctx(
        request,
        "parametres",
        **_profile_hub_context(request, in_espace=True),
        **_settings_context(request),
    )
    return render(request, "backoffice/client/parametres.html", ctx)


@role_required(*client_roles)
def client_sessions(request):
    from core.views import _profile_hub_context

    ua = request.META.get("HTTP_USER_AGENT", "Appareil inconnu")
    return render(
        request,
        "backoffice/client/sessions.html",
        _ctx(
            request,
            "profile_sessions",
            page_title="Sessions actives",
            user_agent=ua[:200],
            ip_address=request.META.get("REMOTE_ADDR", "—"),
            **_profile_hub_context(request, in_espace=True),
        ),
    )


@role_required(*client_roles)
def client_about(request):
    from core.views import _profile_hub_context, _settings_context

    return render(
        request,
        "backoffice/client/about.html",
        _ctx(
            request,
            "about",
            page_title="À propos",
            **_profile_hub_context(request, in_espace=True),
            **_settings_context(request),
        ),
    )


@role_required(*client_roles)
def client_terms(request):
    from core.views import _profile_hub_context

    return render(
        request,
        "backoffice/client/terms.html",
        _ctx(
            request,
            "terms",
            page_title="Conditions d'utilisation",
            **_profile_hub_context(request, in_espace=True),
        ),
    )


# ─── Contenu site (admin) ──────────────────────────────────────────
@role_required(*admin_roles)
def admin_hero(request):
    hero = SiteHero.get_solo()
    hero_rules = IMAGE_RULES["hero"]
    if request.method == "POST":
        hero.eyebrow = request.POST.get("eyebrow", hero.eyebrow)
        hero.title = request.POST.get("title", hero.title)
        hero.title_accent = request.POST.get("title_accent", hero.title_accent)
        hero.description = request.POST.get("description", hero.description)
        hero.cta1_label = request.POST.get("cta1_label", hero.cta1_label)
        hero.cta1_url = request.POST.get("cta1_url", hero.cta1_url)
        hero.cta2_label = request.POST.get("cta2_label", hero.cta2_label)
        hero.cta2_url = request.POST.get("cta2_url", hero.cta2_url)
        hero.is_active = bool(request.POST.get("is_active"))
        if request.POST.get("clear_image"):
            hero.hero_image = None
        elif request.FILES.get("hero_image"):
            err = validate_uploaded_image(request.FILES.get("hero_image"), "hero")
            if err:
                messages.error(request, err)
                return render(
                    request,
                    "backoffice/admin/hero.html",
                    _ctx(request, "hero", hero=hero, image_rules=hero_rules),
                )
            _set_image(hero, "hero_image", request.FILES)
        hero.save()
        _audit(request, "update_hero", "cms", "Hero accueil")
        messages.success(request, "Hero enregistré. Visible sur la page d'accueil.")
        return redirect("bo_admin_hero")
    return render(
        request,
        "backoffice/admin/hero.html",
        _ctx(request, "hero", hero=hero, image_rules=hero_rules),
    )


@role_required(*admin_roles)
def admin_ads(request):
    ad_rules = IMAGE_RULES["ad"]
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            title = request.POST.get("title", "").strip()
            uploaded = request.FILES.get("image")
            if title and uploaded:
                err = validate_uploaded_image(uploaded, "ad")
                if err:
                    messages.error(request, err)
                    return redirect("bo_admin_ads")
                Advertisement.objects.create(
                    title=title,
                    image=uploaded,
                    link_url=request.POST.get("link_url", ""),
                    placement=request.POST.get("placement", Advertisement.Placement.HOME_MID),
                    is_active=bool(request.POST.get("is_active", True)),
                    priority=int(request.POST.get("priority") or 0),
                )
                messages.success(request, "Publicité créée.")
            else:
                messages.error(request, "Titre et image obligatoires.")
        elif action == "update":
            ad = get_object_or_404(Advertisement, pk=request.POST.get("ad_id"))
            ad.title = request.POST.get("title", ad.title)
            ad.link_url = request.POST.get("link_url", ad.link_url)
            ad.placement = request.POST.get("placement", ad.placement)
            ad.is_active = request.POST.get("is_active") == "on"
            ad.priority = int(request.POST.get("priority") or ad.priority)
            uploaded = request.FILES.get("image")
            if uploaded:
                err = validate_uploaded_image(uploaded, "ad")
                if err:
                    messages.error(request, err)
                    return redirect(f"{reverse('bo_admin_ads')}?edit={ad.pk}")
                _set_image(ad, "image", request.FILES)
            ad.save()
            messages.success(request, "Publicité mise à jour.")
        elif action == "delete":
            ad = get_object_or_404(Advertisement, pk=request.POST.get("ad_id"))
            ad.delete()
            messages.success(request, "Publicité supprimée.")
        return redirect("bo_admin_ads")

    qs = Advertisement.objects.all()
    edit_obj = (
        Advertisement.objects.filter(pk=request.GET.get("edit")).first()
        if request.GET.get("edit")
        else None
    )
    return render(
        request,
        "backoffice/admin/ads.html",
        _ctx(
            request,
            "ads",
            page_obj=paginate(request, qs, 20),
            total_count=qs.count(),
            edit_obj=edit_obj,
            placements=Advertisement.Placement.choices,
            show_create=request.GET.get("new") == "1",
            image_rules=ad_rules,
        ),
    )


@role_required(*admin_roles)
def admin_tips(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            title = request.POST.get("title", "").strip()
            if title:
                tip = PharmacistTip.objects.create(
                    title=title,
                    category=request.POST.get("category", "Santé"),
                    excerpt=request.POST.get("excerpt", ""),
                    body=request.POST.get("body", ""),
                    icon=request.POST.get("icon", "health_and_safety"),
                    pharmacy_id=request.POST.get("pharmacy") or None,
                    author=request.user,
                    is_published=bool(request.POST.get("is_published")),
                )
                if _set_image(tip, "cover_image", request.FILES):
                    tip.save(update_fields=["cover_image"])
                messages.success(request, "Conseil créé.")
        elif action == "update":
            tip = get_object_or_404(PharmacistTip, pk=request.POST.get("tip_id"))
            tip.title = request.POST.get("title", tip.title)
            tip.category = request.POST.get("category", tip.category)
            tip.excerpt = request.POST.get("excerpt", tip.excerpt)
            tip.body = request.POST.get("body", tip.body)
            tip.icon = request.POST.get("icon", tip.icon)
            tip.pharmacy_id = request.POST.get("pharmacy") or tip.pharmacy_id
            tip.is_published = bool(request.POST.get("is_published"))
            _set_image(tip, "cover_image", request.FILES)
            tip.save()
            messages.success(request, "Conseil mis à jour.")
        elif action == "delete":
            tip = get_object_or_404(PharmacistTip, pk=request.POST.get("tip_id"))
            tip.delete()
            messages.success(request, "Conseil supprimé.")
        return redirect("bo_admin_tips")

    qs = PharmacistTip.objects.select_related("pharmacy").all()
    edit_obj = (
        PharmacistTip.objects.filter(pk=request.GET.get("edit")).first()
        if request.GET.get("edit")
        else None
    )
    return render(
        request,
        "backoffice/admin/tips.html",
        _ctx(
            request,
            "tips",
            page_obj=paginate(request, qs, 20),
            total_count=qs.count(),
            edit_obj=edit_obj,
            pharmacies=Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE),
            show_create=request.GET.get("new") == "1",
        ),
    )


# ─── Pharmacie : messagerie patients ────────────────────────────────
@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_ORDERS)
def pharmacy_messages(request):
    from core.pharmacy_chat import send_pharmacy_message
    from core.pharmacy_filters import filter_pharmacy_conversations, list_query_params
    from notifications.models import PharmacyMessage

    pharmacy = _pharmacy_for(request.user, request)
    if not pharmacy:
        messages.error(request, "Aucune pharmacie associée à votre compte.")
        return redirect("bo_pharmacy_dashboard")

    conv_id = request.GET.get("conv") or request.POST.get("conversation_id")
    conversations = (
        PharmacyConversation.objects.filter(pharmacy=pharmacy)
        .select_related("client")
        .order_by("-updated_at")
    )
    conversations = filter_pharmacy_conversations(
        conversations, request, request.user, PharmacyMessage
    )

    if request.method == "POST":
        conv = get_object_or_404(PharmacyConversation, pk=request.POST.get("conversation_id"), pharmacy=pharmacy)
        body = request.POST.get("body", "").strip()
        if body:
            send_pharmacy_message(
                conversation=conv,
                sender=request.user,
                body=body,
                notify_user=conv.client,
                notify_title=f"Message de {pharmacy.name}",
                notify_message=body[:200],
            )
            messages.success(request, "Réponse envoyée au patient.")
        return redirect(
            f"{reverse('bo_pharmacy_messages')}?{list_query_params(request, 'q', 'unread', 'period', conv=conv.id)}"
        )

    active = conversations.filter(pk=conv_id).first() if conv_id else conversations.first()
    chat_messages = (
        active.messages.select_related("sender").order_by("created_at") if active else []
    )
    if active:
        active.messages.exclude(sender=request.user).update(is_read=True)

    return render(
        request,
        "backoffice/pharmacy/messages.html",
        _ctx(
            request,
            "messages",
            pharmacy=pharmacy,
            conversations=conversations,
            active_conv=active,
            chat_messages=chat_messages,
            search_q=request.GET.get("q", "").strip(),
            search_unread=request.GET.get("unread") == "1",
            search_period=request.GET.get("period", "").strip(),
            conversations_count=conversations.count(),
        ),
    )


# ─── Pharmacie : conseils + logo ────────────────────────────────────
@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_SETTINGS)
def pharmacy_tips(request):
    pharmacy = _pharmacy_for(request.user, request)
    if request.method == "POST" and pharmacy:
        action = request.POST.get("action")
        if action == "create":
            title = request.POST.get("title", "").strip()
            if title:
                tip = PharmacistTip.objects.create(
                    pharmacy=pharmacy,
                    author=request.user,
                    title=title,
                    category=request.POST.get("category", "Santé"),
                    excerpt=request.POST.get("excerpt", ""),
                    body=request.POST.get("body", ""),
                    icon=request.POST.get("icon", "health_and_safety"),
                    is_published=bool(request.POST.get("is_published")),
                )
                if _set_image(tip, "cover_image", request.FILES):
                    tip.save(update_fields=["cover_image"])
                messages.success(request, "Conseil publié sur le site.")
        elif action == "update":
            tip = get_object_or_404(PharmacistTip, pk=request.POST.get("tip_id"), pharmacy=pharmacy)
            tip.title = request.POST.get("title", tip.title)
            tip.category = request.POST.get("category", tip.category)
            tip.excerpt = request.POST.get("excerpt", tip.excerpt)
            tip.body = request.POST.get("body", tip.body)
            tip.icon = request.POST.get("icon", tip.icon)
            tip.is_published = bool(request.POST.get("is_published"))
            _set_image(tip, "cover_image", request.FILES)
            tip.save()
            messages.success(request, "Conseil mis à jour.")
        elif action == "delete":
            tip = get_object_or_404(PharmacistTip, pk=request.POST.get("tip_id"), pharmacy=pharmacy)
            tip.delete()
            messages.success(request, "Conseil supprimé.")
        return redirect("bo_pharmacy_tips")

    tips = PharmacistTip.objects.filter(pharmacy=pharmacy) if pharmacy else PharmacistTip.objects.none()
    edit_obj = tips.filter(pk=request.GET.get("edit")).first() if request.GET.get("edit") else None
    return render(
        request,
        "backoffice/pharmacy/tips.html",
        _ctx(
            request,
            "tips",
            pharmacy=pharmacy,
            page_obj=paginate(request, tips, 20),
            edit_obj=edit_obj,
            show_create=request.GET.get("new") == "1",
        ),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_SETTINGS)
def pharmacy_subscription(request):
    """Forfait Gab'Pharma de l'officine (lecture seule — activé par l'admin)."""
    from core.pharmacy_subscription import subscription_summary

    pharmacy = _pharmacy_for(request.user, request)
    if not pharmacy:
        messages.error(request, "Aucune pharmacie associée à votre compte.")
        return redirect("bo_pharmacy_dashboard")

    summary = subscription_summary(pharmacy)
    history = pharmacy.subscriptions.order_by("-created_at")[:12]
    return render(
        request,
        "backoffice/pharmacy/subscription.html",
        _ctx(
            request,
            "subscription",
            pharmacy=pharmacy,
            summary=summary,
            history=history,
        ),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_SETTINGS)
def pharmacy_settings(request):
    pharmacy = _pharmacy_for(request.user, request)
    if request.method == "POST" and pharmacy:
        action = request.POST.get("action") or "update_profile"
        if action == "update_profile":
            pharmacy.name = request.POST.get("name", pharmacy.name).strip() or pharmacy.name
            pharmacy.phone = request.POST.get("phone", pharmacy.phone)
            pharmacy.email = request.POST.get("email", pharmacy.email)
            pharmacy.address = request.POST.get("address", pharmacy.address)
            pharmacy.city = request.POST.get("city", pharmacy.city)
            pharmacy.district = request.POST.get("district", pharmacy.district)
            pharmacy.region = request.POST.get("region", pharmacy.region)
            pharmacy.description = request.POST.get("description", pharmacy.description)
            pharmacy.opening_hours = request.POST.get("opening_hours", pharmacy.opening_hours)
            pharmacy.delivery_promise = request.POST.get("delivery_promise", pharmacy.delivery_promise).strip()
            pharmacy.logo_color = request.POST.get("logo_color", pharmacy.logo_color) or "green"
            pharmacy.is_24h = request.POST.get("is_24h") == "on"
            pharmacy.is_on_duty = request.POST.get("is_on_duty") == "on"
            lat = request.POST.get("latitude", "").strip()
            lng = request.POST.get("longitude", "").strip()
            from decimal import Decimal, InvalidOperation

            try:
                pharmacy.latitude = Decimal(lat) if lat else None
            except (InvalidOperation, TypeError):
                pass
            try:
                pharmacy.longitude = Decimal(lng) if lng else None
            except (InvalidOperation, TypeError):
                pass
            _set_image(pharmacy, "logo", request.FILES)
            pharmacy.save()
            messages.success(request, "Profil pharmacie enregistré (visible sur le site).")
        elif action == "add_document":
            title = request.POST.get("doc_title", "").strip()
            f = request.FILES.get("doc_file")
            if title and f:
                PharmacyDocument.objects.create(
                    pharmacy=pharmacy,
                    title=title,
                    file=f,
                    expires_at=request.POST.get("doc_expires_at") or None,
                )
                messages.success(request, f"Document « {title} » enregistré.")
            else:
                messages.error(request, "Titre et fichier obligatoires pour un document.")
        elif action == "delete_document":
            doc = get_object_or_404(PharmacyDocument, pk=request.POST.get("doc_id"), pharmacy=pharmacy)
            doc.delete()
            messages.success(request, "Document supprimé.")
        return redirect("bo_pharmacy_settings")

    docs = pharmacy.documents.all() if pharmacy else []
    doc_checklist, extra_documents = (
        pharmacy_document_checklist(pharmacy) if pharmacy else ([], [])
    )
    compliance = pharmacy_compliance_summary(pharmacy) if pharmacy else {}
    from core.pharmacy_subscription import subscription_summary

    sub_summary = subscription_summary(pharmacy) if pharmacy else {}
    return render(
        request,
        "backoffice/pharmacy/settings.html",
        _ctx(
            request,
            "settings",
            pharmacy=pharmacy,
            documents=docs,
            doc_checklist=doc_checklist,
            extra_documents=extra_documents,
            compliance=compliance,
            sub_summary=sub_summary,
            required_document_types=PHARMACY_REQUIRED_DOCUMENTS,
            logo_colors=[("green", "Vert"), ("blue", "Bleu"), ("purple", "Violet")],
        ),
    )


# ─── Admin CDC : assurances & abonnements ───────────────────────────
def _pending_insurer_profile(profile_id):
    return get_object_or_404(
        PartnerProfile.objects.select_related("user", "insurance_provider"),
        pk=profile_id,
        partner_type=PartnerProfile.PartnerType.INSURER,
        validated_at__isnull=True,
        user__status=User.Status.PENDING,
    )


def _approve_insurer_registration(request, profile):
    profile.validated_at = timezone.now()
    profile.validated_by = request.user
    profile.save(update_fields=["validated_at", "validated_by"])
    user = profile.user
    user.status = User.Status.ACTIVE
    user.save(update_fields=["status"])
    if profile.insurance_provider_id:
        provider = profile.insurance_provider
        provider.is_active = True
        provider.save(update_fields=["is_active"])
    notify_user(
        user,
        "Compte assureur validé",
        (
            f"Votre demande d'inscription pour {profile.organization_name} a été approuvée. "
            "Vous pouvez vous connecter à l'espace assurance."
        ),
        notification_type=Notification.Type.SUCCESS,
        transactional=True,
    )
    _audit(
        request,
        "validate_insurer",
        "insurance",
        profile.organization_name,
        True,
    )
    from core.partner_subscription import create_partner_subscription
    from accounts.models import PartnerSubscription

    if not profile.platform_subscriptions.exists():
        create_partner_subscription(
            profile,
            PartnerSubscription.Plan.STANDARD,
            activate=False,
        )


def _reject_insurer_registration(request, profile, notes):
    user = profile.user
    user.status = User.Status.INACTIVE
    user.save(update_fields=["status"])
    if profile.insurance_provider_id:
        profile.insurance_provider.is_active = False
        profile.insurance_provider.save(update_fields=["is_active"])
    notify_user(
        user,
        "Demande d'inscription refusée",
        f"Votre demande pour {profile.organization_name} n'a pas été retenue. Motif : {notes}",
        notification_type=Notification.Type.WARNING,
        transactional=True,
    )
    _audit(
        request,
        "reject_insurer",
        "insurance",
        f"{profile.organization_name} — {notes[:80]}",
        True,
    )


@role_required(*admin_roles)
def admin_insurance(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "claim_status":
            claim = get_object_or_404(InsuranceClaim, pk=request.POST.get("claim_id"))
            st = request.POST.get("status")
            if st in dict(InsuranceClaim.Status.choices):
                claim.status = st
                claim.review_notes = request.POST.get("review_notes", claim.review_notes).strip()
                claim.save()
                notify_user(
                    claim.client,
                    f"Demande assurance {claim.get_status_display().lower()}",
                    f"Votre demande auprès de {claim.provider.name} ({claim.amount} F) est {claim.get_status_display().lower()}.",
                    notification_type=Notification.Type.INFO,
                    data={"claim_id": claim.id},
                    transactional=True,
                )
                messages.success(request, "Demande mise à jour.")
        return redirect("bo_admin_insurance")

    pending_insurers = (
        PartnerProfile.objects.filter(
            partner_type=PartnerProfile.PartnerType.INSURER,
            validated_at__isnull=True,
            user__status=User.Status.PENDING,
        )
        .select_related("user", "insurance_provider")
        .order_by("-user__date_joined")
    )

    return render(
        request,
        "backoffice/admin/insurance.html",
        _ctx(
            request,
            "insurance",
            pending_insurers=pending_insurers,
            providers=InsuranceProvider.objects.filter(is_active=True).order_by("name"),
            claims=InsuranceClaim.objects.select_related("client", "provider", "order")[:40],
            claim_statuses=InsuranceClaim.Status.choices,
        ),
    )


@role_required(*admin_roles)
def admin_insurance_review(request, profile_id):
    profile = _pending_insurer_profile(profile_id)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "validate_insurer":
            _approve_insurer_registration(request, profile)
            messages.success(request, f"Assureur {profile.organization_name} validé.")
            return redirect("bo_admin_insurance")
        if action == "reject_insurer":
            notes = request.POST.get("review_notes", "").strip()
            if not notes:
                messages.error(
                    request,
                    "Indiquez le motif du refus (pièces manquantes, non-conformité, enquête…).",
                )
                return redirect("bo_admin_insurance_review", profile_id=profile.id)
            _reject_insurer_registration(request, profile, notes)
            messages.success(request, f"Demande {profile.organization_name} refusée.")
            return redirect("bo_admin_insurance")

    user = profile.user
    provider = profile.insurance_provider
    return render(
        request,
        "backoffice/admin/insurance_review.html",
        _ctx(
            request,
            "insurance",
            review_profile=profile,
            review_user=user,
            review_provider=provider,
        ),
    )


@role_required(*admin_roles)
def admin_subscriptions(request):
    from accounts.models import PartnerProfile, PartnerSubscription
    from core.partner_subscription import (
        compute_insurer_amount,
        create_partner_subscription,
        insurer_partners_for_admin,
        notify_partner_subscription,
        record_subscription_payment,
    )

    tab = request.GET.get("tab") or request.POST.get("tab") or "pharmacies"
    if tab not in ("pharmacies", "insurers"):
        tab = "pharmacies"

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_pharmacy":
            pharmacy = get_object_or_404(Pharmacy, pk=request.POST.get("pharmacy_id"))
            plan = request.POST.get("plan") or Pharmacy.SubscriptionPlan.ESSENTIAL
            amounts = {
                Pharmacy.SubscriptionPlan.ESSENTIAL: 25000,
                Pharmacy.SubscriptionPlan.PROFESSIONAL: 75000,
                Pharmacy.SubscriptionPlan.ENTERPRISE: 150000,
            }
            start = timezone.localdate()
            end = start + timedelta(days=365 if request.POST.get("annual") == "on" else 30)
            amount = amounts.get(plan, 25000)
            if request.POST.get("annual") == "on":
                amount = int(amount * 12 * 0.8)
            sub = Subscription.objects.create(
                pharmacy=pharmacy,
                plan=plan,
                amount=amount,
                status=Subscription.Status.ACTIVE,
                starts_at=start,
                ends_at=end,
            )
            pharmacy.subscription_plan = plan
            pharmacy.save(update_fields=["subscription_plan", "updated_at"])
            from core.pharmacy_subscription import notify_pharmacy_subscription

            notify_pharmacy_subscription(pharmacy, sub, event="activated")
            messages.success(request, f"Abonnement {sub.get_plan_display()} créé pour {pharmacy.name}.")
        elif action == "status_pharmacy":
            sub = get_object_or_404(Subscription, pk=request.POST.get("sub_id"))
            st = request.POST.get("status")
            if st in dict(Subscription.Status.choices):
                sub.status = st
                sub.save(update_fields=["status"])
                from core.pharmacy_subscription import notify_pharmacy_subscription

                notify_pharmacy_subscription(sub.pharmacy, sub, event="updated")
                if st == Subscription.Status.EXPIRED:
                    ph = sub.pharmacy
                    ph.subscription_plan = Pharmacy.SubscriptionPlan.NONE
                    ph.save(update_fields=["subscription_plan", "updated_at"])
                messages.success(request, "Abonnement pharmacie mis à jour.")
        elif action == "create_insurer":
            partner = get_object_or_404(
                PartnerProfile,
                pk=request.POST.get("partner_id"),
                partner_type=PartnerProfile.PartnerType.INSURER,
            )
            plan = request.POST.get("plan") or PartnerSubscription.Plan.STANDARD
            annual = request.POST.get("annual") == "on"
            activate = request.POST.get("activate_now") == "on"
            ref = request.POST.get("payment_reference", "").strip()
            method = request.POST.get("payment_method", "").strip()
            sub = create_partner_subscription(
                partner,
                plan,
                annual=annual,
                activate=activate,
                payment_reference=ref,
                payment_method=method,
            )
            if activate:
                messages.success(
                    request,
                    f"Abonnement {sub.get_plan_display()} activé pour {partner.organization_name} "
                    f"({sub.amount:,} FCFA).".replace(",", " "),
                )
            else:
                messages.success(
                    request,
                    f"Abonnement {sub.get_plan_display()} créé en attente de paiement pour "
                    f"{partner.organization_name} ({sub.amount:,} FCFA).".replace(",", " "),
                )
        elif action == "record_payment":
            sub = get_object_or_404(PartnerSubscription, pk=request.POST.get("sub_id"))
            record_subscription_payment(
                sub,
                payment_reference=request.POST.get("payment_reference", "").strip(),
                payment_method=request.POST.get("payment_method", "").strip(),
            )
            messages.success(
                request,
                f"Paiement enregistré — abonnement {sub.get_plan_display()} activé pour "
                f"{sub.partner.organization_name}.",
            )
        elif action == "status_insurer":
            sub = get_object_or_404(PartnerSubscription, pk=request.POST.get("sub_id"))
            st = request.POST.get("status")
            if st in dict(PartnerSubscription.Status.choices):
                sub.status = st
                sub.save(update_fields=["status"])
                notify_partner_subscription(sub.partner, sub, event="updated")
                messages.success(request, "Abonnement assureur mis à jour.")
        return redirect(f"{reverse('bo_admin_subscriptions')}?tab={tab}")

    pharmacy_mrr = (
        Subscription.objects.filter(status=Subscription.Status.ACTIVE).aggregate(s=Sum("amount"))["s"]
        or 0
    )
    insurer_mrr = (
        PartnerSubscription.objects.filter(status=PartnerSubscription.Status.ACTIVE).aggregate(
            s=Sum("amount")
        )["s"]
        or 0
    )
    insurer_collected = (
        PartnerSubscription.objects.filter(paid_at__isnull=False).aggregate(s=Sum("amount"))["s"] or 0
    )
    insurer_pending = PartnerSubscription.objects.filter(
        status=PartnerSubscription.Status.PENDING
    ).count()

    return render(
        request,
        "backoffice/admin/subscriptions.html",
        _ctx(
            request,
            "subscriptions",
            tab=tab,
            subscriptions=Subscription.objects.select_related("pharmacy")[:50],
            pharmacies=Pharmacy.objects.order_by("name"),
            plans=Pharmacy.SubscriptionPlan.choices,
            statuses=Subscription.Status.choices,
            mrr=pharmacy_mrr,
            insurer_subscriptions=PartnerSubscription.objects.select_related(
                "partner", "partner__user", "partner__insurance_provider"
            )[:80],
            insurer_partners=insurer_partners_for_admin(),
            insurer_plans=PartnerSubscription.Plan.choices,
            insurer_statuses=PartnerSubscription.Status.choices,
            insurer_plan_amounts={
                PartnerSubscription.Plan.STANDARD: compute_insurer_amount(
                    PartnerSubscription.Plan.STANDARD
                ),
                PartnerSubscription.Plan.PROFESSIONAL: compute_insurer_amount(
                    PartnerSubscription.Plan.PROFESSIONAL
                ),
                PartnerSubscription.Plan.ENTERPRISE: compute_insurer_amount(
                    PartnerSubscription.Plan.ENTERPRISE
                ),
            },
            pharmacy_mrr=pharmacy_mrr,
            insurer_mrr=insurer_mrr,
            insurer_collected=insurer_collected,
            insurer_pending=insurer_pending,
            payment_methods=Payment.Method.choices,
        ),
    )


@role_required(*support_roles)
@portal_permission_required("tickets")
def support_tickets(request):
    if request.method == "POST":
        ticket = get_object_or_404(SupportTicket, pk=request.POST.get("ticket_id"))
        st = request.POST.get("status")
        if st in dict(SupportTicket.Status.choices):
            ticket.status = st
            ticket.staff_notes = request.POST.get("staff_notes", ticket.staff_notes).strip()
            ticket.assigned_to = request.user
            if st in {SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED}:
                ticket.resolved_at = timezone.now()
            ticket.save()
            notify_user(
                ticket.client,
                f"Réclamation {ticket.code}",
                f"Statut : {ticket.get_status_display()}.",
                notification_type=Notification.Type.INFO,
                data={"ticket_id": ticket.id},
                transactional=True,
            )
            messages.success(request, f"{ticket.code} mis à jour.")
        return redirect("bo_support_tickets")

    qs = SupportTicket.objects.select_related("client", "order", "assigned_to").order_by("-created_at")
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)
    return render(
        request,
        "backoffice/support/tickets.html",
        _ctx(
            request,
            "tickets",
            page_obj=paginate(request, qs, 20),
            total_count=qs.count(),
            filter_status=status,
            statuses=SupportTicket.Status.choices,
        ),
    )


@role_required(*client_roles)
def client_loyalty(request):
    settings = load_loyalty_settings()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "redeem":
            reward_id = request.POST.get("reward_id")
            try:
                voucher = redeem_reward(request.user, reward_id)
                messages.success(
                    request,
                    f"Bon {voucher.code} créé — valable jusqu'au "
                    f"{voucher.expires_at.strftime('%d/%m/%Y')}. Utilisez-le au checkout.",
                )
            except LoyaltyError as exc:
                messages.error(request, str(exc))
        return redirect("bo_client_loyalty")

    level, next_threshold = loyalty_level(request.user.loyalty_points)
    txs = request.user.loyalty_transactions.select_related("order", "reward")[:40]
    progress = min(100, int((request.user.loyalty_points or 0) * 100 / max(next_threshold, 1)))
    rewards = LoyaltyReward.objects.filter(is_active=True)
    vouchers = active_vouchers(request.user)
    expiring_pts = sum_expiring_points(request.user)
    tier_pct = tier_discount_percent(request.user)
    return render(
        request,
        "backoffice/client/loyalty.html",
        _ctx(
            request,
            "loyalty",
            level=level,
            next_threshold=next_threshold,
            progress=progress,
            transactions=txs,
            rewards=rewards,
            vouchers=vouchers,
            expiring_pts=expiring_pts,
            tier_discount_percent=tier_pct,
            loyalty_settings=settings,
        ),
    )


@role_required(*client_roles)
def client_support(request):
    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        description = request.POST.get("description", "").strip()
        category = request.POST.get("category") or SupportTicket.Category.OTHER
        if category not in dict(SupportTicket.Category.choices):
            category = SupportTicket.Category.OTHER
        order = None
        oid = request.POST.get("order_id")
        if oid:
            order = request.user.orders.filter(pk=oid).first()
        if not subject or not description:
            messages.error(request, "Sujet et description obligatoires.")
        else:
            ticket = SupportTicket.objects.create(
                client=request.user,
                order=order,
                category=category,
                subject=subject,
                description=description,
                attachment=request.FILES.get("attachment"),
            )
            messages.success(request, f"Réclamation {ticket.code} enregistrée.")
            return redirect("bo_client_support")
    tickets = request.user.support_tickets.select_related("order")[:30]
    orders = request.user.orders.exclude(status=Order.Status.CART).order_by("-created_at")[:20]
    return render(
        request,
        "backoffice/client/support.html",
        _ctx(
            request,
            "support",
            tickets=tickets,
            orders=orders,
            categories=SupportTicket.Category.choices,
        ),
    )


# ─── Profil commun (tous rôles) ─────────────────────────────────────
@login_required(login_url="login")
def my_profile(request):
    """Redirige client/livreur vers leur profil métier dédié."""
    if request.user.role == User.Role.CLIENT:
        return redirect("bo_client_settings")
    if request.user.role == User.Role.COURIER:
        return redirect("bo_courier_settings")
    if request.method == "POST":
        if _apply_password_change(request, request.user):
            return redirect("bo_my_profile")
        u = request.user
        u.first_name = request.POST.get("first_name", u.first_name)
        u.last_name = request.POST.get("last_name", u.last_name)
        u.email = request.POST.get("email", u.email)
        u.phone = request.POST.get("phone", u.phone)
        u.city = request.POST.get("city", u.city)
        u.district = request.POST.get("district", u.district)
        _set_image(u, "avatar", request.FILES)
        u.save()
        messages.success(request, "Profil mis à jour.")
        return redirect("bo_my_profile")
    return render(request, "backoffice/profile.html", _ctx(request, "profile"))

