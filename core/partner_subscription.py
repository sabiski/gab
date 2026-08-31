"""Abonnement plateforme partenaire (assureurs) — facturation Gab'Pharma."""
from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from accounts.models import PartnerProfile, PartnerSubscription
from notifications.models import Notification
from notifications.services import notify_user


INSURER_PLAN_AMOUNTS = {
    PartnerSubscription.Plan.STANDARD: 150_000,
    PartnerSubscription.Plan.PROFESSIONAL: 350_000,
    PartnerSubscription.Plan.ENTERPRISE: 750_000,
}

INSURER_PLAN_FEATURES = {
    PartnerSubscription.Plan.STANDARD: [
        "Portail assureur (demandes, assurés)",
        "Jusqu'à 5 000 bénéficiaires",
        "Support e-mail",
    ],
    PartnerSubscription.Plan.PROFESSIONAL: [
        "Tout Standard",
        "Statistiques et rapports avancés",
        "Alertes fraude",
        "Bénéficiaires illimités",
    ],
    PartnerSubscription.Plan.ENTERPRISE: [
        "Tout Professionnel",
        "API et intégrations",
        "Account manager dédié",
        "SLA prioritaire",
    ],
}


def active_partner_subscription(partner: PartnerProfile | None) -> PartnerSubscription | None:
    if not partner:
        return None
    today = timezone.localdate()
    return (
        partner.platform_subscriptions.filter(
            status=PartnerSubscription.Status.ACTIVE,
            ends_at__gte=today,
        )
        .order_by("-created_at")
        .first()
    )


def partner_has_platform_access(partner: PartnerProfile | None, *, allow_pending: bool = False) -> bool:
    sub = active_partner_subscription(partner)
    if sub:
        return True
    if allow_pending and partner:
        return partner.platform_subscriptions.filter(
            status=PartnerSubscription.Status.PENDING
        ).exists()
    return False


def subscription_period(*, annual: bool = False) -> tuple:
    start = timezone.localdate()
    end = start + timedelta(days=365 if annual else 30)
    return start, end


def compute_insurer_amount(plan: str, *, annual: bool = False) -> int:
    base = INSURER_PLAN_AMOUNTS.get(plan, INSURER_PLAN_AMOUNTS[PartnerSubscription.Plan.STANDARD])
    if annual:
        return int(base * 12 * 0.85)
    return base


def create_partner_subscription(
    partner: PartnerProfile,
    plan: str,
    *,
    annual: bool = False,
    activate: bool = False,
    payment_reference: str = "",
    payment_method: str = "",
) -> PartnerSubscription:
    starts_at, ends_at = subscription_period(annual=annual)
    amount = compute_insurer_amount(plan, annual=annual)
    sub = PartnerSubscription.objects.create(
        partner=partner,
        plan=plan,
        amount=amount,
        status=PartnerSubscription.Status.ACTIVE if activate else PartnerSubscription.Status.PENDING,
        starts_at=starts_at,
        ends_at=ends_at,
        payment_reference=payment_reference,
        payment_method=payment_method,
        paid_at=timezone.now() if activate else None,
    )
    if activate:
        notify_partner_subscription(partner, sub, event="activated")
    return sub


def record_subscription_payment(
    sub: PartnerSubscription,
    *,
    payment_reference: str = "",
    payment_method: str = "",
) -> PartnerSubscription:
    sub.status = PartnerSubscription.Status.ACTIVE
    sub.payment_reference = payment_reference or sub.payment_reference
    sub.payment_method = payment_method or sub.payment_method
    sub.paid_at = timezone.now()
    sub.save(
        update_fields=[
            "status",
            "payment_reference",
            "payment_method",
            "paid_at",
        ]
    )
    notify_partner_subscription(sub.partner, sub, event="activated")
    return sub


def partner_subscription_summary(partner: PartnerProfile | None) -> dict:
    sub = active_partner_subscription(partner)
    today = timezone.localdate()
    if sub:
        days_left = (sub.ends_at - today).days
        billing = "Annuelle" if (sub.ends_at - sub.starts_at).days > 45 else "Mensuelle"
        return {
            "subscription": sub,
            "plan": sub.plan,
            "plan_label": sub.get_plan_display(),
            "status": sub.status,
            "status_label": sub.get_status_display(),
            "amount": sub.amount,
            "starts_at": sub.starts_at,
            "ends_at": sub.ends_at,
            "days_left": max(days_left, 0),
            "is_active": True,
            "billing": billing,
            "features": INSURER_PLAN_FEATURES.get(sub.plan, []),
        }
    pending = None
    if partner:
        pending = (
            partner.platform_subscriptions.filter(status=PartnerSubscription.Status.PENDING)
            .order_by("-created_at")
            .first()
        )
    return {
        "subscription": pending,
        "plan": pending.plan if pending else None,
        "plan_label": pending.get_plan_display() if pending else "Aucun forfait actif",
        "status": pending.status if pending else None,
        "status_label": pending.get_status_display() if pending else "Aucun forfait actif",
        "amount": pending.amount if pending else None,
        "starts_at": pending.starts_at if pending else None,
        "ends_at": pending.ends_at if pending else None,
        "days_left": None,
        "is_active": False,
        "billing": None,
        "features": [],
    }


def notify_partner_subscription(partner: PartnerProfile, subscription: PartnerSubscription, *, event: str = "activated"):
    user = partner.user
    if event == "activated":
        title = f"Forfait plateforme {subscription.get_plan_display()} activé"
        billing = (
            "facturation annuelle"
            if (subscription.ends_at - subscription.starts_at).days > 45
            else "facturation mensuelle"
        )
        message = (
            f"Gab'Pharma a activé votre abonnement plateforme {subscription.get_plan_display()} "
            f"({billing}) — {subscription.amount:,} FCFA. "
            f"Valide jusqu'au {subscription.ends_at.strftime('%d/%m/%Y')}."
        ).replace(",", " ")
        ntype = Notification.Type.SUCCESS
    elif event == "updated":
        title = f"Abonnement plateforme — {subscription.get_status_display()}"
        message = (
            f"Le statut de votre forfait {subscription.get_plan_display()} "
            f"est maintenant : {subscription.get_status_display()}."
        )
        ntype = Notification.Type.INFO
    else:
        title = "Abonnement plateforme"
        message = subscription.get_plan_display()
        ntype = Notification.Type.INFO

    notify_user(
        user,
        title,
        message,
        notification_type=ntype,
        data={
            "event": "platform_subscription",
            "subscription_id": subscription.id,
            "partner_id": partner.id,
            "url": reverse("bo_insurer_dashboard"),
        },
        transactional=True,
    )


def insurer_partners_for_admin():
    return PartnerProfile.objects.filter(
        partner_type=PartnerProfile.PartnerType.INSURER,
        validated_at__isnull=False,
    ).select_related("user", "insurance_provider").order_by("organization_name")
