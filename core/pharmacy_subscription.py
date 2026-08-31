"""Abonnement pharmacie — affichage et notifications (CDC)."""
from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from core.pharmacy_notifications import notify_pharmacy
from notifications.models import Notification
from payments.models import Subscription
from pharmacies.models import Pharmacy


PLAN_FEATURES = {
    "essential": [
        "Fiche pharmacie sur le site",
        "Gestion des commandes",
        "Stocks de base",
    ],
    "professional": [
        "Tout Essentiel",
        "Statistiques avancées",
        "Messagerie patients",
        "Alertes stock",
    ],
    "enterprise": [
        "Tout Professionnel",
        "Multi-utilisateurs & RH",
        "Priorité support",
        "Rapports exportables",
    ],
}


def active_pharmacy_subscription(pharmacy):
    """Abonnement actif le plus récent pour une officine."""
    if not pharmacy:
        return None
    return (
        pharmacy.subscriptions.filter(status=Subscription.Status.ACTIVE)
        .order_by("-created_at")
        .first()
    )


def subscription_summary(pharmacy):
    """Contexte d'affichage pour le portail pharmacie."""
    sub = active_pharmacy_subscription(pharmacy)
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
            "features": PLAN_FEATURES.get(sub.plan, []),
        }
    plan = pharmacy.subscription_plan if pharmacy else "none"

    return {
        "subscription": None,
        "plan": plan,
        "plan_label": dict(Pharmacy.SubscriptionPlan.choices).get(plan, "Aucun")
        if pharmacy
        else "Aucun",
        "status": None,
        "status_label": "Aucun forfait actif",
        "amount": None,
        "starts_at": None,
        "ends_at": None,
        "days_left": None,
        "is_active": False,
        "billing": None,
        "features": [],
    }


def notify_pharmacy_subscription(pharmacy, subscription, *, event: str = "activated"):
    """Notifie le personnel pharmacie d'un changement d'abonnement."""
    url = reverse("bo_pharmacy_subscription")
    if event == "activated":
        title = f"Forfait {subscription.get_plan_display()} activé"
        billing = (
            "facturation annuelle"
            if (subscription.ends_at - subscription.starts_at).days > 45
            else "facturation mensuelle"
        )
        message = (
            f"Gab'Pharma a activé votre abonnement {subscription.get_plan_display()} "
            f"({billing}) — {subscription.amount:,} FCFA. "
            f"Valide jusqu'au {subscription.ends_at.strftime('%d/%m/%Y')}."
        ).replace(",", " ")
        ntype = Notification.Type.SUCCESS
    elif event == "updated":
        title = f"Abonnement — {subscription.get_status_display()}"
        message = (
            f"Le statut de votre forfait {subscription.get_plan_display()} "
            f"est maintenant : {subscription.get_status_display()}."
        )
        ntype = Notification.Type.INFO
    else:
        title = "Abonnement pharmacie"
        message = subscription.get_plan_display()
        ntype = Notification.Type.INFO

    notify_pharmacy(
        pharmacy,
        title,
        message,
        notification_type=ntype,
        data={
            "event": "subscription",
            "subscription_id": subscription.id,
            "pharmacy_id": pharmacy.id,
            "url": url,
        },
        permission=None,
        transactional=True,
    )
