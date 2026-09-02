from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q, Min, Case, IntegerField, Value, When
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClientProfile, User
from catalog.models import Category, Favorite, Medicine, MedicineQuestion, MedicineReview, PharmacyStock
from core.cart import (
    cart_add,
    cart_clear,
    cart_lines,
    cart_needs_prescription,
    get_cart,
    save_cart,
)
from core.models import Advertisement, PharmacistTip, SiteHero
from core.catalog_visibility import (
    can_show_availability,
    catalog_mask_details,
    mark_stock_verified,
    stock_availability_verified,
)
from core.patient_access import (
    catalog_for_template,
    delivery_fee_for_user,
    ensure_search_access,
    get_best_access,
    purchase_plan,
    user_has_premium,
    user_needs_paywall,
    access_status_label,
)
from core.portal_urls import in_client_espace, portal_reverse
from deliveries.models import Delivery
from core.insurance import create_insurance_claim_for_order, order_awaiting_insurance, quote_insurance
from core.loyalty import (
    active_vouchers,
    apply_voucher_to_order,
    compute_loyalty_benefit,
    loyalty_level,
    tier_discount_percent,
)
from core.payments_web import (
    PAYMENT_METHODS,
    PaymentLimitError,
    ONLINE_PAYMENT_METHODS,
    create_order_payment,
    default_payment_method,
    valid_client_payment_method,
    valid_payment_method,
)
from payments.ebilling import EbillingError
from core.payment_settlement import client_paid_today, load_payment_settings
from core.pharmacy_notifications import check_stock_alert, notify_pharmacy_new_order
from orders.models import Order, OrderItem, Prescription
from payments.models import Payment, PatientAccessPurchase, InsuranceProvider


def _p_redirect(request, viewname, *args, **kwargs):
    return redirect(portal_reverse(request, viewname, *args, **kwargs))


def _portal_template(request, public_template, client_template):
    return client_template if in_client_espace(request) else public_template


CLIENT_PAGE_MAP = {
    "web/catalog.html": ("backoffice/client/catalog.html", "catalog"),
    "web/favorites.html": ("backoffice/client/favorites.html", "favorites"),
    "web/cart.html": ("backoffice/client/cart.html", "cart"),
    "web/checkout.html": ("backoffice/client/checkout.html", "orders"),
    "web/subscription_plans.html": ("backoffice/client/subscriptions.html", "subscriptions"),
    "web/emergency.html": ("backoffice/client/emergency.html", "emergency"),
    "web/messages_inbox.html": ("backoffice/client/messages.html", "messages"),
    "web/pharmacy_chat.html": ("backoffice/client/chat.html", "messages"),
    "web/product_detail.html": ("backoffice/client/product.html", "catalog"),
    "web/payment_confirmed.html": ("backoffice/client/payment_confirmed.html", "orders"),
}


def _client_bo_context(request, section, context):
    """Contexte backoffice patient — même coque que fidélité / commandes."""
    ctx = {
        **context,
        "bo_section": context.get("active_nav") or section or "",
        "bo_label": "Espace patient",
    }
    user = getattr(request, "user", None)
    if user and user.is_authenticated and user.role == User.Role.CLIENT:
        ctx["bo_role"] = "client"
        ctx["is_superadmin"] = False
    return ctx


def _render(request, template, context):
    if in_client_espace(request):
        mapped = CLIENT_PAGE_MAP.get(template)
        if mapped:
            client_tpl, section = mapped
            return render(
                request,
                client_tpl,
                _client_bo_context(request, section, context),
            )
    return render(request, template, context)
from pharmacies.models import Pharmacy


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


def home(request):
    categories = Category.objects.filter(is_active=True, parent__isnull=True).order_by("order")[:8]
    medicines = (
        Medicine.objects.filter(stocks__is_visible=True, stocks__quantity__gt=0)
        .annotate(min_price=Min("stocks__price"))
        .distinct()
        .order_by("name")[:10]
    )
    featured = (
        PharmacyStock.objects.filter(
            is_visible=True,
            quantity__gt=0,
            medicine__is_featured=True,
            pharmacy__status=Pharmacy.Status.ACTIVE,
        )
        .select_related("medicine", "pharmacy")
        .order_by("price")[:10]
    )
    if not featured.exists():
        featured = (
            PharmacyStock.objects.filter(
                is_visible=True, quantity__gt=0, pharmacy__status=Pharmacy.Status.ACTIVE
            )
            .select_related("medicine", "pharmacy")
            .order_by("price")[:10]
        )
    promos = (
        PharmacyStock.objects.filter(
            is_visible=True,
            quantity__gt=0,
            promotional_price__isnull=False,
            pharmacy__status=Pharmacy.Status.ACTIVE,
        )
        .select_related("medicine", "pharmacy")
        .order_by("promotional_price")[:8]
    )
    if not promos.exists():
        promos = featured[:5]
    pharmacies = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).order_by("-rating")[:6]
    labs = (
        Medicine.objects.exclude(laboratory="")
        .values_list("laboratory", flat=True)
        .distinct()[:8]
    )
    tips = PharmacistTip.objects.filter(is_published=True).select_related("pharmacy")[:6]
    return render(
        request,
        "web/home.html",
        {
            "categories": categories,
            "medicines": medicines,
            "featured": featured,
            "promos": promos,
            "pharmacies": pharmacies,
            "labs": labs,
            "hero": SiteHero.get_solo(),
            "tips": tips,
            "ads_top": _active_ads(Advertisement.Placement.HOME_TOP),
            "ads_mid": _active_ads(Advertisement.Placement.HOME_MID),
            "ads_bottom": _active_ads(Advertisement.Placement.HOME_BOTTOM),
            "active_nav": "home",
        },
    )


def search(request):
    q = request.GET.get("q", "").strip()
    category_slug = request.GET.get("category", "")
    sort = request.GET.get("sort", "relevance")
    if sort not in {"relevance", "price_asc", "price_desc"}:
        sort = "relevance"
    categories = Category.objects.filter(is_active=True, parent__isnull=True).order_by("order")
    results = PharmacyStock.objects.none()
    pharmacy_results = Pharmacy.objects.none()
    category = None
    results_count = 0
    pharmacy_count = 0
    search_blocked = False
    explicit_search = bool(q)

    if category_slug:
        category = Category.objects.filter(slug=category_slug).first()

    paywall = user_needs_paywall(request.user)

    if explicit_search:
        if not request.user.is_authenticated:
            messages.warning(
                request,
                "Connectez-vous et choisissez un forfait pour voir les résultats de recherche.",
            )
            return redirect(f"{reverse('login')}?next={request.get_full_path()}")
        if paywall:
            search_blocked = True
            results_count = (
                PharmacyStock.objects.filter(
                    is_visible=True,
                    quantity__gt=0,
                    pharmacy__status=Pharmacy.Status.ACTIVE,
                )
                .filter(
                    Q(medicine__name__icontains=q)
                    | Q(medicine__dci__icontains=q)
                    | Q(medicine__laboratory__icontains=q)
                )
                .count()
            )
            pharmacy_count = (
                Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
                .filter(
                    Q(name__icontains=q)
                    | Q(district__icontains=q)
                    | Q(city__icontains=q)
                    | Q(address__icontains=q)
                )
                .count()
            )
        else:
            ensure_search_access(request.user, q)
            results = (
                PharmacyStock.objects.filter(
                    is_visible=True,
                    quantity__gt=0,
                    pharmacy__status=Pharmacy.Status.ACTIVE,
                )
                .filter(
                    Q(medicine__name__icontains=q)
                    | Q(medicine__dci__icontains=q)
                    | Q(medicine__laboratory__icontains=q)
                )
                .select_related("medicine", "medicine__category", "pharmacy")
            )
            if category:
                results = results.filter(medicine__category=category)
            pharmacy_qs = (
                Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
                .filter(
                    Q(name__icontains=q)
                    | Q(district__icontains=q)
                    | Q(city__icontains=q)
                    | Q(address__icontains=q)
                    | Q(code__icontains=q)
                )
                .order_by("-rating", "name")
            )
            pharmacy_count = pharmacy_qs.count()
            pharmacy_results = pharmacy_qs[:20]
            results_count = results.count()
    elif category:
        results = PharmacyStock.objects.filter(
            is_visible=True,
            pharmacy__status=Pharmacy.Status.ACTIVE,
            medicine__category=category,
        ).select_related("medicine", "pharmacy")
        results_count = results.count()
    elif not paywall:
        results = PharmacyStock.objects.filter(
            is_visible=True,
            quantity__gt=0,
            pharmacy__status=Pharmacy.Status.ACTIVE,
        ).select_related("medicine", "pharmacy")
        results_count = results.count()
    else:
        results = PharmacyStock.objects.filter(
            is_visible=True,
            pharmacy__status=Pharmacy.Status.ACTIVE,
        ).select_related("medicine", "pharmacy")
        results_count = results.count()

    list_limit = 24 if (paywall and not explicit_search and not category) else 40

    if not search_blocked:
        if sort == "price_asc":
            results = results.order_by("price", "medicine__name")
        elif sort == "price_desc":
            results = results.order_by("-price", "medicine__name")
        elif explicit_search:
            results = results.annotate(
                relevance=Case(
                    When(medicine__name__istartswith=q, then=Value(0)),
                    When(medicine__name__icontains=q, then=Value(1)),
                    When(medicine__dci__icontains=q, then=Value(2)),
                    When(medicine__laboratory__icontains=q, then=Value(3)),
                    default=Value(4),
                    output_field=IntegerField(),
                )
            ).order_by("relevance", "price", "medicine__name")
        else:
            results = results.order_by("medicine__name", "price")
        results = results[:list_limit]

    show_availability = can_show_availability(request, explicit_search=explicit_search)
    access = get_best_access(request.user) if request.user.is_authenticated else None

    return _render(
        request,
        "web/catalog.html",
        {
            "q": q,
            "categories": categories,
            "category": category,
            "results": results if not search_blocked else PharmacyStock.objects.none(),
            "results_count": results_count,
            "pharmacy_results": pharmacy_results,
            "pharmacy_count": pharmacy_count,
            "show_availability": show_availability,
            "search_blocked": search_blocked,
            "search_paywall": paywall,
            "mask_catalog_details": catalog_mask_details(),
            "access_status": access_status_label(request.user),
            "active_access": access,
            "sort": sort,
            "active_nav": "search",
        },
    )


@login_required(login_url="login")
def subscription_plans(request):
    if request.user.role != User.Role.CLIENT and request.user.role not in {
        User.Role.ADMIN,
        User.Role.SUPERADMIN,
    }:
        return redirect(request.user.backoffice_home())

    next_url = request.GET.get("next") or request.POST.get("next") or portal_reverse(request, "search")
    plans = catalog_for_template()
    history = request.user.access_purchases.order_by("-purchased_at")[:15]
    mobile_methods = [
        (Payment.Method.MOOV, "Moov Money"),
        (Payment.Method.AIRTEL, "Airtel Money"),
        (Payment.Method.CARD, "Carte bancaire"),
    ]

    if request.method == "POST":
        plan = request.POST.get("plan")
        method = request.POST.get("payment_method") or Payment.Method.MOOV
        try:
            purchase = purchase_plan(request.user, plan, method)
            messages.success(
                request,
                f"Forfait {purchase.get_plan_display()} activé — réf. {purchase.reference}. "
                "Vous pouvez lancer votre recherche.",
            )
            return redirect(next_url)
        except ValueError as exc:
            messages.error(request, str(exc))

    return _render(
        request,
        "web/subscription_plans.html",
        {
            "plans": plans,
            "history": history,
            "mobile_methods": mobile_methods,
            "next_url": next_url,
            "access_status": access_status_label(request.user),
            "active_access": get_best_access(request.user),
            "active_nav": "plans",
        },
    )


PAYMENT_KEY = "gabpharma_payment_method"
PROFILE_LANG_KEY = "gabpharma_profile_lang"
PROFILE_THEME_KEY = "gabpharma_profile_theme"
PROFILE_SAVE_SEARCHES_KEY = "gabpharma_save_searches"
PROFILE_DATA_SAVER_KEY = "gabpharma_data_saver"
PROFILE_DEFAULT_PASS_KEY = "gabpharma_default_pass"


def _get_payment_method(request):
    method = request.session.get(PAYMENT_KEY) or default_payment_method()
    if not valid_payment_method(method):
        method = default_payment_method()
    return method


def _set_payment_method(request, method):
    if valid_payment_method(method):
        request.session[PAYMENT_KEY] = method
        request.session.modified = True


def product_detail(request, stock_id):
    stock = get_object_or_404(
        PharmacyStock.objects.select_related(
            "medicine", "medicine__category", "pharmacy"
        ),
        pk=stock_id,
        is_visible=True,
        pharmacy__status=Pharmacy.Status.ACTIVE,
    )
    show_availability = stock_availability_verified(request, stock.id)
    med = stock.medicine
    line_medicine_ids = Medicine.objects.filter(name=med.name).values_list("pk", flat=True)
    variant_stocks = (
        PharmacyStock.objects.filter(
            pharmacy=stock.pharmacy,
            medicine__name=med.name,
            is_visible=True,
        )
        .select_related("medicine")
        .order_by("medicine__contenance", "medicine__dosage")
    )
    related = (
        PharmacyStock.objects.filter(
            pharmacy=stock.pharmacy,
            is_visible=True,
            quantity__gt=0,
            medicine__category=med.category,
        )
        .exclude(medicine__name=med.name)
        .select_related("medicine")[:6]
        if med.category_id
        else PharmacyStock.objects.none()
    )
    reviews = (
        MedicineReview.objects.filter(medicine_id__in=line_medicine_ids, is_published=True)
        .select_related("user")[:20]
    )
    questions = MedicineQuestion.objects.filter(
        medicine_id__in=line_medicine_ids, is_published=True
    ).order_by("order", "id")
    from django.db.models import Avg

    rating_avg = MedicineReview.objects.filter(
        medicine_id__in=line_medicine_ids, is_published=True
    ).aggregate(a=Avg("rating"))["a"]
    rating_count = MedicineReview.objects.filter(
        medicine_id__in=line_medicine_ids, is_published=True
    ).count()
    content_updated_at = med.updated_at

    if request.method == "POST" and request.user.is_authenticated:
        if request.POST.get("action") == "review":
            comment = request.POST.get("comment", "").strip()
            try:
                rating = int(request.POST.get("rating", 5))
            except ValueError:
                rating = 5
            rating = max(1, min(5, rating))
            if comment:
                MedicineReview.objects.update_or_create(
                    medicine=med,
                    user=request.user,
                    defaults={"rating": rating, "comment": comment, "is_published": True},
                )
                messages.success(request, "Merci pour votre avis.")
            return _p_redirect(request, "product_detail", stock_id=stock.id)

    return _render(
        request,
        "web/product_detail.html",
        {
            "stock": stock,
            "medicine": med,
            "variants": variant_stocks,
            "related": related,
            "reviews": reviews,
            "questions": questions,
            "rating_avg": rating_avg,
            "rating_count": rating_count,
            "content_updated_at": content_updated_at,
            "show_availability": show_availability,
            "active_nav": "search",
        },
    )


@login_required(login_url="login")
def verify_stock_availability(request, stock_id):
    """Déclencheur CDC v1.1 — afficher la disponibilité d'un produit."""
    stock = get_object_or_404(
        PharmacyStock.objects.select_related("pharmacy"),
        pk=stock_id,
        is_visible=True,
        pharmacy__status=Pharmacy.Status.ACTIVE,
    )
    if user_needs_paywall(request.user):
        messages.info(
            request,
            "Choisissez un forfait pour vérifier la disponibilité en pharmacie.",
        )
        return redirect(
            f"{portal_reverse(request, 'subscription_plans')}?next={request.build_absolute_uri()}"
        )
    mark_stock_verified(request, stock.id)
    messages.success(
        request,
        f"Disponibilité affichée pour {stock.medicine} chez {stock.pharmacy.name}.",
    )
    return _p_redirect(request, "product_detail", stock_id=stock.id)


def pharmacy_list(request):
    q = request.GET.get("q", "").strip()
    filter_mode = request.GET.get("filter", "").strip()
    pharmacies = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
    if filter_mode == "on_duty":
        pharmacies = pharmacies.filter(Q(is_on_duty=True) | Q(is_24h=True))
    elif filter_mode == "24h":
        pharmacies = pharmacies.filter(is_24h=True)
    if q:
        pharmacies = pharmacies.filter(
            Q(name__icontains=q)
            | Q(district__icontains=q)
            | Q(city__icontains=q)
            | Q(address__icontains=q)
            | Q(code__icontains=q)
        )
    pharmacies = pharmacies.order_by("-is_on_duty", "-is_24h", "-rating", "name")
    return render(
        request,
        "web/pharmacies.html",
        {
            "pharmacies": pharmacies,
            "q": q,
            "filter_mode": filter_mode,
            "active_nav": "pharmacies",
        },
    )


def pharmacy_detail(request, slug):
    pharmacy = get_object_or_404(Pharmacy, slug=slug, status=Pharmacy.Status.ACTIVE)
    stocks = pharmacy.stocks.filter(is_visible=True).select_related("medicine")[:24]
    return render(
        request,
        "web/pharmacy_detail.html",
        {"pharmacy": pharmacy, "stocks": stocks, "active_nav": "pharmacies"},
    )


def profile_page(request):
    if not request.user.is_authenticated:
        return render(request, "web/profile.html", {"active_nav": "profile"})
    if request.user.role == User.Role.CLIENT:
        return redirect("bo_client_settings")
    return render(
        request,
        "web/profile.html",
        {"active_nav": "profile", "is_staff_profile": True},
    )


def _ensure_client_profile(user):
    profile, _ = ClientProfile.objects.get_or_create(user=user)
    return profile


def _parse_profile_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _apply_profile_password_change(request):
    if request.POST.get("action") != "change_password":
        return False
    user = request.user
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
        login(request, user)
        messages.success(request, "Mot de passe mis à jour.")
    return True


def _profile_lang(request):
    return request.session.get(PROFILE_LANG_KEY, "fr")


def _profile_theme(request):
    return request.session.get(PROFILE_THEME_KEY, "light")


def _profile_lang_label(code):
    return {"fr": "Français", "en": "English"}.get(code, "Français")


def _profile_theme_label(code):
    return {"light": "Clair", "dark": "Sombre"}.get(code, "Clair")


def _profile_save_searches(request):
    val = request.session.get(PROFILE_SAVE_SEARCHES_KEY)
    return True if val is None else bool(val)


def _profile_data_saver(request):
    return bool(request.session.get(PROFILE_DATA_SAVER_KEY, False))


def _profile_default_pass(request):
    from payments.models import PatientAccessPurchase

    plan = request.session.get(PROFILE_DEFAULT_PASS_KEY)
    if plan in PatientAccessPurchase.Plan.values:
        return plan
    return PatientAccessPurchase.Plan.PASS_1H


def _profile_default_pass_label(request):
    from core.patient_access import plan_meta

    meta = plan_meta(_profile_default_pass(request))
    amount = meta.get("amount", 0)
    return f"{meta.get('label', 'Pass 1 heure')} ({amount} FCFA)"


def _settings_context(request):
    from core.payments_web import PAYMENT_METHODS

    profile = _ensure_client_profile(request.user)
    payment_code = _get_payment_method(request)
    payment_label = dict(Payment.Method.choices).get(payment_code, payment_code)
    for code, label, _kind in PAYMENT_METHODS:
        if code == payment_code:
            payment_label = label
            break
    address_short = (
        profile.address[:48] + "…"
        if profile.address and len(profile.address) > 48
        else (profile.address or request.user.display_location or "Non renseignée")
    )
    return {
        "settings_language": _profile_lang_label(_profile_lang(request)),
        "settings_theme": _profile_theme_label(_profile_theme(request)),
        "settings_address": address_short,
        "settings_payment": payment_label,
        "settings_default_pass": _profile_default_pass_label(request),
        "settings_save_searches": _profile_save_searches(request),
        "settings_data_saver": _profile_data_saver(request),
        "app_version": "2.1.0",
    }


def _profile_hub_context(request, *, in_espace=False):
    profile = _ensure_client_profile(request.user)
    access = get_best_access(request.user)
    payment_code = _get_payment_method(request)
    payment_label = dict(Payment.Method.choices).get(payment_code, payment_code)
    orders_count = request.user.orders.exclude(status="cart").count()
    unread = request.user.notifications.filter(is_read=False).count()
    address_preview = (
        profile.address[:60] + "…"
        if profile.address and len(profile.address) > 60
        else (profile.address or request.user.display_location or "Non renseignée")
    )
    months_fr = {
        1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
        7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
    }
    joined = request.user.date_joined
    member_since = f"{months_fr.get(joined.month, '')} {joined.year}"
    if in_espace:
        urls = {
            "personal_url": reverse("bo_client_profile_personal"),
            "address_url": reverse("bo_client_profile_address"),
            "payment_url": reverse("bo_client_profile_payment"),
            "orders_url": reverse("bo_client_orders"),
            "favorites_url": reverse("bo_client_favorites"),
            "notifications_url": reverse("bo_client_notifications"),
            "preferences_url": reverse("bo_client_profile_preferences"),
            "preferences_theme_url": reverse("bo_client_profile_preferences") + "#theme",
            "security_url": reverse("bo_client_profile_security"),
            "privacy_url": reverse("bo_client_profile_privacy"),
            "prescriptions_url": reverse("bo_client_prescriptions"),
            "loyalty_url": reverse("bo_client_loyalty"),
            "support_url": reverse("bo_client_support"),
            "subscriptions_url": reverse("bo_client_subscriptions"),
            "parametres_url": reverse("bo_client_parametres"),
            "parametres_save_searches_url": reverse("bo_client_parametres") + "#save-searches",
            "parametres_data_saver_url": reverse("bo_client_parametres") + "#data-saver",
            "sessions_url": reverse("bo_client_sessions"),
            "about_url": reverse("bo_client_about"),
            "terms_url": reverse("bo_client_terms"),
        }
    else:
        urls = {
            "personal_url": reverse("profile_personal"),
            "address_url": reverse("profile_address"),
            "payment_url": reverse("profile_payment"),
            "orders_url": reverse("orders"),
            "favorites_url": reverse("favorites"),
            "notifications_url": reverse("profile_notifications"),
            "preferences_url": reverse("profile_preferences"),
            "preferences_theme_url": reverse("profile_preferences") + "#theme",
            "security_url": reverse("profile_security"),
            "privacy_url": reverse("profile_privacy"),
            "prescriptions_url": reverse("bo_client_prescriptions"),
            "loyalty_url": reverse("bo_client_loyalty"),
            "support_url": reverse("bo_client_support"),
            "subscriptions_url": reverse("subscription_plans"),
            "parametres_url": reverse("profile_preferences"),
            "parametres_save_searches_url": reverse("profile_preferences"),
            "parametres_data_saver_url": reverse("profile_preferences"),
        }
    save_on = _profile_save_searches(request)
    data_on = _profile_data_saver(request)
    return {
        "active_nav": "profile",
        "in_espace": in_espace,
        "profile": profile,
        "is_verified": request.user.is_email_verified or request.user.status == User.Status.ACTIVE,
        "member_since": member_since,
        "has_active_plan": access is not None,
        "active_plan_label": access.get_plan_display() if access else "",
        "access_status": access_status_label(request.user),
        "address_preview": address_preview,
        "payment_label": payment_label,
        "orders_count": f"{orders_count} commande{'s' if orders_count != 1 else ''}",
        "unread_notifs": f"{unread} non lue{'s' if unread != 1 else ''}" if unread else "À jour",
        "unread_badge": str(unread) if unread else "",
        "language_label": _profile_lang_label(_profile_lang(request)),
        "theme_label": _profile_theme_label(_profile_theme(request)),
        "default_pass_label": _profile_default_pass_label(request),
        "save_searches_label": "Activé" if save_on else "Désactivé",
        "data_saver_label": "Activé" if data_on else "Désactivé",
        "loyalty_label": f"{request.user.loyalty_points} points",
        **urls,
    }


def _profile_subpage_ctx(request, page_title):
    ctx = _profile_hub_context(request)
    ctx["page_title"] = page_title
    return ctx


@login_required(login_url="login")
def profile_personal(request):
    if request.user.role != User.Role.CLIENT:
        return redirect("profile")
    profile = _ensure_client_profile(request.user)
    if request.method == "POST":
        u = request.user
        u.first_name = request.POST.get("first_name", u.first_name).strip()
        u.last_name = request.POST.get("last_name", u.last_name).strip()
        u.email = request.POST.get("email", u.email).strip()
        u.phone = request.POST.get("phone", u.phone).strip()
        if request.FILES.get("avatar"):
            u.avatar = request.FILES["avatar"]
        u.save()
        profile.date_of_birth = request.POST.get("date_of_birth") or None
        profile.insurance_number = request.POST.get("insurance_number", profile.insurance_number).strip()
        provider_id = request.POST.get("insurance_provider")
        if provider_id:
            profile.insurance_provider_id = provider_id
        profile.emergency_contact = request.POST.get("emergency_contact", profile.emergency_contact).strip()
        profile.save()
        messages.success(request, "Informations enregistrées.")
        return redirect("profile_personal")
    ctx = _profile_subpage_ctx(request, "Informations personnelles")
    ctx["insurance_providers"] = InsuranceProvider.objects.filter(is_active=True).order_by("name")
    return render(
        request,
        "web/profile_personal.html",
        ctx,
    )


@login_required(login_url="login")
def profile_address(request):
    if request.user.role != User.Role.CLIENT:
        return redirect("profile")
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
        return redirect("profile_address")
    return render(
        request,
        "web/profile_address.html",
        _profile_subpage_ctx(request, "Adresse de livraison"),
    )


@login_required(login_url="login")
def profile_payment(request):
    if request.user.role != User.Role.CLIENT:
        return redirect("profile")
    if request.method == "POST" and request.POST.get("action") == "payment":
        _set_payment_method(request, request.POST.get("payment_method", ""))
        messages.success(request, "Mode de paiement enregistré.")
        return redirect("profile_payment")
    ctx = _profile_subpage_ctx(request, "Moyens de paiement")
    ctx.update(
        {
            "payment_methods": PAYMENT_METHODS,
            "payment_method": _get_payment_method(request),
        }
    )
    return render(request, "web/profile_payment.html", ctx)


@login_required(login_url="login")
def profile_notifications(request):
    if request.user.role != User.Role.CLIENT:
        return redirect("profile")
    from django.conf import settings as dj_settings

    from notifications.models import Notification, NotificationPreference
    from notifications.services import get_user_preferences

    prefs = get_user_preferences(request.user)

    open_id = request.GET.get("open")
    if open_id:
        from notifications.routing import resolve_notification_url

        n = get_object_or_404(Notification, pk=open_id, user=request.user)
        if not n.is_read:
            n.is_read = True
            n.save(update_fields=["is_read"])
        target = resolve_notification_url(n)
        if target:
            return redirect(target)
        return redirect("profile_notifications")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_channels":
            prefs.push_enabled = request.POST.get("push_enabled") == "on"
            prefs.sms_enabled = request.POST.get("sms_enabled") == "on"
            prefs.email_enabled = request.POST.get("email_enabled") == "on"
            prefs.whatsapp_enabled = request.POST.get("whatsapp_enabled") == "on"
            prefs.marketing_enabled = request.POST.get("marketing_enabled") == "on"
            prefs.save()
            messages.success(request, "Préférences de notification enregistrées.")
        return redirect("profile_notifications")
    notifs = list(request.user.notifications.all()[:50])
    for n in notifs:
        n.open_url = reverse("profile_notifications") + f"?open={n.id}"
    unread_count = request.user.notifications.filter(is_read=False).count()
    ctx = _profile_subpage_ctx(request, "Notifications")
    ctx.update(
        {
            "notifications": notifs,
            "unread_count": unread_count,
            "notif_prefs": prefs,
            "vapid_public_key": dj_settings.VAPID_PUBLIC_KEY,
        }
    )
    return render(request, "web/profile_notifications.html", ctx)


@login_required(login_url="login")
def push_subscribe(request):
    """Enregistre un abonnement Web Push (PWA)."""
    import json

    from django.http import JsonResponse

    from notifications.services import save_push_subscription

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST requis"}, status=405)
    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON invalide"}, status=400)
    endpoint = payload.get("endpoint", "")
    keys = payload.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return JsonResponse({"ok": False, "error": "Abonnement incomplet"}, status=400)
    save_push_subscription(
        request.user,
        endpoint=endpoint,
        p256dh=keys["p256dh"],
        auth=keys["auth"],
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    return JsonResponse({"ok": True})


@login_required(login_url="login")
def profile_preferences(request):
    if request.user.role != User.Role.CLIENT:
        return redirect("profile")
    if request.method == "POST":
        lang = request.POST.get("language", "fr")
        theme = request.POST.get("theme", "light")
        if lang == "fr":
            request.session[PROFILE_LANG_KEY] = lang
        if theme == "light":
            request.session[PROFILE_THEME_KEY] = theme
        request.session.modified = True
        messages.success(request, "Préférences enregistrées.")
        return redirect("profile_preferences")
    ctx = _profile_subpage_ctx(request, "Préférences")
    ctx.update({"language": _profile_lang(request), "theme": _profile_theme(request)})
    return render(request, "web/profile_preferences.html", ctx)


@login_required(login_url="login")
def profile_security(request):
    if request.user.role != User.Role.CLIENT:
        return redirect("profile")
    if request.method == "POST" and _apply_profile_password_change(request):
        return redirect("profile_security")
    return render(
        request,
        "web/profile_security.html",
        _profile_subpage_ctx(request, "Sécurité du compte"),
    )


@login_required(login_url="login")
def profile_privacy(request):
    if request.user.role != User.Role.CLIENT:
        return redirect("profile")
    return render(
        request,
        "web/profile_privacy.html",
        _profile_subpage_ctx(request, "Confidentialité"),
    )

def orders_page(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={reverse('orders')}")
    orders = request.user.orders.exclude(status="cart").select_related("pharmacy")[:20]
    return render(request, "web/orders.html", {"orders": orders, "active_nav": "orders"})


def favorites_page(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={reverse('favorites')}")
    if request.method == "POST" and request.POST.get("action") == "remove":
        fav = get_object_or_404(
            Favorite, pk=request.POST.get("fav_id"), user=request.user
        )
        name = fav.stock.medicine.name
        fav.delete()
        messages.success(request, f"{name} retiré des favoris.")
        return _p_redirect(request, "favorites")
    favorites = (
        request.user.favorites.select_related("stock__medicine", "stock__pharmacy")
        .order_by("-created_at")
    )
    return _render(request, "web/favorites.html", {"favorites": favorites, "active_nav": "favorites"})


@login_required(login_url="login")
def favorite_toggle(request, stock_id):
    """Ajoute ou retire une offre (stock pharmacie) des favoris."""
    stock = get_object_or_404(PharmacyStock.objects.select_related("medicine"), pk=stock_id)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or portal_reverse(request, "favorites")
    if not str(next_url).startswith("/"):
        next_url = portal_reverse(request, "favorites")
    fav = Favorite.objects.filter(user=request.user, stock=stock).first()
    if fav:
        fav.delete()
        messages.success(request, f"{stock.medicine.name} retiré des favoris.")
    else:
        Favorite.objects.create(user=request.user, stock=stock)
        messages.success(request, f"{stock.medicine.name} ajouté aux favoris.")
    return redirect(next_url)


def offline(request):
    return render(request, "core/offline.html")


@login_required(login_url="login")
def cart_add_view(request, stock_id):
    if request.method != "POST":
        return _p_redirect(request, "home")
    stock, err = cart_add(request, stock_id, qty=int(request.POST.get("qty") or 1))
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    cart_count = sum(get_cart(request).values())

    if err:
        if is_ajax:
            return JsonResponse(
                {"ok": False, "message": err, "level": "error", "cart_count": cart_count}
            )
        messages.error(request, err)
    elif stock and stock.medicine.requires_prescription:
        msg = (
            f"{stock.medicine.name} ajouté — médicament sur ordonnance : "
            "vous devrez choisir une ordonnance de votre dossier à la commande."
        )
        if is_ajax:
            return JsonResponse(
                {
                    "ok": True,
                    "message": msg,
                    "level": "warning",
                    "cart_count": cart_count,
                    "product": stock.medicine.name,
                }
            )
        messages.warning(request, msg)
    elif stock:
        msg = f"{stock.medicine.name} ajouté au panier."
        if is_ajax:
            return JsonResponse(
                {
                    "ok": True,
                    "message": msg,
                    "level": "success",
                    "cart_count": cart_count,
                    "product": stock.medicine.name,
                }
            )
        messages.success(request, msg)
    elif is_ajax:
        return JsonResponse(
            {
                "ok": False,
                "message": "Produit indisponible.",
                "level": "error",
                "cart_count": cart_count,
            }
        )

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or portal_reverse(request, "cart")
    if not str(next_url).startswith("/"):
        next_url = portal_reverse(request, "cart")
    return redirect(next_url)


@login_required(login_url="login")
def cart_view(request):
    if request.method == "POST":
        action = request.POST.get("action")
        cart = get_cart(request)
        if action == "update":
            for sid, qty in list(cart.items()):
                try:
                    q = int(request.POST.get(f"qty_{sid}", qty))
                except ValueError:
                    q = qty
                if q <= 0:
                    cart.pop(sid, None)
                else:
                    cart[sid] = q
            save_cart(request, cart)
            messages.success(request, "Panier mis à jour.")
        elif action == "payment":
            _set_payment_method(request, request.POST.get("payment_method", ""))
            messages.success(request, "Mode de paiement enregistré.")
        elif action == "clear":
            cart_clear(request)
            messages.success(request, "Panier vidé.")
        elif action == "remove":
            cart.pop(str(request.POST.get("stock_id")), None)
            save_cart(request, cart)
        return _p_redirect(request, "cart")
    lines = cart_lines(request)
    total = sum(l["line_total"] for l in lines)
    pharmacy = lines[0]["stock"].pharmacy if lines else None
    needs_rx = cart_needs_prescription(lines)
    rx_meds = [
        l["stock"].medicine.name
        for l in lines
        if l["stock"].medicine.requires_prescription
    ]
    dossier_count = (
        request.user.prescriptions.filter(
            status__in=[
                Prescription.Status.DRAFT,
                Prescription.Status.VALIDATED,
                Prescription.Status.PENDING,
            ]
        ).count()
        if request.user.is_authenticated
        else 0
    )
    delivery_fee = delivery_fee_for_user(request.user, Order.DeliveryMode.HOME) if lines else 0
    premium_delivery = user_has_premium(request.user)
    return _render(
        request,
        "web/cart.html",
        {
            "lines": lines,
            "total": total,
            "delivery_fee": delivery_fee,
            "grand_total": total + delivery_fee,
            "premium_delivery": premium_delivery,
            "pharmacy": pharmacy,
            "needs_rx": needs_rx,
            "rx_meds": rx_meds,
            "dossier_rx_count": dossier_count,
            "payment_methods": PAYMENT_METHODS,
            "payment_method": _get_payment_method(request),
            "active_nav": "orders",
        },
    )


@login_required(login_url="login")
def checkout_insurance_quote(request):
    """Devis checkout JSON (fidélité + assurance)."""
    lines = cart_lines(request)
    if not lines:
        return JsonResponse({"ok": False, "reason": "Panier vide."}, status=400)
    subtotal = sum(l["line_total"] for l in lines)
    mode = request.GET.get("delivery_mode") or Order.DeliveryMode.HOME
    fee = 0 if mode == Order.DeliveryMode.PICKUP else delivery_fee_for_user(request.user, mode)
    gross = subtotal + fee
    voucher_code = request.GET.get("loyalty_voucher", "")
    loyalty = compute_loyalty_benefit(
        request.user,
        subtotal=subtotal,
        delivery_fee=fee,
        voucher_code=voucher_code,
    )
    if voucher_code and not loyalty["voucher"]:
        return JsonResponse({"ok": False, "reason": "Bon fidélité invalide ou expiré."}, status=400)
    after_loyalty = max(0, gross - loyalty["loyalty_discount"])
    use_insurance = request.GET.get("use_insurance") == "1"
    coverage = 0
    client_share = after_loyalty
    provider_name = ""
    coverage_rate = 0
    insurance_ok = True
    insurance_reason = ""
    if use_insurance:
        provider_id = request.GET.get("insurance_provider")
        provider = None
        if provider_id:
            provider = InsuranceProvider.objects.filter(pk=provider_id, is_active=True).first()
        else:
            profile = ClientProfile.objects.filter(user=request.user).first()
            if profile and profile.insurance_provider_id:
                provider = profile.insurance_provider
        quote = quote_insurance(request.user, provider, after_loyalty)
        insurance_ok = quote.ok
        insurance_reason = quote.reason
        coverage = quote.coverage_amount
        client_share = quote.client_share
        provider_name = quote.provider.name if quote.provider else ""
        coverage_rate = quote.coverage_rate
    return JsonResponse(
        {
            "ok": insurance_ok,
            "reason": insurance_reason,
            "coverage_amount": coverage,
            "client_share": client_share,
            "gross_total": gross,
            "loyalty_discount": loyalty["loyalty_discount"],
            "loyalty_label": loyalty["label"],
            "coverage_rate": coverage_rate,
            "provider_name": provider_name,
        }
    )


@login_required(login_url="login")
def checkout_view(request):
    lines = cart_lines(request)
    if not lines:
        messages.error(request, "Votre panier est vide.")
        return _p_redirect(request, "cart")
    pharmacy = lines[0]["stock"].pharmacy
    needs_rx = cart_needs_prescription(lines)
    # Ordonnances stockées (dossier) — à choisir à la commande
    rx_qs = request.user.prescriptions.filter(
        status__in=[
            Prescription.Status.DRAFT,
            Prescription.Status.VALIDATED,
            Prescription.Status.PENDING,
        ]
    ).order_by("-created_at")

    if request.method == "POST":
        mode = request.POST.get("delivery_mode") or Order.DeliveryMode.HOME
        if mode not in dict(Order.DeliveryMode.choices):
            mode = Order.DeliveryMode.HOME

        address = ""
        dlat = dlng = None
        if mode != Order.DeliveryMode.PICKUP:
            try:
                dlat = Decimal(str(request.POST.get("delivery_latitude", "")).strip())
                dlng = Decimal(str(request.POST.get("delivery_longitude", "")).strip())
            except (InvalidOperation, TypeError, ValueError):
                dlat = dlng = None
            if dlat is None or dlng is None:
                messages.error(
                    request,
                    "Activez la géolocalisation pour indiquer votre position de livraison.",
                )
                return _p_redirect(request, "checkout")
            if not (-90 <= float(dlat) <= 90 and -180 <= float(dlng) <= 180):
                messages.error(request, "Position GPS invalide.")
                return _p_redirect(request, "checkout")
            address = request.POST.get("delivery_address", "").strip()
            if not address:
                address = f"Position GPS ({dlat}, {dlng})"
            # Mémorise la position sur le profil client
            request.user.latitude = dlat
            request.user.longitude = dlng
            request.user.save(update_fields=["latitude", "longitude", "updated_at"])

        fee = delivery_fee_for_user(request.user, mode)
        subtotal_preview = sum(l["line_total"] for l in lines)
        gross_preview = subtotal_preview + fee

        voucher_code = request.POST.get("loyalty_voucher", "").strip()
        loyalty_benefit = compute_loyalty_benefit(
            request.user,
            subtotal=subtotal_preview,
            delivery_fee=fee,
            voucher_code=voucher_code,
        )
        if voucher_code and not loyalty_benefit["voucher"]:
            messages.error(request, "Bon fidélité invalide ou expiré.")
            return _p_redirect(request, "checkout")

        use_insurance = request.POST.get("use_insurance") == "1"
        insurance_provider = None
        if use_insurance:
            provider_id = request.POST.get("insurance_provider")
            if provider_id:
                insurance_provider = InsuranceProvider.objects.filter(
                    pk=provider_id, is_active=True
                ).first()
            if not insurance_provider:
                profile = ClientProfile.objects.filter(user=request.user).first()
                if profile and profile.insurance_provider_id:
                    insurance_provider = profile.insurance_provider
            insurance_gross = max(
                0,
                gross_preview - loyalty_benefit["loyalty_discount"],
            )
            quote = quote_insurance(request.user, insurance_provider, insurance_gross)
            if not quote.ok:
                messages.error(
                    request,
                    f"Assurance refusée : {quote.reason} "
                    "Désactivez l'assurance ou choisissez un autre moyen de paiement.",
                )
                return _p_redirect(request, "checkout")

        linked = None
        if needs_rx:
            rx_id = request.POST.get("prescription_id")
            upload = request.FILES.get("prescription_file")
            if upload:
                linked = Prescription.objects.create(
                    client=request.user,
                    pharmacy=pharmacy,
                    file=upload,
                    doctor_name=request.POST.get("doctor_name", "").strip(),
                    status=Prescription.Status.PENDING,
                    notes="Jointe à la commande",
                )
            elif rx_id:
                linked = get_object_or_404(Prescription, pk=rx_id, client=request.user)
                linked.pharmacy = pharmacy
                linked.status = Prescription.Status.PENDING
                linked.save(update_fields=["pharmacy", "status"])
            else:
                messages.error(
                    request,
                    "Choisissez une ordonnance de votre dossier, ou déposez-en une nouvelle.",
                )
                return _p_redirect(request, "checkout")

        order = Order.objects.create(
            client=request.user,
            pharmacy=pharmacy,
            status=Order.Status.AWAITING_RX if needs_rx else Order.Status.PENDING,
            delivery_mode=mode,
            delivery_address=address or "",
            delivery_latitude=dlat,
            delivery_longitude=dlng,
            delivery_fee=fee,
            notes=request.POST.get("notes", "").strip(),
            linked_prescription=linked,
            is_urgent=user_has_premium(request.user),
        )
        if linked and linked.file:
            order.prescription = linked.file
            order.save(update_fields=["prescription"])
        for line in lines:
            OrderItem.objects.create(
                order=order,
                medicine=line["stock"].medicine,
                quantity=line["qty"],
                unit_price=line["stock"].display_price,
                medicine_name=str(line["stock"].medicine),
            )
            # décrémente stock
            s = line["stock"]
            prev_qty = s.quantity
            s.quantity = max(0, s.quantity - line["qty"])
            s.save(update_fields=["quantity", "updated_at"])
            check_stock_alert(s, previous_qty=prev_qty)
        order.recalculate_totals()
        order.loyalty_discount = loyalty_benefit["loyalty_discount"]
        order.save(update_fields=["loyalty_discount", "updated_at"])
        order.recalculate_totals()
        if loyalty_benefit["voucher"]:
            apply_voucher_to_order(order, loyalty_benefit["voucher"])
            order.recalculate_totals()
        if use_insurance and insurance_provider:
            order.insurance_coverage = quote.coverage_amount
            order.insurance_provider = insurance_provider
            order.save(update_fields=["insurance_coverage", "insurance_provider", "updated_at"])
            order.recalculate_totals()
            create_insurance_claim_for_order(
                order, request.user, insurance_provider, order.insurance_coverage
            )

        payment_method = request.POST.get("payment_method") or _get_payment_method(request)
        if order.total > 0 and not valid_client_payment_method(payment_method):
            payment_method = default_payment_method()
        _set_payment_method(request, payment_method)
        return_url = request.build_absolute_uri(
            reverse("payment_confirmed", kwargs={"code": order.code})
        )
        try:
            flow = create_order_payment(
                order,
                payment_method,
                return_url=return_url,
            )
        except (PaymentLimitError, EbillingError) as exc:
            order.delete()
            messages.error(request, str(exc))
            return _p_redirect(request, "checkout")
        payment = flow.payment
        cart_clear(request)

        if flow.redirect_url:
            messages.info(
                request,
                "Commande enregistrée. Finalisez votre paiement sur la page sécurisée E-Billing.",
            )
            return redirect(flow.redirect_url)

        from notifications.models import Notification
        from notifications.services import notify_user

        awaiting_insurance = order_awaiting_insurance(order)
        if not awaiting_insurance and not flow.pending_online:
            notify_pharmacy_new_order(order)

        if needs_rx:
            notify_user(
                request.user,
                f"Commande {order.code} envoyée",
                (
                    f"Votre commande a été transmise à {pharmacy.name}. "
                    "La pharmacie doit valider l’ordonnance avant préparation."
                ),
                notification_type=Notification.Type.ORDER,
                data={"order_id": order.id, "code": order.code},
                transactional=True,
            )
            messages.success(
                request,
                f"Commande {order.code} envoyée. La pharmacie doit valider l’ordonnance avant préparation.",
            )
        else:
            if awaiting_insurance:
                notify_user(
                    request.user,
                    f"Commande {order.code} — assurance en attente",
                    (
                        f"Votre demande a été transmise à {insurance_provider.name}. "
                        "Vous recevrez votre code de retrait dès validation par l'assureur."
                    ),
                    notification_type=Notification.Type.ORDER,
                    data={"order_id": order.id, "code": order.code},
                    transactional=True,
                )
                messages.success(
                    request,
                    f"Commande {order.code} enregistrée. En attente de validation par "
                    f"{insurance_provider.name} avant envoi à la pharmacie.",
                )
            else:
                if flow.pending_online:
                    notify_user(
                        request.user,
                        f"Commande {order.code} — paiement en cours",
                        "Validez le paiement sur votre téléphone. Vous serez notifié dès confirmation.",
                        notification_type=Notification.Type.ORDER,
                        data={"order_id": order.id, "code": order.code},
                        transactional=True,
                    )
                    messages.info(
                        request,
                        "Paiement en cours de validation. Validez l'opération sur votre téléphone.",
                    )
                else:
                    notify_user(
                        request.user,
                        f"Commande {order.code} enregistrée",
                        f"Votre commande a été transmise à {pharmacy.name}.",
                        notification_type=Notification.Type.ORDER,
                        data={"order_id": order.id, "code": order.code},
                        transactional=True,
                    )
                    messages.success(request, f"Commande {order.code} enregistrée.")
        pay_label = dict(Payment.Method.choices).get(payment.method, payment.method) if payment else "Assurance"
        if order.insurance_coverage > 0 and insurance_provider:
            if awaiting_insurance:
                messages.info(
                    request,
                    f"{insurance_provider.name} : prise en charge de {order.insurance_coverage} F "
                    f"en cours de validation. Reste à payer après accord : {order.total} F.",
                )
            else:
                messages.info(
                    request,
                    f"{insurance_provider.name} prend en charge {order.insurance_coverage} F. "
                    f"Reste à payer : {order.total} F.",
                )
        if loyalty_benefit["loyalty_discount"] > 0:
            messages.info(
                request,
                f"Réduction fidélité appliquée : −{loyalty_benefit['loyalty_discount']} F "
                f"({loyalty_benefit['label']}).",
            )
        if payment:
            messages.info(
                request,
                f"Paiement {pay_label} — réf. {payment.reference}"
                + (f" (acompte {payment.amount} F)" if payment.is_deposit else ""),
            )
        return _p_redirect(request, "payment_confirmed", code=order.code)

    total = sum(l["line_total"] for l in lines)
    fee = delivery_fee_for_user(request.user, Order.DeliveryMode.HOME)
    gross = total + fee
    rx_meds = [
        l["stock"].medicine.name
        for l in lines
        if l["stock"].medicine.requires_prescription
    ]
    profile, _ = ClientProfile.objects.get_or_create(user=request.user)
    insurance_providers = InsuranceProvider.objects.filter(is_active=True).order_by("name")
    default_provider = profile.insurance_provider
    use_insurance_default = bool(
        profile.insurance_number and (default_provider or insurance_providers.exists())
    )
    initial_quote = None
    if use_insurance_default and default_provider:
        initial_quote = quote_insurance(request.user, default_provider, gross)
    payment_method = _get_payment_method(request)
    pay_settings = load_payment_settings()
    paid_today = client_paid_today(request.user)
    loyalty_vouchers = active_vouchers(request.user)
    tier_pct = tier_discount_percent(request.user)
    level_name, _ = loyalty_level(request.user.loyalty_points)
    tier_preview = compute_loyalty_benefit(
        request.user, subtotal=total, delivery_fee=fee, voucher_code=""
    )
    return _render(
        request,
        "web/checkout.html",
        {
            "lines": lines,
            "pharmacy": pharmacy,
            "needs_rx": needs_rx,
            "rx_meds": rx_meds,
            "prescriptions": rx_qs,
            "subtotal": total,
            "delivery_fee": fee,
            "gross_total": gross,
            "total": max(0, gross - tier_preview["loyalty_discount"]),
            "loyalty_preview": tier_preview,
            "loyalty_vouchers": loyalty_vouchers,
            "loyalty_tier_percent": tier_pct,
            "loyalty_level_name": level_name,
            "premium_delivery": user_has_premium(request.user),
            "payment_methods": PAYMENT_METHODS,
            "payment_method": payment_method,
            "insurance_providers": insurance_providers,
            "client_profile": profile,
            "use_insurance_default": use_insurance_default,
            "initial_insurance_quote": initial_quote,
            "payment_cap": pay_settings.daily_transaction_cap,
            "payment_cap_remaining": max(0, pay_settings.daily_transaction_cap - paid_today),
            "active_nav": "orders",
        },
    )


def _fr_datetime(dt):
    months = {
        1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
        7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
    }
    local = timezone.localtime(dt)
    return f"{local.day} {months.get(local.month, '')} {local.year} à {local.strftime('%Hh%M')}"


@login_required(login_url="login")
def order_tracking_api(request, code):
    """Suivi temps réel d'une commande (JSON) — propriétaire uniquement."""
    from core.order_tracking import build_order_tracking_payload, get_delivery_for_order

    order = get_object_or_404(
        Order.objects.select_related("pharmacy", "linked_prescription", "insurance_provider"),
        code=code,
        client=request.user,
    )
    delivery = (
        Delivery.objects.filter(order=order)
        .select_related("courier", "courier__courier_profile")
        .first()
        if order.delivery_mode != Order.DeliveryMode.PICKUP
        else None
    )
    if delivery is None:
        delivery = get_delivery_for_order(order)
    return JsonResponse(build_order_tracking_payload(order, delivery))


@login_required(login_url="login")
def client_delivery_qr(request, code):
    """QR de livraison affiché sur le téléphone du patient (scan livreur)."""
    from django.http import HttpResponse

    from core.order_traceability import order_shows_client_delivery_qr, render_client_delivery_qr_png

    order = get_object_or_404(
        Order.objects.prefetch_related("items"),
        code=code,
        client=request.user,
    )
    if not order_shows_client_delivery_qr(order):
        return HttpResponse(status=404)
    return HttpResponse(render_client_delivery_qr_png(order), content_type="image/png")


@login_required(login_url="login")
def payment_confirmed(request, code):
    """Écran paiement confirmé + code de retrait / livraison (maquette)."""
    from datetime import timedelta

    from core.order_tracking import (
        build_courier_public_info,
        build_tracking_timeline,
        compute_tracking_metrics,
        get_delivery_for_order,
        order_tracking_steps,
    )
    from core.order_traceability import order_shows_client_delivery_qr
    from core.pharmacy_proximity import haversine_km
    from core.insurance import get_pending_insurance_claim, order_awaiting_insurance

    order = get_object_or_404(
        Order.objects.select_related("pharmacy", "insurance_provider", "linked_prescription")
        .prefetch_related("items__medicine", "insurance_claims"),
        code=code,
        client=request.user,
    )
    from orders.models import ensure_validation_code_normalized

    ensure_validation_code_normalized(order, save=True)
    raw = order.validation_code_display
    code_spaced = " ".join(list(raw))

    expires = order.created_at + timedelta(minutes=30)
    pharmacy = order.pharmacy
    distance_km = None
    if pharmacy and pharmacy.latitude and pharmacy.longitude:
        ulat = order.delivery_latitude or request.user.latitude
        ulng = order.delivery_longitude or request.user.longitude
        if ulat is not None and ulng is not None:
            distance_km = round(
                haversine_km(float(ulat), float(ulng), float(pharmacy.latitude), float(pharmacy.longitude)),
                1,
            )

    is_pickup = order.delivery_mode == Order.DeliveryMode.PICKUP
    if is_pickup:
        delivery_label = "Retrait en pharmacie"
    elif order.delivery_mode == Order.DeliveryMode.EXPRESS:
        delivery_label = "Livraison express en ~20 min"
    else:
        delivery_label = "Livraison à domicile en 30 min"

    pharmacy_location = ""
    if pharmacy:
        pharmacy_location = ", ".join(
            p for p in (getattr(pharmacy, "district", None) or "", pharmacy.city or "") if p
        ) or pharmacy.city or "Libreville"

    delivery = None
    if not is_pickup:
        delivery = (
            Delivery.objects.filter(order=order)
            .select_related("courier", "courier__courier_profile")
            .first()
            or get_delivery_for_order(order)
        )
    steps_done = order_tracking_steps(order, delivery)
    tracking_timeline = build_tracking_timeline(order, delivery)
    tracking_is_terminal = order.status in {
        Order.Status.DELIVERED,
        Order.Status.CANCELLED,
        Order.Status.REFUNDED,
    }
    tracking_url_name = (
        "bo_client_order_tracking" if in_client_espace(request) else "order_tracking"
    )
    delivery_qr_url_name = (
        "bo_client_order_delivery_qr" if in_client_espace(request) else "client_delivery_qr"
    )
    show_delivery_qr = order_shows_client_delivery_qr(order)
    courier_info = build_courier_public_info(delivery) if delivery else None
    tracking_metrics = compute_tracking_metrics(order, delivery) if delivery else None
    tracking_auto_open = bool(
        not tracking_is_terminal
        and delivery
        and order.status in {Order.Status.DELIVERING, Order.Status.READY}
    )
    insurance_pending = order_awaiting_insurance(order)
    insurance_claim = get_pending_insurance_claim(order)
    online_payment = (
        order.payments.filter(method__in=ONLINE_PAYMENT_METHODS).order_by("-created_at").first()
    )
    payment_pending_online = bool(
        online_payment and online_payment.status == Payment.Status.PROCESSING
    )

    return _render(
        request,
        "web/payment_confirmed.html",
        {
            "order": order,
            "items": order.items.all(),
            "pharmacy": pharmacy,
            "pharmacy_open": bool(pharmacy and pharmacy.status == Pharmacy.Status.ACTIVE),
            "pharmacy_location": pharmacy_location,
            "distance_km": distance_km,
            "code_raw": raw,
            "code_spaced": code_spaced,
            "expires_label": _fr_datetime(expires),
            "created_label": _fr_datetime(order.created_at),
            "is_pickup": is_pickup,
            "delivery_label": delivery_label,
            "active_nav": "orders",
            "delivery": delivery,
            "steps_done": steps_done,
            "tracking_timeline": tracking_timeline,
            "tracking_is_terminal": tracking_is_terminal,
            "tracking_api_url": reverse(tracking_url_name, kwargs={"code": code}),
            "show_client_delivery_qr": show_delivery_qr,
            "client_delivery_qr_url": reverse(delivery_qr_url_name, kwargs={"code": code})
            if show_delivery_qr
            else "",
            "courier_info": courier_info,
            "tracking_metrics": tracking_metrics,
            "tracking_auto_open": tracking_auto_open,
            "insurance_pending": insurance_pending,
            "insurance_claim": insurance_claim,
            "payment_pending_online": payment_pending_online,
            "online_payment": online_payment,
        },
    )


@login_required(login_url="login")
def emergency_page(request):
    """Bouton d'urgence — réservé au forfait Premium."""
    from core.pharmacy_chat import open_emergency_chat
    from core.pharmacy_proximity import pick_nearest_pharmacy, rank_pharmacies_for_emergency
    from notifications.models import EmergencyAlert, Notification
    from notifications.services import notify_user

    premium = user_has_premium(request.user)
    sos_category = request.session.get("sos_category", EmergencyAlert.Category.MEDICAL)
    post_lat = request.POST.get("latitude") or request.GET.get("lat")
    post_lng = request.POST.get("longitude") or request.GET.get("lng")

    ranked = (
        rank_pharmacies_for_emergency(
            request.user,
            category=sos_category,
            limit=5,
            post_lat=post_lat,
            post_lng=post_lng,
        )
        if premium
        else []
    )
    nearest = ranked[0] if ranked else None
    nearby = [r["pharmacy"] for r in ranked]
    history = (
        request.user.emergency_alerts.select_related("assigned_pharmacy").order_by("-created_at")[:10]
        if premium
        else EmergencyAlert.objects.none()
    )

    def _assign_and_message(alert, pharmacy, distance_km):
        alert.assigned_pharmacy = pharmacy
        alert.distance_km = distance_km
        alert.status = EmergencyAlert.Status.IN_PROGRESS
        alert.save(update_fields=["assigned_pharmacy", "distance_km", "status"])
        body = (
            f"[{alert.code}] Alerte {alert.get_category_display()} — "
            f"patient à {alert.address or request.user.display_location}. "
            f"Distance estimée : {distance_km} km. Merci de répondre rapidement."
        )
        conv = open_emergency_chat(user=request.user, pharmacy=pharmacy, alert=alert, auto_message=body)
        from core.pharmacy_notifications import notify_pharmacy_emergency

        notify_pharmacy_emergency(pharmacy, alert, body, conversation=conv)

    if request.method == "POST":
        if not premium:
            messages.error(request, "Le bouton d'urgence est inclus dans le forfait Premium.")
            return _p_redirect(request, "subscription_plans")
        action = request.POST.get("action")
        category = (
            request.POST.get("category")
            or request.session.get("sos_category")
            or EmergencyAlert.Category.MEDICAL
        )
        if category not in dict(EmergencyAlert.Category.choices):
            category = EmergencyAlert.Category.MEDICAL

        if post_lat and post_lng:
            try:
                request.user.latitude = Decimal(str(post_lat))
                request.user.longitude = Decimal(str(post_lng))
                request.user.save(update_fields=["latitude", "longitude", "updated_at"])
            except (InvalidOperation, ValueError):
                pass

        pick = pick_nearest_pharmacy(
            request.user, category=category, post_lat=post_lat, post_lng=post_lng
        )

        if action == "select_category":
            cat = request.POST.get("category")
            if cat in dict(EmergencyAlert.Category.choices):
                request.session["sos_category"] = cat
                request.session.modified = True
            return _p_redirect(request, "emergency")

        if not pick:
            messages.error(
                request,
                "Aucune pharmacie disponible à proximité. Appelez le 1410 immédiatement.",
            )
            return _p_redirect(request, "emergency")

        pharmacy = pick["pharmacy"]
        dist = pick["distance_km"]
        address = request.POST.get("address", "").strip() or request.user.display_location
        lat = request.user.latitude
        lng = request.user.longitude

        if action == "discuss":
            alert = EmergencyAlert.objects.create(
                client=request.user,
                category=category,
                latitude=lat,
                longitude=lng,
                address=address,
            )
            _assign_and_message(alert, pharmacy, dist)
            request.session.pop("sos_category", None)
            request.session.modified = True
            messages.success(
                request,
                f"Connexion à {pharmacy.name} ({dist} km) — la pharmacie la plus proche pour votre alerte.",
            )
            chat_url = (
                f"{portal_reverse(request, 'pharmacy_chat', pharmacy.slug)}"
                f"?from=urgence&alert={alert.id}"
            )
            return redirect(chat_url)

        if action == "alert":
            alert = EmergencyAlert.objects.create(
                client=request.user,
                category=category,
                latitude=lat,
                longitude=lng,
                address=address,
            )
            _assign_and_message(alert, pharmacy, dist)
            notify_user(
                request.user,
                "Alerte transmise",
                (
                    f"{pharmacy.name} (à {dist} km) a été assignée et prévenue. "
                    "Un fil de discussion est ouvert."
                ),
                notification_type=Notification.Type.HEALTH,
                data={"alert_id": alert.id, "pharmacy_id": pharmacy.id},
                critical=True,
            )
            request.session.pop("sos_category", None)
            request.session.modified = True
            messages.success(
                request,
                f"Alerte {alert.code} → {pharmacy.name} ({dist} km). Discutez avec la pharmacie assignée.",
            )
            chat_url = (
                f"{portal_reverse(request, 'pharmacy_chat', pharmacy.slug)}"
                f"?from=urgence&alert={alert.id}"
            )
            return redirect(chat_url)

        return _p_redirect(request, "emergency")

    return _render(
        request,
        "web/emergency.html",
        {
            "premium": premium,
            "nearby_pharmacies": nearby,
            "ranked_pharmacies": ranked,
            "nearest": nearest,
            "history": history,
            "user_location": request.user.display_location,
            "sos_category": sos_category,
            "sos_category_label": dict(EmergencyAlert.Category.choices).get(sos_category, ""),
            "categories": [
                (EmergencyAlert.Category.MEDICAL, "Problème médical", "favorite"),
                (EmergencyAlert.Category.ACCIDENT, "Accident", "personal_injury"),
                (EmergencyAlert.Category.CHILD, "Enfant malade", "child_care"),
                (EmergencyAlert.Category.OTHER, "Autre urgence", "warning"),
            ],
            "active_nav": "emergency",
        },
    )


@login_required(login_url="login")
def message_inbox(request):
    from notifications.models import PharmacyConversation

    convs = (
        PharmacyConversation.objects.filter(client=request.user)
        .select_related("pharmacy")
        .order_by("-updated_at")[:30]
    )
    pharmacies = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).order_by("name")[:40]
    return _render(
        request,
        "web/messages_inbox.html",
        {"conversations": convs, "pharmacies": pharmacies, "active_nav": "messages"},
    )


@login_required(login_url="login")
def pharmacy_chat(request, slug):
    from core.pharmacy_chat import get_or_create_conversation, send_pharmacy_message
    from notifications.models import Notification, PharmacyMessage

    pharmacy = get_object_or_404(Pharmacy, slug=slug, status=Pharmacy.Status.ACTIVE)
    alert = None
    alert_id = request.GET.get("alert")
    if alert_id:
        from notifications.models import EmergencyAlert

        alert = EmergencyAlert.objects.filter(
            pk=alert_id, client=request.user, assigned_pharmacy=pharmacy
        ).first()
    conv = get_or_create_conversation(
        request.user, pharmacy, emergency_alert=alert
    )
    from_urgence = request.GET.get("from") == "urgence"

    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        if body:
            send_pharmacy_message(
                conversation=conv,
                sender=request.user,
                body=body,
                notify_user=pharmacy.owner,
                notify_title=f"Message de {request.user.get_full_name() or request.user.username}",
                notify_message=f"{pharmacy.name} — {body[:120]}",
            )
            messages.success(request, "Message envoyé à la pharmacie.")
        return _p_redirect(request, "pharmacy_chat", slug=pharmacy.slug)

    conv.messages.exclude(sender=request.user).update(is_read=True)
    msgs = conv.messages.select_related("sender").order_by("created_at")

    return _render(
        request,
        "web/pharmacy_chat.html",
        {
            "pharmacy": pharmacy,
            "conversation": conv,
            "chat_messages": msgs,
            "emergency_alert": alert,
            "from_urgence": from_urgence,
            "active_nav": "emergency" if from_urgence else "messages",
        },
    )


def manifest(request):
    from django.http import JsonResponse

    return JsonResponse(
        {
            "name": "Gab'Pharma",
            "short_name": "Gab'Pharma",
            "description": "La santé à portée de main — Plateforme nationale du médicament",
            "start_url": "/",
            "display": "standalone",
            "theme_color": "#228545",
            "background_color": "#FFFFFF",
            "orientation": "any",
            "lang": "fr",
            "icons": [
                {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
        }
    )
