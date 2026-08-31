"""Règles financières CDC §4.8 — commission, plafonds, rémunération livreur."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import models
from django.db.models import Sum
from django.utils import timezone

from deliveries.models import Delivery
from orders.models import Order
from payments.models import (
    CourierEarning,
    OrderSettlement,
    Payment,
    PlatformPaymentSettings,
)


class PaymentLimitError(ValueError):
    """Plafond journalier de transaction dépassé."""


def load_payment_settings() -> PlatformPaymentSettings:
    return PlatformPaymentSettings.load()


def client_paid_today(user, *, on_date=None) -> int:
    """Montant déjà payé avec succès par le client aujourd'hui (hors assurance)."""
    if not user:
        return 0
    day = on_date or timezone.localdate()
    total = (
        Payment.objects.filter(
            order__client=user,
            status=Payment.Status.SUCCESS,
            created_at__date=day,
        )
        .exclude(method=Payment.Method.INSURANCE)
        .aggregate(s=Sum("amount"))["s"]
    )
    return int(total or 0)


def check_daily_transaction_cap(user, amount: int):
    """Lève PaymentLimitError si le plafond journalier serait dépassé."""
    settings = load_payment_settings()
    amount = int(amount or 0)
    if amount <= 0:
        return
    already = client_paid_today(user)
    if already + amount > settings.daily_transaction_cap:
        remaining = max(0, settings.daily_transaction_cap - already)
        raise PaymentLimitError(
            f"Plafond journalier atteint ({settings.daily_transaction_cap:,} FCFA). "
            f"Reste disponible aujourd'hui : {remaining:,} FCFA."
        )


def cod_deposit_amount(client_total: int) -> int:
    settings = load_payment_settings()
    if client_total <= 0:
        return 0
    pct = max(1, min(100, settings.cod_deposit_rate))
    deposit = int(Decimal(client_total) * Decimal(pct) / Decimal(100))
    return max(settings.cod_deposit_min, deposit)


def _commission_amount(product_base: int, rate: Decimal) -> int:
    if product_base <= 0:
        return 0
    value = (Decimal(product_base) * rate / Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(value)


def build_settlement_preview(order: Order) -> dict:
    settings = load_payment_settings()
    product_base = max(0, order.subtotal - order.discount)
    commission = _commission_amount(product_base, settings.platform_commission_rate)
    pharmacy_net = max(0, product_base - commission)
    delivery_fee = order.delivery_fee or 0
    payout_due = timezone.now() + timedelta(days=settings.payout_delay_days)
    return {
        "product_base": product_base,
        "platform_commission": commission,
        "pharmacy_net": pharmacy_net,
        "delivery_fee": delivery_fee,
        "courier_earning": 0,
        "platform_delivery_margin": delivery_fee,
        "payout_due_at": payout_due,
    }


def ensure_order_settlement(order: Order) -> OrderSettlement:
    """Crée ou met à jour le règlement pharmacie à la confirmation de commande."""
    preview = build_settlement_preview(order)
    settlement, created = OrderSettlement.objects.get_or_create(
        order=order,
        defaults={
            "product_base": preview["product_base"],
            "platform_commission": preview["platform_commission"],
            "pharmacy_net": preview["pharmacy_net"],
            "delivery_fee": preview["delivery_fee"],
            "platform_delivery_margin": preview["delivery_fee"],
            "payout_due_at": preview["payout_due_at"],
            "pharmacy_payout_status": OrderSettlement.PharmacyPayoutStatus.SCHEDULED,
            "courier_payout_status": OrderSettlement.CourierPayoutStatus.NA
            if order.delivery_mode == Order.DeliveryMode.PICKUP
            else OrderSettlement.CourierPayoutStatus.PENDING,
        },
    )
    if not created:
        settlement.product_base = preview["product_base"]
        settlement.platform_commission = preview["platform_commission"]
        settlement.pharmacy_net = preview["pharmacy_net"]
        settlement.delivery_fee = preview["delivery_fee"]
        settlement.payout_due_at = preview["payout_due_at"]
        if order.delivery_mode != Order.DeliveryMode.PICKUP:
            settlement.courier_payout_status = OrderSettlement.CourierPayoutStatus.PENDING
        settlement.save(
            update_fields=[
                "product_base",
                "platform_commission",
                "pharmacy_net",
                "delivery_fee",
                "payout_due_at",
                "courier_payout_status",
                "updated_at",
            ]
        )
    return settlement


def calculate_courier_earning(delivery: Delivery) -> dict:
    settings = load_payment_settings()
    order = delivery.order
    base = settings.courier_base_fee
    distance_km = float(delivery.distance_km or 0)
    distance_bonus = int(distance_km * settings.courier_per_km_fee)
    express_bonus = (
        settings.courier_express_bonus
        if order.delivery_mode == Order.DeliveryMode.EXPRESS
        else 0
    )
    total = base + distance_bonus + express_bonus
    delivery_fee = order.delivery_fee or 0
    if delivery_fee > 0:
        total = min(total, delivery_fee)
    platform_margin = max(0, delivery_fee - total)
    return {
        "base_fee": base,
        "distance_bonus": distance_bonus,
        "express_bonus": express_bonus,
        "total": total,
        "platform_delivery_margin": platform_margin,
    }


def record_courier_earning(delivery: Delivery):
    """Enregistre la rémunération livreur à la clôture de livraison."""
    if delivery.status != Delivery.Status.DELIVERED or not delivery.courier_id:
        return None
    try:
        existing = delivery.earning
    except CourierEarning.DoesNotExist:
        existing = None
    if existing:
        return existing

    breakdown = calculate_courier_earning(delivery)
    earning = CourierEarning.objects.create(
        delivery=delivery,
        courier=delivery.courier,
        base_fee=breakdown["base_fee"],
        distance_bonus=breakdown["distance_bonus"],
        express_bonus=breakdown["express_bonus"],
        total=breakdown["total"],
    )

    preview = build_settlement_preview(delivery.order)
    settlement, _ = OrderSettlement.objects.get_or_create(
        order=delivery.order,
        defaults={
            "product_base": preview["product_base"],
            "platform_commission": preview["platform_commission"],
            "pharmacy_net": preview["pharmacy_net"],
            "delivery_fee": preview["delivery_fee"],
            "platform_delivery_margin": preview["delivery_fee"],
            "payout_due_at": preview["payout_due_at"],
            "pharmacy_payout_status": OrderSettlement.PharmacyPayoutStatus.SCHEDULED,
        },
    )
    settlement.courier_earning = breakdown["total"]
    settlement.platform_delivery_margin = breakdown["platform_delivery_margin"]
    settlement.courier_payout_status = OrderSettlement.CourierPayoutStatus.PENDING
    settlement.save(
        update_fields=[
            "courier_earning",
            "platform_delivery_margin",
            "courier_payout_status",
            "updated_at",
        ]
    )
    return earning


def pharmacy_settlement_summary(pharmacy, *, since=None) -> dict:
    qs = OrderSettlement.objects.filter(order__pharmacy=pharmacy)
    if since:
        qs = qs.filter(created_at__gte=since)
    agg = qs.aggregate(
        gross=Sum("product_base"),
        commission=Sum("platform_commission"),
        net=Sum("pharmacy_net"),
        pending=Sum("pharmacy_net", filter=models.Q(pharmacy_payout_status__in=["pending", "scheduled"])),
    )
    return {
        "gross": int(agg["gross"] or 0),
        "commission": int(agg["commission"] or 0),
        "net": int(agg["net"] or 0),
        "pending_payout": int(agg["pending"] or 0),
    }


def courier_earnings_summary(courier, *, since=None) -> dict:
    qs = CourierEarning.objects.filter(courier=courier)
    if since:
        qs = qs.filter(created_at__gte=since)
    agg = qs.aggregate(
        sum_total=Sum("total"),
        sum_pending=Sum("total", filter=models.Q(status=CourierEarning.Status.PENDING)),
        sum_paid=Sum("total", filter=models.Q(status=CourierEarning.Status.PAID)),
    )
    return {
        "total": int(agg["sum_total"] or 0),
        "pending": int(agg["sum_pending"] or 0),
        "paid": int(agg["sum_paid"] or 0),
    }
