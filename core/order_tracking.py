"""Suivi commande patient — étapes et payload API temps réel."""
from __future__ import annotations

from django.utils import timezone

from deliveries.models import Delivery
from orders.models import Order


def get_delivery_for_order(order: Order) -> Delivery | None:
    try:
        return order.delivery
    except Delivery.DoesNotExist:
        return None


def _courier_initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _format_distance_km(km: float | None) -> str:
    if km is None:
        return ""
    if km < 0.1:
        return "à proximité"
    if km < 1:
        return f"{int(round(km * 1000))} m"
    return f"{km:.1f} km"


def build_courier_public_info(delivery: Delivery | None) -> dict | None:
    if not delivery or not delivery.courier_id:
        return None
    courier = delivery.courier
    profile = getattr(courier, "courier_profile", None)
    name = courier.get_full_name() or courier.username
    info = {
        "name": name,
        "initials": _courier_initials(name),
        "phone": getattr(courier, "phone", "") or "",
        "rating": float(profile.rating) if profile else None,
        "total_deliveries": profile.total_deliveries if profile else 0,
        "level": profile.get_level_display() if profile else "",
        "vehicle_type": (profile.vehicle_type if profile else "") or "Véhicule",
        "vehicle_plate": profile.vehicle_plate if profile else "",
        "picked_up_at": delivery.picked_up_at.isoformat() if delivery.picked_up_at else None,
        "status": delivery.status,
        "status_label": delivery.get_status_display(),
    }
    return info


def compute_tracking_metrics(order: Order, delivery: Delivery | None) -> dict:
    from core.pharmacy_proximity import haversine_km

    metrics = {
        "progress_percent": 5,
        "phase": "preparing",
        "distance_remaining_km": None,
        "distance_remaining_label": "",
        "distance_total_km": None,
        "distance_total_label": "",
        "eta_minutes": None,
    }
    if not delivery:
        if order.status == Order.Status.PREPARING:
            metrics["progress_percent"] = 25
        elif order.status == Order.Status.READY:
            metrics["progress_percent"] = 40
        return metrics

    status_progress = {
        Delivery.Status.PENDING: 15,
        Delivery.Status.ASSIGNED: 30,
        Delivery.Status.PICKING_UP: 45,
        Delivery.Status.PICKED_UP: 62,
        Delivery.Status.IN_TRANSIT: 82,
        Delivery.Status.DELIVERED: 100,
    }
    metrics["progress_percent"] = status_progress.get(delivery.status, 10)
    metrics["eta_minutes"] = delivery.estimated_minutes

    pharmacy = order.pharmacy
    dest_lat = order.delivery_latitude
    dest_lng = order.delivery_longitude
    pharm_lat = pharmacy.latitude if pharmacy else None
    pharm_lng = pharmacy.longitude if pharmacy else None
    clat = float(delivery.courier_lat) if delivery.courier_lat is not None else None
    clng = float(delivery.courier_lng) if delivery.courier_lng is not None else None

    if pharm_lat is not None and dest_lat is not None:
        total = haversine_km(float(pharm_lat), float(pharm_lng), float(dest_lat), float(dest_lng))
        metrics["distance_total_km"] = round(total, 2)
        metrics["distance_total_label"] = _format_distance_km(total)

    if delivery.status in {Delivery.Status.PICKED_UP, Delivery.Status.IN_TRANSIT}:
        metrics["phase"] = "transit"
        if clat is not None and dest_lat is not None:
            rem = haversine_km(clat, clng, float(dest_lat), float(dest_lng))
            metrics["distance_remaining_km"] = round(rem, 2)
            metrics["distance_remaining_label"] = _format_distance_km(rem)
    elif delivery.status in {Delivery.Status.ASSIGNED, Delivery.Status.PICKING_UP}:
        metrics["phase"] = "pickup"
        if clat is not None and pharm_lat is not None:
            rem = haversine_km(clat, clng, float(pharm_lat), float(pharm_lng))
            metrics["distance_remaining_km"] = round(rem, 2)
            metrics["distance_remaining_label"] = _format_distance_km(rem)
        elif metrics["distance_total_km"]:
            metrics["distance_remaining_label"] = metrics["distance_total_label"]
    elif delivery.status == Delivery.Status.DELIVERED:
        metrics["phase"] = "delivered"
        metrics["progress_percent"] = 100
        metrics["distance_remaining_label"] = "Arrivé"

    return metrics

def order_tracking_steps(order: Order, delivery: Delivery | None = None) -> dict:
    if delivery is None:
        delivery = get_delivery_for_order(order)

    def _awaiting_insurance(o):
        from core.insurance import order_awaiting_insurance

        return order_awaiting_insurance(o)

    return {
        "created": True,
        "confirmed": order.status
        not in {Order.Status.PENDING, Order.Status.CART, Order.Status.AWAITING_RX}
        and not _awaiting_insurance(order),
        "preparing": order.status
        in {
            Order.Status.PREPARING,
            Order.Status.READY,
            Order.Status.DELIVERING,
            Order.Status.DELIVERED,
        },
        "ready": order.status
        in {Order.Status.READY, Order.Status.DELIVERING, Order.Status.DELIVERED},
        "shipping": order.status in {Order.Status.DELIVERING, Order.Status.DELIVERED}
        or (
            delivery
            and delivery.status
            in {
                Delivery.Status.ASSIGNED,
                Delivery.Status.PICKING_UP,
                Delivery.Status.PICKED_UP,
                Delivery.Status.IN_TRANSIT,
                Delivery.Status.DELIVERED,
            }
        ),
        "delivered": order.status == Order.Status.DELIVERED,
        "awaiting_rx": order.status == Order.Status.AWAITING_RX,
        "cancelled": order.status == Order.Status.CANCELLED,
    }


def _step_state(done: bool, active: bool) -> str:
    if done:
        return "done"
    if active:
        return "active"
    return "pending"


def build_tracking_timeline(order: Order, delivery: Delivery | None = None) -> list[dict]:
    from core.insurance import get_pending_insurance_claim, order_awaiting_insurance

    steps = order_tracking_steps(order, delivery)
    has_rx = bool(order.linked_prescription_id) or steps["awaiting_rx"]
    is_pickup = order.delivery_mode == Order.DeliveryMode.PICKUP
    timeline: list[dict] = []
    insurance_pending = order_awaiting_insurance(order)
    pending_claim = get_pending_insurance_claim(order)

    timeline.append(
        {
            "id": "created",
            "label": "Commande enregistrée" if insurance_pending else "Paiement confirmé",
            "detail": order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else "",
            "state": "done",
        }
    )

    if order.insurance_provider_id and order.insurance_coverage > 0:
        if insurance_pending:
            ins_detail = (
                f"En attente de validation par {order.insurance_provider.name}…"
            )
            ins_state = "active"
        elif pending_claim is None:
            ins_detail = f"{order.insurance_provider.name} — {order.insurance_coverage} F validés"
            ins_state = "done"
        else:
            ins_detail = order.insurance_provider.name
            ins_state = "done"
        timeline.append(
            {
                "id": "insurance",
                "label": "Validation assurance",
                "detail": ins_detail,
                "state": ins_state,
            }
        )

    if has_rx:
        rx = order.linked_prescription
        if steps["awaiting_rx"]:
            detail = "En cours de vérification par la pharmacie…"
            state = "active"
        elif rx and rx.status == "rejected":
            detail = rx.review_notes or rx.get_status_display()
            state = "active"
        elif rx:
            detail = rx.get_status_display()
            state = "done" if steps["confirmed"] else "pending"
        else:
            detail = ""
            state = _step_state(steps["confirmed"], steps["awaiting_rx"])
        timeline.append(
            {
                "id": "rx",
                "label": "Validation ordonnance",
                "detail": detail,
                "state": state,
            }
        )

    active_preparing = order.status == Order.Status.PREPARING
    prep_detail = ""
    if order.preparing_at and order.status == Order.Status.PREPARING:
        prep_detail = f"Depuis {order.preparing_at.strftime('%H:%M')}"
    timeline.append(
        {
            "id": "preparing",
            "label": "Préparation en pharmacie",
            "detail": prep_detail,
            "state": _step_state(steps["preparing"], active_preparing),
        }
    )

    if is_pickup:
        active_ready = order.status == Order.Status.READY
        timeline.append(
            {
                "id": "ready",
                "label": "Prête au retrait",
                "detail": "Présentez votre code en pharmacie",
                "state": _step_state(steps["ready"] and not steps["delivered"], active_ready),
            }
        )
    else:
        courier_name = ""
        delivery_detail = ""
        metrics = compute_tracking_metrics(order, delivery)
        if delivery:
            delivery_detail = delivery.get_status_display()
            if delivery.courier:
                courier_name = (
                    delivery.courier.get_full_name() or delivery.courier.username
                )
                delivery_detail = f"{delivery_detail} · {courier_name}"
            if metrics.get("distance_remaining_label"):
                delivery_detail = f"{delivery_detail} · {metrics['distance_remaining_label']}"
            elif delivery.estimated_minutes:
                delivery_detail = (
                    f"{delivery_detail} · ~{delivery.estimated_minutes} min"
                    if delivery_detail
                    else f"~{delivery.estimated_minutes} min"
                )
            if delivery.picked_up_at and delivery.status in {
                Delivery.Status.PICKED_UP,
                Delivery.Status.IN_TRANSIT,
                Delivery.Status.DELIVERED,
            }:
                delivery_detail = (
                    f"Colis retiré à {delivery.picked_up_at.strftime('%H:%M')} · {courier_name}"
                    if courier_name
                    else delivery_detail
                )
        active_shipping = order.status == Order.Status.DELIVERING or (
            delivery
            and delivery.status
            in {
                Delivery.Status.ASSIGNED,
                Delivery.Status.PICKING_UP,
                Delivery.Status.PICKED_UP,
                Delivery.Status.IN_TRANSIT,
            }
        )
        timeline.append(
            {
                "id": "shipping",
                "label": "Livraison",
                "detail": delivery_detail,
                "state": _step_state(steps["delivered"], active_shipping),
            }
        )

    delivered_detail = ""
    if delivery and delivery.delivered_at:
        delivered_detail = delivery.delivered_at.strftime("%d/%m/%Y %H:%M")
    timeline.append(
        {
            "id": "delivered",
            "label": "Livrée" if not is_pickup else "Retirée",
            "detail": delivered_detail,
            "state": _step_state(steps["delivered"], False),
        }
    )

    return timeline


def _map_point(lat, lng, label: str) -> dict | None:
    if lat is None or lng is None:
        return None
    return {"lat": float(lat), "lng": float(lng), "label": label}


def build_order_tracking_payload(order: Order, delivery: Delivery | None = None) -> dict:
    if delivery is None:
        delivery = get_delivery_for_order(order)
    steps = order_tracking_steps(order, delivery)
    is_terminal = order.status in {
        Order.Status.DELIVERED,
        Order.Status.CANCELLED,
        Order.Status.REFUNDED,
    }
    pharmacy = order.pharmacy
    courier = delivery.courier if delivery else None
    delivery_payload = None
    if delivery:
        delivery_payload = {
            "status": delivery.status,
            "status_label": delivery.get_status_display(),
            "courier_name": (courier.get_full_name() or courier.username) if courier else "",
            "eta_minutes": delivery.estimated_minutes,
            "courier_lat": float(delivery.courier_lat) if delivery.courier_lat else None,
            "courier_lng": float(delivery.courier_lng) if delivery.courier_lng else None,
            "picked_up_at": delivery.picked_up_at.isoformat() if delivery.picked_up_at else None,
        }

    metrics = compute_tracking_metrics(order, delivery)
    courier_info = build_courier_public_info(delivery)

    map_data = {
        "pharmacy": _map_point(
            pharmacy.latitude if pharmacy else None,
            pharmacy.longitude if pharmacy else None,
            pharmacy.name if pharmacy else "Pharmacie",
        ),
        "destination": _map_point(
            order.delivery_latitude,
            order.delivery_longitude,
            "Votre adresse",
        ),
        "courier": _map_point(
            delivery.courier_lat if delivery else None,
            delivery.courier_lng if delivery else None,
            "Livreur",
        )
        if delivery
        else None,
    }

    current_hint = order.get_status_display()
    if delivery and order.status in {Order.Status.DELIVERING, Order.Status.READY}:
        current_hint = delivery.get_status_display()
        if courier:
            current_hint = f"{current_hint} — {courier.get_full_name() or courier.username}"

    return {
        "ok": True,
        "order_code": order.code,
        "status": order.status,
        "status_label": order.get_status_display(),
        "current_hint": current_hint,
        "is_terminal": is_terminal,
        "is_pickup": order.delivery_mode == Order.DeliveryMode.PICKUP,
        "steps_done": steps,
        "timeline": build_tracking_timeline(order, delivery),
        "delivery": delivery_payload,
        "courier": courier_info,
        "metrics": metrics,
        "map": map_data,
        "updated_at": timezone.now().isoformat(),
    }
