"""Notifications assureur — portail compagnie d'assurance."""
from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from accounts.models import InsurerPortalSettings, PartnerProfile
from core.insurer_claims import claim_display_reference
from core.insurer_profile import InsurerPortalProfile
from core.insurer_reports import _parse_date
from notifications.models import Notification, NotificationCampaign, NotificationPreference
from notifications.services import notify_user
from payments.models import InsuranceClaim

CATEGORY_UI = {
    "claim": ("Demande", "demand", "description", "purple"),
    "payment": ("Paiement", "payment", "payments", "green"),
    "alert": ("Alerte", "alert", "warning", "red"),
    "contract": ("Contrat", "contract", "article", "blue"),
    "system": ("Système", "system", "settings", "gray"),
    "reminder": ("Rappel", "reminder", "schedule", "amber"),
    "info": ("Information", "info", "info", "blue"),
}

TAB_FILTERS = {
    "all": Q(),
    "unread": Q(is_read=False),
    "system": Q(data__insurer_category="system") | Q(notification_type=Notification.Type.INFO, data__insurer_category__isnull=True),
    "alert": Q(data__insurer_category="alert") | Q(notification_type__in=[Notification.Type.WARNING, Notification.Type.ERROR, Notification.Type.HEALTH]),
    "reminder": Q(data__insurer_category="reminder"),
    "info": Q(data__insurer_category="info"),
}


def insurer_users_for_provider(provider_id: int):
    return PartnerProfile.objects.filter(
        partner_type=PartnerProfile.PartnerType.INSURER,
        insurance_provider_id=provider_id,
    ).select_related("user")


def notify_insurer_provider(
    provider,
    title: str,
    message: str,
    *,
    category: str = "info",
    notification_type=Notification.Type.INFO,
    data: dict | None = None,
    sync_key: str = "",
    transactional: bool = True,
):
    """Notifie tous les comptes partenaires rattachés à l'assureur."""
    if not provider:
        return
    payload = dict(data or {})
    payload["insurer_category"] = category
    if sync_key:
        payload["sync_key"] = sync_key
    for partner in insurer_users_for_provider(provider.pk):
        user = partner.user
        if sync_key and Notification.objects.filter(user=user, data__sync_key=sync_key).exists():
            continue
        settings = InsurerPortalSettings.for_partner(partner)
        if not settings.category_preferences.get(category, True):
            continue
        notify_user(
            user,
            title,
            message,
            notification_type=notification_type,
            data=payload,
            transactional=transactional,
        )


def on_insurance_claim_created(claim: InsuranceClaim) -> None:
    order = claim.order
    pharmacy = order.pharmacy if order else None
    ref = claim_display_reference(claim)
    pharmacy_label = pharmacy.name if pharmacy else "Pharmacie"
    city = pharmacy.city if pharmacy else ""
    notify_insurer_provider(
        claim.provider,
        f"Nouvelle demande de prise en charge — {ref}",
        (
            f"Demande de {claim.client.get_full_name() or claim.client.username} "
            f"pour {claim.amount:,} FCFA chez {pharmacy_label}."
        ).replace(",", " "),
        category="claim",
        notification_type=Notification.Type.ORDER,
        sync_key=f"claim_new_{claim.pk}",
        data={
            "claim_id": claim.pk,
            "claim_reference": ref,
            "pharmacy_name": pharmacy_label,
            "pharmacy_city": city,
            "amount": claim.amount,
        },
    )


def on_insurance_claim_status_changed(claim: InsuranceClaim, old_status: str) -> None:
    ref = claim_display_reference(claim)
    if claim.status == InsuranceClaim.Status.APPROVED:
        notify_insurer_provider(
            claim.provider,
            f"Prise en charge validée — {ref}",
            f"La demande {ref} a été approuvée ({claim.amount:,} FCFA).".replace(",", " "),
            category="claim",
            notification_type=Notification.Type.SUCCESS,
            sync_key=f"claim_approved_{claim.pk}",
            data={"claim_id": claim.pk, "claim_reference": ref},
        )
    elif claim.status == InsuranceClaim.Status.REJECTED:
        notify_insurer_provider(
            claim.provider,
            f"Demande refusée — {ref}",
            (claim.review_notes or f"La demande {ref} a été refusée.").strip(),
            category="alert",
            notification_type=Notification.Type.WARNING,
            sync_key=f"claim_rejected_{claim.pk}",
            data={"claim_id": claim.pk, "claim_reference": ref},
        )
    elif claim.status == InsuranceClaim.Status.PAID:
        notify_insurer_provider(
            claim.provider,
            f"Remboursement effectué — {ref}",
            f"Le remboursement de {claim.amount:,} FCFA pour {ref} est enregistré.".replace(",", " "),
            category="payment",
            notification_type=Notification.Type.SUCCESS,
            sync_key=f"claim_paid_{claim.pk}",
            data={"claim_id": claim.pk, "claim_reference": ref, "amount": claim.amount},
        )


def on_fraud_alert_created(alert) -> None:
    notify_insurer_provider(
        alert.insurance_provider,
        f"Alerte fraude — {alert.reference}",
        alert.detail or alert.get_alert_type_display(),
        category="alert",
        notification_type=Notification.Type.WARNING,
        sync_key=f"fraud_{alert.pk}",
        data={
            "fraud_alert_id": alert.pk,
            "fraud_reference": alert.reference,
            "claim_id": alert.claim_id,
            "alert_type": alert.alert_type,
        },
    )


def infer_category(notif: Notification) -> str:
    cat = (notif.data or {}).get("insurer_category")
    if cat:
        return cat
    if notif.notification_type in {Notification.Type.WARNING, Notification.Type.ERROR, Notification.Type.HEALTH}:
        return "alert"
    if notif.notification_type == Notification.Type.ORDER:
        return "claim"
    if notif.notification_type == Notification.Type.SUCCESS and (notif.data or {}).get("amount"):
        return "payment"
    title_l = notif.title.lower()
    if "fraude" in title_l or "alerte" in title_l:
        return "alert"
    if "contrat" in title_l or "police" in title_l:
        return "contract"
    if "rappel" in title_l:
        return "reminder"
    if "système" in title_l or "maintenance" in title_l:
        return "system"
    return "info"


def enrich_notification(notif: Notification) -> dict:
    cat_key = infer_category(notif)
    label, tab_key, icon, tone = CATEGORY_UI.get(cat_key, CATEGORY_UI["info"])
    data = notif.data or {}
    recipient = data.get("pharmacy_name") or data.get("client_name") or "—"
    recipient_sub = data.get("pharmacy_city") or data.get("insured_number") or ""
    ref = data.get("claim_reference") or data.get("fraud_reference") or ""
    subtitle = f"Demande #{ref}" if ref else (notif.message[:80] if notif.message else "")
    return {
        "id": notif.pk,
        "title": notif.title,
        "subtitle": subtitle,
        "message": notif.message,
        "type_label": label,
        "type_key": tab_key,
        "type_tone": tone,
        "icon": icon,
        "recipient": recipient,
        "recipient_sub": recipient_sub,
        "created_at": notif.created_at,
        "is_read": notif.is_read,
        "claim_id": data.get("claim_id"),
        "category": cat_key,
    }


def notifications_queryset(user):
    return Notification.objects.filter(user=user).order_by("-created_at")


def notifications_stats(user) -> dict:
    today = timezone.localdate()
    qs = notifications_queryset(user)
    total = qs.count()
    unread = qs.filter(is_read=False).count()
    month_start = today.replace(day=1)
    sent_month = qs.filter(created_at__date__gte=month_start).count()
    scheduled = NotificationCampaign.objects.filter(
        created_by=user,
        status=NotificationCampaign.Status.SCHEDULED,
    ).count()

    def pct(n):
        return round((n / total) * 100, 1) if total else 0.0

    return {
        "total": total,
        "unread": unread,
        "sent_month": sent_month,
        "scheduled": scheduled,
        "pct_total": 100.0 if total else 0.0,
        "pct_unread": pct(unread),
        "pct_sent_month": pct(sent_month),
        "pct_scheduled": pct(scheduled),
    }


def notifications_apply_tab(qs, tab: str):
    tab = (tab or "all").strip()
    filt = TAB_FILTERS.get(tab, TAB_FILTERS["all"])
    if tab == "unread":
        return qs.filter(is_read=False)
    if tab in {"system", "alert", "reminder", "info"}:
        return qs.filter(filt)
    return qs


def notifications_apply_search(qs, q: str):
    q = (q or "").strip()
    if not q:
        return qs
    return qs.filter(
        Q(title__icontains=q)
        | Q(message__icontains=q)
        | Q(data__claim_reference__icontains=q)
        | Q(data__pharmacy_name__icontains=q)
        | Q(data__fraud_reference__icontains=q)
    )


def notifications_apply_filters(qs, *, category: str | None = None, date_from=None, date_to=None):
    if category:
        qs = qs.filter(data__insurer_category=category)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


def tab_counts(user) -> dict:
    qs = notifications_queryset(user)
    return {
        "all": qs.count(),
        "unread": qs.filter(is_read=False).count(),
        "system": notifications_apply_tab(qs, "system").count(),
        "alert": notifications_apply_tab(qs, "alert").count(),
        "reminder": notifications_apply_tab(qs, "reminder").count(),
        "info": notifications_apply_tab(qs, "info").count(),
    }


def get_notification_preferences(user):
    prefs, _ = NotificationPreference.objects.get_or_create(user=user)
    return prefs


def portal_settings_for_profile(profile: InsurerPortalProfile):
    if profile._partner:
        return InsurerPortalSettings.for_partner(profile._partner)
    return None


def parse_notification_dates(request):
    date_from = _parse_date(request.GET.get("date_from", ""))
    date_to = _parse_date(request.GET.get("date_to", ""))
    if not date_from and not date_to:
        today = timezone.localdate()
        date_from = today.replace(day=1)
        date_to = today
    return date_from, date_to
