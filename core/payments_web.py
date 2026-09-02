"""Création paiement web lors du checkout."""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from django.conf import settings

from payments.ebilling import EbillingError, create_ebill, ebilling_configured
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
ONLINE_PAYMENT_METHODS = {
    Payment.Method.MOOV,
    Payment.Method.AIRTEL,
    Payment.Method.CARD,
}


@dataclass
class PaymentFlowResult:
    payment: Payment | None = None
    redirect_url: str | None = None
    pending_online: bool = False


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


def _payer_contact(user):
    email = (getattr(user, "email", "") or "").strip()
    name = (user.get_full_name() or user.username or "Client").strip()
    phone = (getattr(user, "phone", "") or "").strip()
    return email, name, phone


def _start_ebilling_payment(payment, user, *, method: str, return_url: str) -> str | None:
    email, name, phone = _payer_contact(user)
    if not email:
        raise EbillingError("Ajoutez une adresse e-mail à votre profil pour payer en ligne.")

    bill = create_ebill(
        amount=int(payment.amount),
        reference=payment.reference,
        payer_email=email,
        payer_name=name,
        payer_msisdn=phone,
        description=f"Commande Gab'Pharma {payment.order.code}",
        return_url=return_url,
        payment_method=method,
    )
    provider = dict(payment.provider_response or {})
    provider.update(
        {
            "channel": "ebilling",
            "ebilling_bill_id": bill.bill_id,
            "ebilling_env": getattr(settings, "EBILLING_ENV", "lab"),
            "ebilling_flow": getattr(settings, "EBILLING_FLOW", "redirect"),
        }
    )
    if bill.raw:
        provider["ebilling_create"] = bill.raw
    payment.provider_response = provider
    payment.status = Payment.Status.PROCESSING
    payment.save(update_fields=["status", "provider_response", "updated_at"])
    return bill.redirect_url


def create_order_payment(order, method, *, return_url: str = "") -> PaymentFlowResult:
    """Enregistre le paiement client (reste à charge après assurance)."""
    client_total = order.total
    payments: list[Payment] = []

    ins = create_insurance_payment(order)
    if ins:
        payments.append(ins)

    if client_total <= 0:
        if order.status == Order.Status.PENDING:
            order.status = Order.Status.CONFIRMED
            order.save(update_fields=["status", "updated_at"])
        ensure_order_settlement(order)
        return PaymentFlowResult(payment=payments[0] if payments else None)

    check_daily_transaction_cap(order.client, client_total)

    if method not in CLIENT_PAYMENT_METHODS:
        method = default_payment_method()

    amount = client_total
    is_deposit = method == Payment.Method.COD
    if is_deposit:
        amount = cod_deposit_amount(client_total)

    if method in ONLINE_PAYMENT_METHODS:
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
        provider_response={"channel": "web"},
    )
    payments.append(payment)

    if method in ONLINE_PAYMENT_METHODS:
        if ebilling_configured():
            try:
                redirect = _start_ebilling_payment(
                    payment,
                    order.client,
                    method=method,
                    return_url=return_url,
                )
                return PaymentFlowResult(
                    payment=payment,
                    redirect_url=redirect,
                    pending_online=True,
                )
            except EbillingError:
                payment.status = Payment.Status.FAILED
                payment.save(update_fields=["status", "updated_at"])
                raise
        # Secours développement sans credentials E-Billing
        payment.status = Payment.Status.SUCCESS
        payment.provider_response = {
            "channel": "web",
            "simulated": True,
            "client_share": client_total,
        }
        payment.save(update_fields=["status", "provider_response", "updated_at"])
        if order.status == Order.Status.PENDING:
            order.status = Order.Status.CONFIRMED
            order.save(update_fields=["status", "updated_at"])
        ensure_order_settlement(order)
        return PaymentFlowResult(payment=payment)

    ensure_order_settlement(order)
    return PaymentFlowResult(payment=payment)
