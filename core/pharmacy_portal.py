"""Helpers portail pharmacie — CDC §3.4."""
from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from accounts.models import CourierProfile, User
from core.pharmacy_proximity import haversine_km
from deliveries.models import Delivery
from notifications.models import EmergencyAlert, SupportTicket
from orders.models import Order, OrderItem
from payments.models import Payment

EMERGENCY_ESCALATION_MINUTES = 15
PREP_SLA_MINUTES = 45


def escalate_stale_emergencies(pharmacy):
    """Urgence non traitée → ticket support plateforme."""
    if not pharmacy:
        return 0
    cutoff = timezone.now() - timedelta(minutes=EMERGENCY_ESCALATION_MINUTES)
    alerts = EmergencyAlert.objects.filter(
        assigned_pharmacy=pharmacy,
        status__in={
            EmergencyAlert.Status.PENDING,
            EmergencyAlert.Status.IN_PROGRESS,
        },
        created_at__lt=cutoff,
        escalated_to_support=False,
    )
    count = 0
    for alert in alerts:
        SupportTicket.objects.create(
            client=alert.client,
            category=SupportTicket.Category.OTHER,
            subject=f"Urgence non traitée — {alert.code}",
            description=(
                f"Alerte {alert.get_category_display()} assignée à {pharmacy.name} "
                f"depuis plus de {EMERGENCY_ESCALATION_MINUTES} min sans réponse du patient."
            ),
            status=SupportTicket.Status.PENDING,
        )
        alert.escalated_to_support = True
        alert.escalated_at = timezone.now()
        alert.save(update_fields=["escalated_to_support", "escalated_at"])
        count += 1
    return count


def sync_pharmacy_emergencies(pharmacy):
    """
    Workflow automatique SOS :
    - assignation → prise en charge immédiate (IN_PROGRESS)
    - escalade support après délai
    - clôture des alertes très anciennes encore ouvertes
    """
    if not pharmacy:
        return
    now = timezone.now()
    EmergencyAlert.objects.filter(
        assigned_pharmacy=pharmacy,
        status=EmergencyAlert.Status.PENDING,
    ).update(status=EmergencyAlert.Status.IN_PROGRESS)
    escalate_stale_emergencies(pharmacy)
    stale_cutoff = now - timedelta(hours=48)
    EmergencyAlert.objects.filter(
        assigned_pharmacy=pharmacy,
        status=EmergencyAlert.Status.IN_PROGRESS,
        created_at__lt=stale_cutoff,
    ).update(status=EmergencyAlert.Status.RESOLVED, resolved_at=now)


def emergency_alerts_for_dashboard(pharmacy, *, limit=8):
    """Alertes SOS actives avec lien messagerie (sans action manuelle « Traiter »)."""
    from django.urls import reverse

    from notifications.models import PharmacyConversation

    if not pharmacy:
        return []
    sync_pharmacy_emergencies(pharmacy)
    alerts = list(
        EmergencyAlert.objects.filter(
            assigned_pharmacy=pharmacy,
            status__in={
                EmergencyAlert.Status.PENDING,
                EmergencyAlert.Status.IN_PROGRESS,
            },
        )
        .select_related("client")
        .order_by("-created_at")[:limit]
    )
    if not alerts:
        return []
    alert_ids = [a.id for a in alerts]
    client_ids = [a.client_id for a in alerts]
    conv_by_alert = {
        row["emergency_alert_id"]: row["id"]
        for row in PharmacyConversation.objects.filter(
            pharmacy=pharmacy, emergency_alert_id__in=alert_ids
        ).values("id", "emergency_alert_id")
    }
    conv_by_client = {
        row["client_id"]: row["id"]
        for row in PharmacyConversation.objects.filter(
            pharmacy=pharmacy, client_id__in=client_ids
        ).values("id", "client_id")
    }
    for alert in alerts:
        conv_id = conv_by_alert.get(alert.id) or conv_by_client.get(alert.client_id)
        if conv_id:
            alert.chat_url = f"{reverse('bo_pharmacy_messages')}?conv={conv_id}"
        else:
            alert.chat_url = reverse("bo_pharmacy_messages")
    return alerts


def nearby_couriers(pharmacy, limit=8):
    """Livreurs en ligne triés par distance à la pharmacie."""
    if not pharmacy or pharmacy.latitude is None or pharmacy.longitude is None:
        return []
    lat, lng = float(pharmacy.latitude), float(pharmacy.longitude)
    ranked = []
    qs = User.objects.filter(
        role=User.Role.COURIER,
        courier_profile__courier_status=CourierProfile.CourierStatus.ONLINE,
    ).select_related("courier_profile")
    for c in qs:
        clat = c.latitude
        clng = c.longitude
        if clat is None or clng is None:
            dist = 99.0
        else:
            dist = haversine_km(lat, lng, float(clat), float(clng))
        ranked.append({"courier": c, "distance_km": round(dist, 1)})
    ranked.sort(key=lambda x: x["distance_km"])
    return ranked[:limit]


def pending_courier_requests(pharmacy):
    """Livraisons en attente d'un livreur pour cette pharmacie."""
    if not pharmacy:
        return Delivery.objects.none()
    return (
        Delivery.objects.filter(
            order__pharmacy=pharmacy,
            status=Delivery.Status.PENDING,
            courier__isnull=True,
            order__status__in={Order.Status.READY, Order.Status.DELIVERING},
        )
        .select_related("order", "order__client")
        .order_by("-created_at")
    )


def payment_breakdown(pharmacy, since=None):
    """CA ventilé par mode de paiement."""
    if not pharmacy:
        return []
    qs = Payment.objects.filter(
        order__pharmacy=pharmacy,
        status__in={Payment.Status.SUCCESS, Payment.Status.PROCESSING},
    )
    if since:
        qs = qs.filter(created_at__gte=since)
    rows = (
        qs.values("method")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    labels = dict(Payment.Method.choices)
    return [
        {
            "method": r["method"],
            "label": labels.get(r["method"], r["method"]),
            "total": r["total"] or 0,
            "count": r["count"],
        }
        for r in rows
    ]


def top_sold_products(pharmacy, since=None, limit=5):
    if not pharmacy:
        return []
    qs = OrderItem.objects.filter(
        order__pharmacy=pharmacy,
        order__status__in={
            Order.Status.CONFIRMED,
            Order.Status.PREPARING,
            Order.Status.READY,
            Order.Status.DELIVERING,
            Order.Status.DELIVERED,
        },
    )
    if since:
        qs = qs.filter(order__created_at__gte=since)
    return list(
        qs.values("medicine_name")
        .annotate(qty=Sum("quantity"))
        .order_by("-qty")[:limit]
    )


def sales_by_category(pharmacy, since=None):
    if not pharmacy:
        return []
    qs = OrderItem.objects.filter(
        order__pharmacy=pharmacy,
        order__status__in={
            Order.Status.CONFIRMED,
            Order.Status.PREPARING,
            Order.Status.READY,
            Order.Status.DELIVERING,
            Order.Status.DELIVERED,
        },
    ).select_related("medicine__category")
    if since:
        qs = qs.filter(order__created_at__gte=since)
    buckets = {}
    for item in qs:
        cat = (
            item.medicine.category.name
            if item.medicine_id and item.medicine.category_id
            else "Autre"
        )
        buckets[cat] = buckets.get(cat, 0) + item.line_total
    return sorted(
        [{"category": k, "total": v} for k, v in buckets.items()],
        key=lambda x: -x["total"],
    )[:8]


def delivery_performance(pharmacy):
    if not pharmacy:
        return {}
    delivered = Delivery.objects.filter(
        order__pharmacy=pharmacy,
        status=Delivery.Status.DELIVERED,
        delivered_at__isnull=False,
        picked_up_at__isnull=False,
    )
    durations = []
    for d in delivered[:200]:
        mins = (d.delivered_at - d.picked_up_at).total_seconds() / 60
        if mins > 0:
            durations.append(mins)
    avg_min = int(sum(durations) / len(durations)) if durations else None
    total = Delivery.objects.filter(order__pharmacy=pharmacy).count()
    ok = delivered.count()
    return {
        "delivered_count": ok,
        "avg_delivery_min": avg_min,
        "on_time_rate": int((ok / total) * 100) if total else 0,
        "avg_distance_km": delivered.aggregate(a=Sum("distance_km"))["a"],
    }


def estimate_margin(pharmacy, since=None):
    """Marge estimée à partir du prix d'achat stock (ou 70 % du prix vente)."""
    from catalog.models import PharmacyStock

    if not pharmacy:
        return 0
    qs = OrderItem.objects.filter(
        order__pharmacy=pharmacy,
        order__status__in={
            Order.Status.DELIVERED,
            Order.Status.DELIVERING,
            Order.Status.READY,
        },
    ).select_related("medicine", "order")
    if since:
        qs = qs.filter(order__created_at__gte=since)
    margin = 0
    stock_cache = {}
    for item in qs:
        key = item.medicine_id
        if key not in stock_cache:
            s = PharmacyStock.objects.filter(pharmacy=pharmacy, medicine_id=key).first()
            stock_cache[key] = s
        stock = stock_cache[key]
        unit_sale = item.unit_price
        if stock and stock.purchase_price:
            cost = stock.purchase_price
        else:
            cost = int(unit_sale * 0.7)
        margin += (unit_sale - cost) * item.quantity
    return max(0, margin)


def process_order_refund(order):
    """Remboursement auto si commande payée annulée — statut commande → Remboursée."""
    updated = 0
    for p in order.payments.filter(
        status__in={Payment.Status.SUCCESS, Payment.Status.PROCESSING}
    ):
        p.status = Payment.Status.REFUNDED
        p.save(update_fields=["status", "updated_at"])
        updated += 1
    if updated and order.status == Order.Status.CANCELLED:
        order.status = Order.Status.REFUNDED
        order.save(update_fields=["status", "updated_at"])
    return updated


def mark_preparing(order):
    if order.status == Order.Status.PREPARING and not order.preparing_at:
        order.preparing_at = timezone.now()
        order.save(update_fields=["preparing_at", "updated_at"])
