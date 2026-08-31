"""Programme fidélité — points, récompenses, bons (CDC §4.15)."""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import (
    LoyaltyProgramSettings,
    LoyaltyReward,
    LoyaltyTransaction,
    LoyaltyVoucher,
    User,
)


class LoyaltyError(ValueError):
    pass


LEVEL_ORDER = ("Découverte", "Bronze", "Argent", "Or")
LEVEL_KEYS = {
    "Découverte": "decouverte",
    "Bronze": "bronze",
    "Argent": "argent",
    "Or": "or",
}


def load_loyalty_settings() -> LoyaltyProgramSettings:
    return LoyaltyProgramSettings.load()


def loyalty_level(points):
    pts = int(points or 0)
    if pts >= 2000:
        return "Or", 2000
    if pts >= 500:
        return "Argent", 2000
    if pts >= 100:
        return "Bronze", 500
    return "Découverte", 100


def tier_discount_percent(user) -> int:
    """Remise automatique liée au palier (non cumulable avec un bon)."""
    settings = load_loyalty_settings()
    level, _ = loyalty_level(user.loyalty_points)
    if level == "Or":
        return int(settings.gold_discount_percent)
    if level == "Argent":
        return int(settings.silver_discount_percent)
    if level == "Bronze":
        return int(settings.bronze_discount_percent)
    return 0


def _level_rank(level_name: str) -> int:
    try:
        return LEVEL_ORDER.index(level_name)
    except ValueError:
        return 0


def _user_meets_min_level(user, min_level: str) -> bool:
    if not min_level:
        return True
    user_level, _ = loyalty_level(user.loyalty_points)
    key = min_level.strip().lower()
    required = {
        "decouverte": 0,
        "bronze": 1,
        "argent": 2,
        "or": 3,
    }.get(key, 0)
    return _level_rank(user_level) >= required


def _generate_voucher_code() -> str:
    for _ in range(20):
        code = f"GP-{secrets.token_hex(3).upper()}"
        if not LoyaltyVoucher.objects.filter(code=code).exists():
            return code
    return f"GP-{secrets.token_hex(4).upper()}"


@transaction.atomic
def credit_loyalty_for_order(order):
    """Crédit points à la livraison confirmée."""
    client = order.client
    if not client or getattr(client, "role", None) != User.Role.CLIENT:
        return 0
    if LoyaltyTransaction.objects.filter(order=order, kind=LoyaltyTransaction.Kind.EARN).exists():
        return 0

    settings = load_loyalty_settings()
    rate = max(1, settings.points_per_1000_fcfa)
    points = max(1, int(order.total or 0) // 1000 * rate)
    if points <= 0:
        return 0

    client.loyalty_points = int(client.loyalty_points or 0) + points
    client.save(update_fields=["loyalty_points", "updated_at"])
    expires_at = timezone.now() + timedelta(days=settings.points_validity_days)
    LoyaltyTransaction.objects.create(
        user=client,
        kind=LoyaltyTransaction.Kind.EARN,
        points=points,
        balance_after=client.loyalty_points,
        reason=f"Livraison commande {order.code}",
        order=order,
        expires_at=expires_at,
    )
    return points


@transaction.atomic
def redeem_reward(user, reward_id):
    """Échange des points contre un bon de réduction."""
    reward = LoyaltyReward.objects.filter(pk=reward_id, is_active=True).first()
    if not reward:
        raise LoyaltyError("Récompense introuvable ou inactive.")
    if not _user_meets_min_level(user, reward.min_level):
        raise LoyaltyError("Palier fidélité insuffisant pour cette récompense.")

    balance = int(user.loyalty_points or 0)
    if balance < reward.points_cost:
        raise LoyaltyError(
            f"Solde insuffisant ({balance} pts) — il en faut {reward.points_cost}."
        )

    settings = load_loyalty_settings()
    user.loyalty_points = balance - reward.points_cost
    user.save(update_fields=["loyalty_points", "updated_at"])
    LoyaltyTransaction.objects.create(
        user=user,
        kind=LoyaltyTransaction.Kind.SPEND,
        points=-reward.points_cost,
        balance_after=user.loyalty_points,
        reason=f"Échange : {reward.label}",
        reward=reward,
    )

    free_delivery = reward.reward_type == LoyaltyReward.RewardType.FREE_DELIVERY
    voucher = LoyaltyVoucher.objects.create(
        user=user,
        reward=reward,
        code=_generate_voucher_code(),
        points_spent=reward.points_cost,
        discount_amount=0 if free_delivery else reward.value,
        free_delivery=free_delivery,
        expires_at=timezone.now() + timedelta(days=settings.voucher_validity_days),
    )
    return voucher


def active_vouchers(user):
    now = timezone.now()
    return LoyaltyVoucher.objects.filter(
        user=user,
        status=LoyaltyVoucher.Status.ACTIVE,
        expires_at__gt=now,
    ).select_related("reward")


def lookup_voucher(user, code: str):
    code = (code or "").strip().upper()
    if not code:
        return None
    now = timezone.now()
    return (
        LoyaltyVoucher.objects.filter(
            user=user,
            code__iexact=code,
            status=LoyaltyVoucher.Status.ACTIVE,
            expires_at__gt=now,
        )
        .select_related("reward")
        .first()
    )


def compute_loyalty_benefit(user, *, subtotal: int, delivery_fee: int, voucher_code: str = ""):
    """
    Calcule la réduction fidélité pour le checkout.
    Un bon de points remplace la remise de palier (non cumulable).
    """
    subtotal = int(subtotal or 0)
    delivery_fee = int(delivery_fee or 0)
    gross = subtotal + delivery_fee

    voucher = lookup_voucher(user, voucher_code) if voucher_code else None
    if voucher:
        if voucher.free_delivery:
            amount = min(delivery_fee, gross)
            return {
                "voucher": voucher,
                "tier_percent": 0,
                "loyalty_discount": amount,
                "free_delivery": True,
                "label": voucher.reward.label,
            }
        amount = min(voucher.discount_amount, gross)
        return {
            "voucher": voucher,
            "tier_percent": 0,
            "loyalty_discount": amount,
            "free_delivery": False,
            "label": voucher.reward.label,
        }

    percent = tier_discount_percent(user)
    if percent > 0:
        amount = min(int(subtotal * percent / 100), gross)
        level, _ = loyalty_level(user.loyalty_points)
        return {
            "voucher": None,
            "tier_percent": percent,
            "loyalty_discount": amount,
            "free_delivery": False,
            "label": f"Remise palier {level} ({percent} %)",
        }

    return {
        "voucher": None,
        "tier_percent": 0,
        "loyalty_discount": 0,
        "free_delivery": False,
        "label": "",
    }


@transaction.atomic
def apply_voucher_to_order(order, voucher: LoyaltyVoucher):
    if not voucher or voucher.status != LoyaltyVoucher.Status.ACTIVE:
        return
    voucher.status = LoyaltyVoucher.Status.USED
    voucher.used_at = timezone.now()
    voucher.order = order
    voucher.save(update_fields=["status", "used_at", "order"])
    order.loyalty_voucher = voucher
    order.save(update_fields=["loyalty_voucher", "updated_at"])


def points_expiring_soon(user, within_days: int | None = None):
    settings = load_loyalty_settings()
    days = within_days if within_days is not None else settings.expiry_warning_days
    deadline = timezone.now() + timedelta(days=days)
    return LoyaltyTransaction.objects.filter(
        user=user,
        kind=LoyaltyTransaction.Kind.EARN,
        expired_processed=False,
        expires_at__lte=deadline,
        expires_at__gt=timezone.now(),
    )


def sum_expiring_points(user, within_days: int | None = None) -> int:
    qs = points_expiring_soon(user, within_days)
    return sum(tx.points for tx in qs)


@transaction.atomic
def process_point_expirations():
    """Expire les points dont la date est dépassée (commande management)."""
    now = timezone.now()
    settings = load_loyalty_settings()
    warn_before = now + timedelta(days=settings.expiry_warning_days)
    expired_count = warned_count = 0

    for tx in LoyaltyTransaction.objects.filter(
        kind=LoyaltyTransaction.Kind.EARN,
        expired_processed=False,
        expires_at__lte=warn_before,
        expires_at__gt=now,
        expiry_notified=False,
    ).select_related("user"):
        from notifications.models import Notification
        from notifications.services import notify_user

        pts = sum_expiring_points(tx.user, settings.expiry_warning_days)
        if pts > 0:
            notify_user(
                tx.user,
                "Points fidélité bientôt expirés",
                f"{pts} point(s) expireront dans les {settings.expiry_warning_days} prochains jours. "
                "Échangez-les contre des récompenses depuis votre espace fidélité.",
                notification_type=Notification.Type.WARNING,
                transactional=True,
            )
            warned_count += 1
        LoyaltyTransaction.objects.filter(pk=tx.pk).update(expiry_notified=True)

    for tx in LoyaltyTransaction.objects.filter(
        kind=LoyaltyTransaction.Kind.EARN,
        expired_processed=False,
        expires_at__lte=now,
    ).select_related("user"):
        user = tx.user
        deduct = min(tx.points, int(user.loyalty_points or 0))
        if deduct <= 0:
            tx.expired_processed = True
            tx.save(update_fields=["expired_processed"])
            continue
        user.loyalty_points = int(user.loyalty_points or 0) - deduct
        user.save(update_fields=["loyalty_points", "updated_at"])
        LoyaltyTransaction.objects.create(
            user=user,
            kind=LoyaltyTransaction.Kind.EXPIRE,
            points=-deduct,
            balance_after=user.loyalty_points,
            reason=f"Expiration de points ({tx.created_at.date().isoformat()})",
        )
        tx.expired_processed = True
        tx.save(update_fields=["expired_processed"])
        expired_count += 1

    return {"expired_batches": expired_count, "warnings_sent": warned_count}
