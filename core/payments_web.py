"""Création paiement web lors du checkout."""
import secrets

from payments.models import Payment
from orders.models import Order
from core.payment_settlement import (
    PaymentLimitError,
    check_daily_transaction_cap,
    cod_deposit_amount,
    ensure_order_settlement,
)


PAYMENT_METHODS = [
    (Payment.Method.MOOV, "Moov Money", "mobile"),
    (Payment.Method.AIRTEL, "Airtel Money", "mobile"),
    (Payment.Method.CARD, "Carte bancaire", "card"),
    (Payment.Method.COD, "Paiement à la livraison", "cod"),
    (Payment.Method.PHARMACY, "Paiement en pharmacie", "pharmacy"),
]

CLIENT_PAYMENT_METHODS = {m[0] for m in PAYMENT_METHODS}


def default_payment_method():
    return Payment.Method.COD


def valid_payment_method(method):
    return method in dict(Payment.Method.choices)


def valid_client_payment_method(method):
    return method in CLIENT_PAYMENT_METHODS


def create_insurance_payment(order):
    """Enregistre la part prise en charge par l'assurance."""
    if order.insurance_coverage <= 0 or not order.insurance_provider_id:
        return None
    ref = f"ASS-{order.code}-{secrets.token_hex(3).upper()}"
    return Payment.objects.create(
        order=order,
        method=Payment.Method.INSURANCE,
        amount=order.insurance_coverage,
        status=Payment.Status.PROCESSING,
        reference=ref,
        provider_response={
            "channel": "web",
            "provider_code": order.insurance_provider.code,
            "auto": True,
        },
    )


def create_order_payment(order, method):
    """Enregistre le paiement client (reste à charge après assurance)."""
    client_total = order.total
    payments = []

    ins = create_insurance_payment(order)
    if ins:
        payments.append(ins)

    if client_total <= 0:
        if order.status == Order.Status.PENDING:
            order.status = Order.Status.CONFIRMED
            order.save(update_fields=["status", "updated_at"])
        ensure_order_settlement(order)
        return payments[0] if payments else None

    check_daily_transaction_cap(order.client, client_total)

    if method not in CLIENT_PAYMENT_METHODS:
        method = default_payment_method()

    amount = client_total
    is_deposit = method == Payment.Method.COD
    if is_deposit:
        amount = cod_deposit_amount(client_total)

    if method in {Payment.Method.MOOV, Payment.Method.AIRTEL, Payment.Method.CARD}:
        status = Payment.Status.PROCESSING
    elif method == Payment.Method.COD:
        status = Payment.Status.PENDING
        order.deposit_amount = amount
        order.save(update_fields=["deposit_amount", "updated_at"])
    else:
        status = Payment.Status.PENDING

    ref = f"GP-{order.code}-{secrets.token_hex(3).upper()}"
    payment = Payment.objects.create(
        order=order,
        method=method,
        amount=amount if is_deposit else client_total,
        status=status,
        reference=ref,
        is_deposit=is_deposit,
        provider_response={"channel": "web", "simulated": True, "client_share": client_total},
    )
    payments.append(payment)

    if method in {Payment.Method.MOOV, Payment.Method.AIRTEL, Payment.Method.CARD}:
        payment.status = Payment.Status.SUCCESS
        payment.save(update_fields=["status", "updated_at"])
        if order.status == Order.Status.PENDING:
            order.status = Order.Status.CONFIRMED
            order.save(update_fields=["status", "updated_at"])

    ensure_order_settlement(order)
    return payment