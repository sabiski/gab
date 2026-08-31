"""Transfert sécurisé de livraison entre livreurs (CDC §4.14)."""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.utils import timezone

from accounts.models import CourierProfile, User
from core.pharmacy_proximity import haversine_km
from deliveries.models import Delivery, DeliveryIncident, DeliveryStep
from notifications.models import Notification
from notifications.services import notify_user

TRANSFER_ESCALATION_MINUTES = 30


def generate_transfer_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def log_delivery_step(delivery: Delivery, label: str, status: str = ""):
    DeliveryStep.objects.create(
        delivery=delivery,
        label=label,
        status=status or delivery.status,
    )


def find_couriers_near_delivery(delivery: Delivery, *, exclude_user_id=None, limit=8):
    """Livreurs en ligne proches du point de transfert (position livreur ou pharmacie)."""
    lat = lng = None
    if delivery.courier_lat is not None and delivery.courier_lng is not None:
        lat, lng = float(delivery.courier_lat), float(delivery.courier_lng)
    elif delivery.order.pharmacy_id and delivery.order.pharmacy.latitude is not None:
        lat = float(delivery.order.pharmacy.latitude)
        lng = float(delivery.order.pharmacy.longitude)
    elif delivery.order.delivery_latitude is not None:
        lat = float(delivery.order.delivery_latitude)
        lng = float(delivery.order.delivery_longitude)

    qs = User.objects.filter(
        role=User.Role.COURIER,
        courier_profile__courier_status=CourierProfile.CourierStatus.ONLINE,
        status=User.Status.ACTIVE,
    ).select_related("courier_profile")
    if exclude_user_id:
        qs = qs.exclude(pk=exclude_user_id)

    ranked = []
    for courier in qs:
        if courier.latitude is None or courier.longitude is None:
            dist = 99.0
        elif lat is None:
            dist = 50.0
        else:
            dist = haversine_km(lat, lng, float(courier.latitude), float(courier.longitude))
        ranked.append({"courier": courier, "distance_km": round(dist, 1)})
    ranked.sort(key=lambda row: row["distance_km"])
    return ranked[:limit]


def _notify(user, title, message, data=None):
    if not user:
        return
    notify_user(
        user,
        title,
        message,
        notification_type=Notification.Type.DELIVERY,
        data=data or {},
        transactional=True,
    )


def clear_pending_transfer(delivery: Delivery, *, save: bool = False) -> bool:
    """Clôture un transfert non abouti quand la course se termine (livraison ou échec)."""
    changed_fields: list[str] = []
    if delivery.transfer_code:
        delivery.transfer_code = ""
        changed_fields.append("transfer_code")
    if delivery.transfer_requested_at is not None:
        delivery.transfer_requested_at = None
        changed_fields.append("transfer_requested_at")
    if delivery.handoff_courier_id:
        delivery.handoff_courier = None
        changed_fields.append("handoff_courier")
    if not changed_fields:
        return False

    delivery.incidents.filter(
        status__in={
            DeliveryIncident.Status.OPEN,
            DeliveryIncident.Status.IN_PROGRESS,
        }
    ).update(status=DeliveryIncident.Status.RESOLVED, resolved_at=timezone.now())

    if save:
        changed_fields.append("updated_at")
        delivery.save(update_fields=changed_fields)
    return True


def request_delivery_handoff(
    delivery: Delivery,
    courier: User,
    *,
    incident_type: str,
    description: str = "",
    priority: str = DeliveryIncident.Priority.MEDIUM,
):
    """
    Le livreur initial signale qu'il ne peut plus poursuivre.
    Il reste responsable tant que le code n'est pas validé par le remplaçant.
    """
    if delivery.courier_id != courier.id:
        raise ValueError("Vous n'êtes pas le livreur assigné à cette course.")
    if delivery.status in {
        Delivery.Status.DELIVERED,
        Delivery.Status.FAILED,
        Delivery.Status.PENDING,
    }:
        raise ValueError("Cette livraison ne peut pas être transférée.")
    if delivery.transfer_requested_at and delivery.transfer_code:
        raise ValueError("Un transfert est déjà en cours pour cette course.")

    code = generate_transfer_code()
    delivery.transfer_code = code
    delivery.transfer_requested_at = timezone.now()
    delivery.handoff_courier = None
    delivery.save(
        update_fields=[
            "transfer_code",
            "transfer_requested_at",
            "handoff_courier",
            "updated_at",
        ]
    )

    incident = DeliveryIncident.objects.create(
        delivery=delivery,
        reported_by=courier,
        incident_type=incident_type,
        description=description,
        priority=priority,
        status=DeliveryIncident.Status.IN_PROGRESS,
    )
    log_delivery_step(delivery, "Demande de transfert initiée", delivery.status)

    order = delivery.order
    for row in find_couriers_near_delivery(delivery, exclude_user_id=courier.id, limit=5):
        c = row["courier"]
        _notify(
            c,
            "Course à reprendre",
            f"Transfert demandé pour {order.code} (~{row['distance_km']} km). Acceptez puis saisissez le code remis sur place.",
            {"delivery_id": delivery.id, "order_id": order.id, "event": "handoff_offer"},
        )

    if order.pharmacy and order.pharmacy.owner_id:
        _notify(
            order.pharmacy.owner,
            f"Changement de livreur — {order.code}",
            "Un transfert de course est en cours. Vous serez informé à la reprise.",
            {"order_id": order.id},
        )
    _notify(
        order.client,
        f"Livreur en cours de changement — {order.code}",
        "Un nouveau livreur reprendra votre livraison. Votre code de remise reste inchangé.",
        {"order_id": order.id, "code": order.code},
    )

    return code, incident


def accept_handoff_offer(delivery: Delivery, courier: User):
    """Le livreur remplaçant accepte la reprise (avant saisie du code sur place)."""
    if not delivery.transfer_code or not delivery.transfer_requested_at:
        raise ValueError("Aucune demande de transfert active.")
    if delivery.handoff_courier_id and delivery.handoff_courier_id != courier.id:
        raise ValueError("Un autre livreur a déjà accepté cette reprise.")
    if delivery.courier_id == courier.id:
        raise ValueError("Vous êtes déjà le livreur assigné.")
    if courier.courier_profile.courier_status == CourierProfile.CourierStatus.OFFLINE:
        raise ValueError("Passez en ligne pour accepter une reprise.")

    delivery.handoff_courier = courier
    delivery.save(update_fields=["handoff_courier", "updated_at"])
    log_delivery_step(delivery, f"Reprise acceptée par {courier}", delivery.status)

    _notify(
        delivery.courier,
        f"Reprise acceptée — {delivery.order.code}",
        f"{courier.get_full_name() or courier.username} vient récupérer le colis. Communiquez-lui le code de transfert.",
        {"delivery_id": delivery.id, "order_id": delivery.order_id},
    )
    return delivery


def validate_handoff_code(delivery: Delivery, courier: User, code: str):
    """Validation du code de transfert par le livreur remplaçant."""
    code = (code or "").strip()
    if not code:
        raise ValueError("Saisissez le code de transfert.")
    if delivery.handoff_courier_id != courier.id:
        raise ValueError("Vous devez d'abord accepter cette reprise de course.")
    if not delivery.transfer_code:
        raise ValueError("Aucun transfert en attente de validation.")
    if not secrets.compare_digest(delivery.transfer_code, code):
        raise ValueError("Code de transfert incorrect.")

    previous_courier = delivery.courier
    delivery.courier = courier
    delivery.transfer_code = ""
    delivery.handoff_courier = None
    delivery.transfer_completed_at = timezone.now()
    delivery.transfer_requested_at = None
    delivery.save(
        update_fields=[
            "courier",
            "transfer_code",
            "handoff_courier",
            "transfer_completed_at",
            "transfer_requested_at",
            "updated_at",
        ]
    )

    delivery.incidents.filter(
        status__in={
            DeliveryIncident.Status.OPEN,
            DeliveryIncident.Status.IN_PROGRESS,
        }
    ).update(status=DeliveryIncident.Status.RESOLVED, resolved_at=timezone.now())

    log_delivery_step(delivery, "Transfert validé — responsabilité transférée", delivery.status)

    order = delivery.order
    _notify(
        order.client,
        f"Nouveau livreur — {order.code}",
        "Un livreur a repris votre commande. Votre code de validation client est inchangé.",
        {"order_id": order.id},
    )
    if order.pharmacy and order.pharmacy.owner_id:
        _notify(
            order.pharmacy.owner,
            f"Livraison reprise — {order.code}",
            f"{courier.get_full_name() or courier.username} a confirmé la reprise du colis.",
            {"order_id": order.id},
        )
    if previous_courier:
        _notify(
            previous_courier,
            f"Transfert terminé — {order.code}",
            "La responsabilité de la course a été transférée avec succès.",
            {"delivery_id": delivery.id},
        )
        prev_profile = CourierProfile.objects.filter(user=previous_courier).first()
        if prev_profile:
            prev_profile.courier_status = CourierProfile.CourierStatus.ONLINE
            prev_profile.save(update_fields=["courier_status"])

    new_profile = CourierProfile.objects.filter(user=courier).first()
    if new_profile:
        new_profile.courier_status = CourierProfile.CourierStatus.BUSY
        new_profile.save(update_fields=["courier_status"])

    return delivery


def admin_prepare_handoff(delivery: Delivery, new_courier: User, incident: DeliveryIncident | None = None):
    """Réaffectation admin/support : le remplaçant doit valider le code."""
    if not delivery.courier_id:
        delivery.courier = new_courier
        delivery.status = Delivery.Status.ASSIGNED
        delivery.save(update_fields=["courier", "status", "updated_at"])
        _notify(
            new_courier,
            "Livraison assignée",
            f"Course {delivery.order.code} vous a été assignée.",
            {"delivery_id": delivery.id},
        )
        if incident:
            incident.status = DeliveryIncident.Status.RESOLVED
            incident.resolved_at = timezone.now()
            incident.save(update_fields=["status", "resolved_at"])
        return None

    code = generate_transfer_code()
    delivery.transfer_code = code
    delivery.transfer_requested_at = timezone.now()
    delivery.handoff_courier = new_courier
    delivery.save(
        update_fields=[
            "transfer_code",
            "transfer_requested_at",
            "handoff_courier",
            "updated_at",
        ]
    )
    log_delivery_step(delivery, "Transfert initié par le support", delivery.status)
    _notify(
        new_courier,
        "Reprise de course",
        f"Récupérez le colis {delivery.order.code} et saisissez le code de transfert communiqué par le livreur initial.",
        {"delivery_id": delivery.id, "order_id": delivery.order_id},
    )
    if delivery.courier_id:
        _notify(
            delivery.courier,
            f"Transfert programmé — {delivery.order.code}",
            f"Remettez le colis à {new_courier.get_full_name() or new_courier.username} avec le code de transfert.",
            {"delivery_id": delivery.id},
        )
    if incident:
        incident.status = DeliveryIncident.Status.IN_PROGRESS
        incident.save(update_fields=["status"])
    return code


def escalate_stale_transfers():
    """Escalade les transferts non résolus au-delà du délai paramétrable."""
    cutoff = timezone.now() - timedelta(minutes=TRANSFER_ESCALATION_MINUTES)
    qs = Delivery.objects.filter(
        transfer_requested_at__lt=cutoff,
        transfer_code__gt="",
    ).exclude(status=Delivery.Status.DELIVERED)
    count = 0
    for delivery in qs:
        open_incidents = delivery.incidents.filter(
            status__in={
                DeliveryIncident.Status.OPEN,
                DeliveryIncident.Status.IN_PROGRESS,
            }
        )
        if open_incidents.filter(status=DeliveryIncident.Status.ESCALATED).exists():
            continue
        open_incidents.update(status=DeliveryIncident.Status.ESCALATED)
        count += 1
    return count


def handoff_offers_for_courier(courier: User):
    """Courses en attente de reprise (transfert demandé, pas encore accepté)."""
    return (
        Delivery.objects.filter(
            transfer_requested_at__isnull=False,
            transfer_code__gt="",
            handoff_courier__isnull=True,
        )
        .exclude(courier=courier)
        .exclude(status__in={Delivery.Status.DELIVERED, Delivery.Status.FAILED})
        .select_related("order", "order__pharmacy", "order__client", "courier")
        .order_by("-transfer_requested_at")[:15]
    )


def pending_handoff_validations(courier: User):
    """Reprises acceptées en attente de saisie du code de transfert."""
    return (
        Delivery.objects.filter(handoff_courier=courier, transfer_code__gt="")
        .exclude(status__in={Delivery.Status.DELIVERED, Delivery.Status.FAILED})
        .select_related("order", "order__pharmacy", "order__client", "courier")
        .order_by("-updated_at")
    )
