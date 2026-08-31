"""Évaluation post-livraison pharmacie / livreur (CDC §4.7)."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count
from django.urls import reverse

from accounts.models import CourierProfile, User
from deliveries.models import Delivery
from orders.models import Order, OrderEvaluation
from pharmacies.models import Pharmacy


class EvaluationError(ValueError):
    pass


def _clamp_rating(value, default=5):
    try:
        rating = int(value)
    except (TypeError, ValueError):
        rating = default
    return max(1, min(5, rating))


def order_needs_courier_rating(order) -> bool:
    return order.delivery_mode != Order.DeliveryMode.PICKUP


def refresh_pharmacy_rating(pharmacy: Pharmacy):
    agg = OrderEvaluation.objects.filter(pharmacy=pharmacy).aggregate(
        avg=Avg("pharmacy_rating"),
        count=Count("id"),
    )
    pharmacy.rating = round(float(agg["avg"] or 0), 2)
    pharmacy.review_count = agg["count"] or 0
    pharmacy.save(update_fields=["rating", "review_count", "updated_at"])


def refresh_courier_rating(courier: User):
    profile, _ = CourierProfile.objects.get_or_create(user=courier)
    agg = OrderEvaluation.objects.filter(
        courier=courier,
        courier_rating__isnull=False,
    ).aggregate(avg=Avg("courier_rating"), count=Count("id"))
    profile.rating = Decimal(str(round(float(agg["avg"] or 5), 2)))
    profile.total_deliveries = agg["count"] or profile.total_deliveries or 0
    profile.save(update_fields=["rating", "total_deliveries"])


@transaction.atomic
def submit_order_evaluation(order, user, *, pharmacy_rating, pharmacy_comment="", courier_rating=None, courier_comment=""):
    if order.client_id != user.id:
        raise EvaluationError("Cette commande ne vous appartient pas.")
    if order.status != Order.Status.DELIVERED:
        raise EvaluationError("L'évaluation est disponible après livraison confirmée.")
    if hasattr(order, "delivery_evaluation"):
        raise EvaluationError("Vous avez déjà évalué cette commande.")

    delivery = Delivery.objects.filter(order=order).select_related("courier").first()
    needs_courier = order_needs_courier_rating(order) and delivery and delivery.courier_id

    pr = _clamp_rating(pharmacy_rating)
    cr = None
    if needs_courier:
        if courier_rating is None:
            raise EvaluationError("Notez également le livreur.")
        cr = _clamp_rating(courier_rating)
    elif courier_rating is not None:
        cr = _clamp_rating(courier_rating)

    evaluation = OrderEvaluation.objects.create(
        order=order,
        client=user,
        pharmacy=order.pharmacy,
        pharmacy_rating=pr,
        pharmacy_comment=(pharmacy_comment or "").strip()[:1000],
        courier=delivery.courier if delivery and delivery.courier_id else None,
        courier_rating=cr,
        courier_comment=(courier_comment or "").strip()[:1000],
    )
    if order.pharmacy_id:
        refresh_pharmacy_rating(order.pharmacy)
    if evaluation.courier_id:
        refresh_courier_rating(evaluation.courier)
    return evaluation


def prompt_order_evaluation(order):
    """Notification invitant le client à noter pharmacie et livreur."""
    if order.status != Order.Status.DELIVERED:
        return
    if OrderEvaluation.objects.filter(order=order).exists():
        return
    from notifications.models import Notification
    from notifications.services import notify_user

    url = reverse("bo_client_order_detail", kwargs={"pk": order.pk})
    notify_user(
        order.client,
        f"Évaluez votre commande {order.code}",
        "Comment s'est passée votre livraison ? Notez la pharmacie"
        + (" et le livreur." if order_needs_courier_rating(order) else "."),
        notification_type=Notification.Type.ORDER,
        data={"order_id": order.id, "evaluate": True, "url": url},
        transactional=True,
    )


def handle_order_delivered(order, delivery=None):
    """Effets de bord à la clôture livraison : gains, fidélité, invitation à noter."""
    from core.loyalty import credit_loyalty_for_order
    from core.payment_settlement import record_courier_earning
    from notifications.models import Notification
    from notifications.services import notify_user

    if delivery is None:
        delivery = Delivery.objects.filter(order=order).first()
    if delivery and delivery.courier_id and delivery.status == Delivery.Status.DELIVERED:
        record_courier_earning(delivery)
    pts = credit_loyalty_for_order(order)
    if pts:
        notify_user(
            order.client,
            "Points fidélité crédités",
            f"+{pts} point(s) pour la commande {order.code}.",
            notification_type=Notification.Type.SUCCESS,
            data={"order_id": order.id, "points": pts},
            transactional=True,
        )
    prompt_order_evaluation(order)
