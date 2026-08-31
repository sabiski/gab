"""Cohérence commande ↔ livraison (évite les « en livraison » orphelines)."""
from __future__ import annotations

from deliveries.models import Delivery
from orders.models import Order

from core.cart import ensure_delivery_for_order

ACTIVE_DELIVERY_STATUSES = frozenset(
    {
        Delivery.Status.ASSIGNED,
        Delivery.Status.PICKING_UP,
        Delivery.Status.PICKED_UP,
        Delivery.Status.IN_TRANSIT,
    }
)


def repair_order_delivery_state(order: Order) -> bool:
    """
    Corrige les désynchronisations courantes.
    Retourne True si la commande a été modifiée.
    """
    try:
        delivery = order.delivery
    except Delivery.DoesNotExist:
        delivery = None

    if delivery and delivery.status == Delivery.Status.DELIVERED:
        if order.status != Order.Status.DELIVERED:
            order.status = Order.Status.DELIVERED
            order.save(update_fields=["status", "updated_at"])
            return True
        return False

    if order.status != Order.Status.DELIVERING:
        return False

    orphan = delivery is None or (
        delivery.status == Delivery.Status.PENDING and not delivery.courier_id
    )
    if not orphan:
        return False

    order.status = Order.Status.READY
    order.save(update_fields=["status", "updated_at"])
    if order.delivery_mode != Order.DeliveryMode.PICKUP:
        ensure_delivery_for_order(order)
    return True


def repair_stale_delivering_orders(*, pharmacy_id: int | None = None) -> int:
    """Réaligne les commandes bloquées en « en livraison » sans course active."""
    qs = Order.objects.filter(status=Order.Status.DELIVERING).select_related("delivery")
    if pharmacy_id:
        qs = qs.filter(pharmacy_id=pharmacy_id)
    fixed = 0
    for order in qs:
        if repair_order_delivery_state(order):
            fixed += 1
    return fixed


def pharmacy_active_delivery_filter(qs):
    """Commandes réellement en cours de livraison (livreur assigné)."""
    return qs.filter(
        status=Order.Status.DELIVERING,
        delivery__courier__isnull=False,
        delivery__status__in=ACTIVE_DELIVERY_STATUSES,
    ).distinct()
