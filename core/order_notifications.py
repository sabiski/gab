"""Notifications commande / livraison — client, pharmacie, livreur."""
from __future__ import annotations

from notifications.models import Notification
from notifications.routing import pharmacy_order_url
from notifications.services import notify_user

from core.pharmacy_notifications import PERM_ORDERS, notify_pharmacy


def _courier_name(delivery) -> str:
    if not delivery.courier_id:
        return "Le livreur"
    return delivery.courier.get_full_name() or delivery.courier.username


def notify_pharmacy_courier_assigned(delivery) -> None:
    order = delivery.order
    if not order.pharmacy_id:
        return
    notify_pharmacy(
        order.pharmacy,
        f"Livreur assigné — {order.code}",
        f"{_courier_name(delivery)} a pris en charge la livraison.",
        notification_type=Notification.Type.DELIVERY,
        data={
            "order_id": order.id,
            "code": order.code,
            "delivery_id": delivery.id,
            "url": pharmacy_order_url(order.id),
            "event": "courier_assigned",
        },
        permission=PERM_ORDERS,
    )


def notify_delivery_outcome(delivery, *, success: bool, reason: str = "") -> None:
    """Informe pharmacie et livreur du succès ou de l'échec de la livraison."""
    order = delivery.order
    code = order.code
    courier_name = _courier_name(delivery)
    client_name = order.client.get_full_name() or order.client.username
    url = pharmacy_order_url(order.id) if order.pharmacy_id else ""

    if success:
        if order.pharmacy_id:
            notify_pharmacy(
                order.pharmacy,
                f"Livraison réussie — {code}",
                f"{client_name} a bien reçu sa commande ({courier_name}).",
                notification_type=Notification.Type.SUCCESS,
                data={
                    "order_id": order.id,
                    "code": code,
                    "delivery_id": delivery.id,
                    "url": url,
                    "event": "delivery_success",
                },
                permission=PERM_ORDERS,
            )
        if delivery.courier_id:
            notify_user(
                delivery.courier,
                f"Livraison confirmée — {code}",
                "Bravo ! La livraison a été validée avec succès.",
                notification_type=Notification.Type.SUCCESS,
                data={
                    "order_id": order.id,
                    "code": code,
                    "delivery_id": delivery.id,
                    "event": "delivery_success",
                },
                transactional=True,
            )
        return

    reason_text = (reason or order.cancellation_reason or "Échec de livraison").strip()
    if order.pharmacy_id:
        notify_pharmacy(
            order.pharmacy,
            f"Échec livraison — {code}",
            f"{reason_text} · Livreur : {courier_name}.",
            notification_type=Notification.Type.ERROR,
            data={
                "order_id": order.id,
                "code": code,
                "delivery_id": delivery.id,
                "url": url,
                "event": "delivery_failed",
                "reason": reason_text,
            },
            permission=PERM_ORDERS,
            critical=True,
        )
    if delivery.courier_id:
        notify_user(
            delivery.courier,
            f"Livraison échouée — {code}",
            reason_text,
            notification_type=Notification.Type.ERROR,
            data={
                "order_id": order.id,
                "code": code,
                "delivery_id": delivery.id,
                "event": "delivery_failed",
                "reason": reason_text,
            },
            transactional=True,
        )
