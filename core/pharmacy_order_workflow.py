"""Transitions de statut commande — portail pharmacie (CDC flux officine)."""
from __future__ import annotations

from orders.models import Order

# Statuts jamais dans le sélecteur « Mettre à jour » (automatiques ou flux dédié)
PHARMACY_MANUAL_ORDER_STATUSES = frozenset(
    {
        Order.Status.DELIVERED,
        Order.Status.REFUNDED,
    }
)


def pharmacy_selectable_statuses(order: Order) -> list[tuple[str, str]]:
    """Statuts que la pharmacie peut choisir pour cette commande."""
    labels = dict(Order.Status.choices)
    current = order.status

    if current in {
        Order.Status.CART,
        Order.Status.AWAITING_RX,
        Order.Status.CANCELLED,
        Order.Status.REFUNDED,
        Order.Status.DELIVERING,
        Order.Status.DELIVERED,
    }:
        return []

    allowed = _allowed_targets(order)
    ordered: list[str] = []
    for code in (current, *sorted(allowed - {current})):
        if code in allowed or code == current:
            if code not in ordered:
                ordered.append(code)

    return [(code, labels[code]) for code in ordered if code in labels]


def pharmacy_can_set_status(order: Order, new_status: str) -> bool:
    if new_status == order.status:
        return True
    return new_status in _allowed_targets(order)


def _allowed_targets(order: Order) -> set[str]:
    current = order.status
    is_pickup = order.delivery_mode == Order.DeliveryMode.PICKUP

    if current == Order.Status.PENDING:
        return {Order.Status.CONFIRMED, Order.Status.PREPARING, Order.Status.READY}
    if current == Order.Status.CONFIRMED:
        return {Order.Status.PREPARING, Order.Status.READY}
    if current == Order.Status.PREPARING:
        return {Order.Status.READY, Order.Status.CONFIRMED}
    if current == Order.Status.READY:
        if is_pickup:
            return {Order.Status.DELIVERED, Order.Status.PREPARING}
        return {Order.Status.PREPARING}
    return set()
