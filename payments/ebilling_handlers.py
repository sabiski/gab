"""Finalisation métier après callback E-Billing."""
from __future__ import annotations

import logging

from django.db import transaction

from orders.models import Order
from payments.ebilling import is_failed_state, is_paid_state, parse_callback_payload
from payments.models import Payment

logger = logging.getLogger("gabpharma.ebilling")


def apply_ebilling_callback(payload: dict) -> tuple[bool, str]:
    """
    Traite un callback PAYIN E-Billing (idempotent).
    Retourne (ok, message).
    """
    data = parse_callback_payload(payload)
    reference = data["reference"]
    if not reference:
        return False, "Référence manquante."

    payment = (
        Payment.objects.select_related("order", "order__client", "order__pharmacy")
        .filter(reference=reference)
        .order_by("-created_at")
        .first()
    )
    if not payment:
        logger.warning("Callback E-Billing : paiement inconnu reference=%s", reference)
        return False, "Paiement introuvable."

    if payment.status == Payment.Status.SUCCESS:
        return True, "Déjà traité."

    if data["amount"] is not None and int(data["amount"]) != int(payment.amount):
        logger.error(
            "Callback E-Billing : montant incorrect ref=%s attendu=%s reçu=%s",
            reference,
            payment.amount,
            data["amount"],
        )
        return False, "Montant incorrect."

    state = data["state"]
    if is_failed_state(state):
        payment.status = Payment.Status.FAILED
        provider = dict(payment.provider_response or {})
        provider["ebilling_callback"] = data["raw"]
        payment.provider_response = provider
        payment.save(update_fields=["status", "provider_response", "updated_at"])
        return True, "Paiement échoué enregistré."

    if not is_paid_state(state):
        return True, "État intermédiaire ignoré."

    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status == Payment.Status.SUCCESS:
            return True, "Déjà traité."
        provider = dict(payment.provider_response or {})
        provider.update(
            {
                "ebilling_bill_id": data["bill_id"] or provider.get("ebilling_bill_id"),
                "ebilling_transaction_id": data["transaction_id"],
                "ebilling_payment_system": data["payment_system"],
                "ebilling_callback": data["raw"],
            }
        )
        payment.provider_response = provider
        payment.status = Payment.Status.SUCCESS
        payment.save(update_fields=["status", "provider_response", "updated_at"])
        _finalize_order(payment)

    return True, "Paiement confirmé."


def _finalize_order(payment: Payment) -> None:
    from core.insurance import order_awaiting_insurance
    from core.pharmacy_notifications import notify_pharmacy_new_order
    from core.payment_settlement import ensure_order_settlement
    from notifications.models import Notification
    from notifications.services import notify_user

    order = payment.order
    if order.status == Order.Status.PENDING and not order_awaiting_insurance(order):
        order.status = Order.Status.CONFIRMED
        order.save(update_fields=["status", "updated_at"])

    ensure_order_settlement(order)

    if not order_awaiting_insurance(order):
        notify_pharmacy_new_order(order)

    client = order.client
    if client:
        notify_user(
            client,
            f"Paiement confirmé — {order.code}",
            f"Votre paiement de {payment.amount:,} FCFA a été reçu. Commande transmise à la pharmacie.",
            notification_type=Notification.Type.ORDER,
            data={"order_id": order.id, "code": order.code, "payment_id": payment.id},
            transactional=True,
        )
