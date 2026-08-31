"""Outils admin pour incidents livraison (CDC §3.1 — Centre de gestion des incidents)."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from accounts.models import User
from core.delivery_transfer import find_couriers_near_delivery
from deliveries.models import Delivery, DeliveryIncident
from notifications.models import Notification
from notifications.services import notify_user


def notify_support_on_incident(incident: DeliveryIncident):
    """Alerte le personnel support / admin lors d'un nouvel incident."""
    order = incident.delivery.order
    title = f"Incident livraison — {order.code}"
    message = (
        f"{incident.get_incident_type_display()} · "
        f"{incident.get_priority_display()} · "
        f"{incident.description[:120] if incident.description else 'Sans description'}"
    )
    data = {
        "incident_id": incident.id,
        "order_id": order.id,
        "event": "delivery_incident",
    }
    staff = User.objects.filter(
        role__in={User.Role.SUPPORT, User.Role.ADMIN, User.Role.SUPERADMIN},
        status=User.Status.ACTIVE,
    )
    for user in staff:
        notify_user(
            user,
            title,
            message,
            notification_type=Notification.Type.ERROR
            if incident.priority in {DeliveryIncident.Priority.HIGH, DeliveryIncident.Priority.URGENT}
            else Notification.Type.WARNING,
            data=data,
            critical=incident.incident_type == DeliveryIncident.Type.ACCIDENT,
            transactional=True,
        )


def incident_priority_for_type(incident_type: str) -> str:
    """Accident → priorité urgente (CDC)."""
    if incident_type == DeliveryIncident.Type.ACCIDENT:
        return DeliveryIncident.Priority.URGENT
    if incident_type in {
        DeliveryIncident.Type.VEHICLE_BREAKDOWN,
        DeliveryIncident.Type.CLIENT_REFUSES_PAY,
        DeliveryIncident.Type.ADDRESS_NOT_FOUND,
    }:
        return DeliveryIncident.Priority.HIGH
    return DeliveryIncident.Priority.MEDIUM


def nearby_couriers_for_incident(incident: DeliveryIncident, *, limit=8):
    """Livreurs disponibles à proximité avec distance et charge du jour."""
    delivery = incident.delivery
    exclude = delivery.courier_id
    ranked = find_couriers_near_delivery(delivery, exclude_user_id=exclude, limit=limit)
    today = timezone.localdate()
    for row in ranked:
        courier = row["courier"]
        row["deliveries_today"] = Delivery.objects.filter(
            courier=courier,
            created_at__date=today,
        ).exclude(status=Delivery.Status.DELIVERED).count()
    return ranked


def incident_resolution_stats(*, days: int = 30) -> dict:
    """Taux de résolution des incidents (indicateur plateforme)."""
    since = timezone.now() - timedelta(days=days)
    qs = DeliveryIncident.objects.filter(created_at__gte=since)
    total = qs.count()
    resolved = qs.filter(status=DeliveryIncident.Status.RESOLVED).count()
    open_count = qs.filter(
        status__in={DeliveryIncident.Status.OPEN, DeliveryIncident.Status.IN_PROGRESS}
    ).count()
    escalated = qs.filter(status=DeliveryIncident.Status.ESCALATED).count()
    rate = int((resolved / total) * 100) if total else 100
    return {
        "total": total,
        "resolved": resolved,
        "open": open_count,
        "escalated": escalated,
        "resolution_rate": rate,
        "period_days": days,
    }
