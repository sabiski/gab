"""Notifications in-app pour le portail pharmacie."""
from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.pharmacy_access import PERM_ORDERS, PERM_STOCKS, get_role_permissions_map
from notifications.models import Notification
from notifications.routing import pharmacy_order_url, resolve_notification_url
from notifications.services import notify_user
from pharmacies.models import Pharmacy, PharmacyEmployee


def pharmacy_staff_users(pharmacy, permission=None):
    """Utilisateurs actifs de la pharmacie, filtrés par permission métier."""
    if not pharmacy:
        return User.objects.none()
    user_ids = set()
    if pharmacy.owner_id:
        owner = pharmacy.owner
        if permission is None or permission in get_role_permissions_map().get(
            PharmacyEmployee.JobRole.OWNER, set()
        ):
            user_ids.add(owner.pk)
    employees = PharmacyEmployee.objects.filter(
        pharmacy=pharmacy, is_active=True
    ).select_related("user")
    for emp in employees:
        if emp.user_id and (
            permission is None or permission in get_role_permissions_map().get(emp.job_role, set())
        ):
            user_ids.add(emp.user_id)
    return User.objects.filter(pk__in=user_ids, is_active=True)


def notify_pharmacy(
    pharmacy,
    title,
    message,
    *,
    notification_type=Notification.Type.INFO,
    data=None,
    permission=None,
    critical=False,
):
    """Diffuse une notification à tout le personnel habilité."""
    if not pharmacy:
        return []
    data = dict(data or {})
    created = []
    for user in pharmacy_staff_users(pharmacy, permission=permission):
        notif = notify_user(
            user,
            title,
            message,
            notification_type=notification_type,
            data=data,
            critical=critical,
            transactional=True,
        )
        if notif:
            created.append(notif)
    return created


def notify_pharmacy_new_order(order):
    """Alerte la pharmacie lors d'une nouvelle commande client."""
    if not order.pharmacy_id:
        return
    client_name = order.client.get_full_name() or order.client.username
    urgent = " — URGENT" if order.is_urgent else ""
    rx_hint = ""
    from orders.models import Order

    if order.status == Order.Status.AWAITING_RX:
        rx_hint = " Ordonnance à valider."
    url = pharmacy_order_url(order.id)
    notify_pharmacy(
        order.pharmacy,
        f"Nouvelle commande {order.code}{urgent}",
        f"{client_name} — {order.total} F.{rx_hint}",
        notification_type=Notification.Type.ORDER,
        data={
            "order_id": order.id,
            "code": order.code,
            "url": url,
            "event": "new_order",
        },
        permission=PERM_ORDERS,
    )


def notify_pharmacy_message(conversation, title, message):
    """Alerte le personnel lors d'un message patient."""
    url = f"{reverse('bo_pharmacy_messages')}?conv={conversation.id}"
    notify_pharmacy(
        conversation.pharmacy,
        title,
        message,
        notification_type=Notification.Type.INFO,
        data={
            "conversation_id": conversation.id,
            "pharmacy_id": conversation.pharmacy_id,
            "url": url,
            "event": "message",
        },
        permission=PERM_ORDERS,
    )


def notify_pharmacy_emergency(pharmacy, alert, body, *, conversation=None):
    """Alerte critique urgence SOS."""
    from notifications.models import PharmacyConversation
    from notifications.routing import emergency_messages_url

    if conversation is None:
        conversation = PharmacyConversation.objects.filter(
            pharmacy=pharmacy, emergency_alert=alert
        ).first()
    payload = {
        "alert_id": alert.id,
        "pharmacy_id": pharmacy.id,
        "conversation_id": conversation.id if conversation else None,
    }
    url = emergency_messages_url(payload)
    notify_pharmacy(
        pharmacy,
        f"Urgence assignée — {alert.code}",
        body[:250],
        notification_type=Notification.Type.ERROR,
        data={**payload, "url": url, "event": "emergency"},
        permission=PERM_ORDERS,
        critical=True,
    )


def _recent_stock_alert(user, stock_id, alert_kind):
    since = timezone.now() - timedelta(hours=6)
    return Notification.objects.filter(
        user=user,
        notification_type=Notification.Type.WARNING,
        created_at__gte=since,
        data__stock_id=stock_id,
        data__alert_kind=alert_kind,
    ).exists()


def check_stock_alert(stock, *, previous_qty=None):
    """
    Notifie si rupture ou stock faible (avec anti-spam 6 h par type).
    previous_qty permet de ne notifier qu'au franchissement du seuil.
    """
    pharmacy = stock.pharmacy
    qty = stock.quantity
    threshold = stock.low_stock_threshold or 0
    medicine = str(stock.medicine)
    url = f"{reverse('bo_pharmacy_stocks')}?q={stock.medicine.name}"

    if qty <= 0:
        if previous_qty is not None and previous_qty <= 0:
            return
        title = f"Rupture de stock — {medicine}"
        message = f"{medicine} est en rupture ({qty} unité(s))."
        alert_kind = "out"
    elif qty <= threshold:
        if previous_qty is not None and previous_qty <= threshold:
            return
        title = f"Stock faible — {medicine}"
        message = f"{medicine} : {qty} restant(s) (seuil {threshold})."
        alert_kind = "low"
    else:
        return

    for user in pharmacy_staff_users(pharmacy, permission=PERM_STOCKS):
        if _recent_stock_alert(user, stock.id, alert_kind):
            continue
        notify_user(
            user,
            title,
            message,
            notification_type=Notification.Type.WARNING,
            data={
                "stock_id": stock.id,
                "medicine_id": stock.medicine_id,
                "alert_kind": alert_kind,
                "url": url,
                "event": "stock_alert",
            },
            transactional=True,
        )


def notification_target_url(notif):
    """URL de destination pour une notification pharmacie."""
    return resolve_notification_url(notif) or reverse("bo_pharmacy_notifications")
