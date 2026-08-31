"""Helpers portail livreur — véhicules, documents, missions urgentes."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from accounts.models import CourierProfile
from deliveries.models import Delivery

ACTIVE_MISSION_STATUSES = frozenset(
    {
        Delivery.Status.ASSIGNED,
        Delivery.Status.PICKING_UP,
        Delivery.Status.PICKED_UP,
        Delivery.Status.IN_TRANSIT,
    }
)

INCIDENT_UI_TYPES = (
    ("accident", "Accident de la route", "car_crash"),
    ("vehicle_breakdown", "Panne de véhicule", "build"),
    ("package_damaged", "Colis endommagé", "inventory_2"),
    ("client_absent", "Client absent / injoignable", "person_off"),
    ("address_not_found", "Adresse incorrecte", "wrong_location"),
    ("other", "Autre problème", "help"),
)

INCIDENT_TYPE_MAP = {
    "package_damaged": "other",
    "other_problem": "other",
}

DOCUMENT_SPECS = (
    ("license_document", "Permis de conduire", "badge"),
    ("insurance_document", "Assurance", "verified_user"),
    ("registration_document", "Carte grise", "description"),
    ("technical_control_document", "Contrôle technique", "fact_check"),
    ("training_document", "Attestation de formation", "school"),
    ("professional_card_document", "Carte professionnelle", "id_card"),
)

ADMIN_DOCUMENT_SPECS = (
    ("id_document", "Pièce d'identité", "fingerprint"),
) + DOCUMENT_SPECS

COURIER_DOCUMENT_FIELD_NAMES = frozenset(field for field, _, _ in ADMIN_DOCUMENT_SPECS)


def _document_card(profile: CourierProfile | None, field: str, label: str, icon: str) -> dict:
    if isinstance(profile, CourierProfile) and profile.pk and profile.user_id:
        doc = getattr(profile, field, None)
        valid = bool(doc and getattr(doc, "name", ""))
        return {
            "field": field,
            "label": label,
            "icon": icon,
            "valid": valid,
            "url": doc.url if valid else "",
            "ref": f"DOC-{profile.user_id:04d}-{field[:3].upper()}",
            "status": "Valide" if valid else "À fournir",
        }
    return {
        "field": field,
        "label": label,
        "icon": icon,
        "valid": False,
        "url": "",
        "ref": "—",
        "status": "À fournir",
    }


def courier_document_cards(profile: CourierProfile) -> list[dict]:
    return [_document_card(profile, field, label, icon) for field, label, icon in DOCUMENT_SPECS]


def courier_admin_document_cards(profile: CourierProfile | None = None) -> list[dict]:
    return [_document_card(profile, field, label, icon) for field, label, icon in ADMIN_DOCUMENT_SPECS]


def apply_courier_vehicle_post(profile: CourierProfile, post) -> None:
    profile.vehicle_label = post.get("vehicle_label", profile.vehicle_label).strip()
    profile.vehicle_type = post.get("vehicle_type", profile.vehicle_type).strip()
    profile.vehicle_plate = post.get("vehicle_plate", profile.vehicle_plate).strip()
    try:
        profile.vehicle_year = int(post.get("vehicle_year") or 0) or None
    except (TypeError, ValueError):
        profile.vehicle_year = None
    profile.vehicle_fuel = post.get("vehicle_fuel", profile.vehicle_fuel).strip()
    try:
        profile.vehicle_cc = int(post.get("vehicle_cc") or 0) or None
    except (TypeError, ValueError):
        profile.vehicle_cc = None
    profile.vehicle_color = post.get("vehicle_color", profile.vehicle_color).strip()
    profile.vehicle_chassis = post.get("vehicle_chassis", profile.vehicle_chassis).strip()


def apply_courier_documents_upload(profile: CourierProfile, files) -> bool:
    changed = False
    for field in COURIER_DOCUMENT_FIELD_NAMES:
        if files.get(field):
            setattr(profile, field, files[field])
            changed = True
    if changed:
        profile.eligibility_approved = False
        profile.eligibility_approved_at = None
        profile.eligibility_approved_by = None
    return changed


def apply_courier_admin_meta(profile: CourierProfile, post) -> None:
    profile.zone = post.get("zone", profile.zone).strip()
    pharmacy_id = (post.get("pharmacy_id") or "").strip()
    if pharmacy_id:
        try:
            profile.pharmacy_id = int(pharmacy_id)
        except (TypeError, ValueError):
            profile.pharmacy_id = None
    else:
        profile.pharmacy_id = None
    courier_status = post.get("courier_status", "").strip()
    if courier_status in dict(CourierProfile.CourierStatus.choices):
        profile.courier_status = courier_status
    level = post.get("level", "").strip()
    if level in dict(CourierProfile.Level.choices):
        profile.level = level


def courier_documents_status(profile: CourierProfile | None) -> dict:
    if not isinstance(profile, CourierProfile) or not profile.pk:
        return {
            "completed": 0,
            "total": len(ADMIN_DOCUMENT_SPECS),
            "missing": [label for _, label, _ in ADMIN_DOCUMENT_SPECS],
            "progress_label": f"0/{len(ADMIN_DOCUMENT_SPECS)}",
            "documents_complete": False,
        }
    missing = []
    completed = 0
    for field, label, _icon in ADMIN_DOCUMENT_SPECS:
        doc = getattr(profile, field, None)
        if doc and getattr(doc, "name", ""):
            completed += 1
        else:
            missing.append(label)
    total = len(ADMIN_DOCUMENT_SPECS)
    return {
        "completed": completed,
        "total": total,
        "missing": missing,
        "progress_label": f"{completed}/{total}",
        "documents_complete": completed == total,
    }


def courier_eligibility(profile: CourierProfile | None) -> dict:
    """Éligibilité missions : 7 documents + validation admin."""
    docs = courier_documents_status(profile)
    admin_approved = bool(
        isinstance(profile, CourierProfile) and profile.pk and profile.eligibility_approved
    )
    if docs["documents_complete"] and admin_approved:
        status = "eligible"
        status_label = "Éligible aux missions"
    elif docs["documents_complete"]:
        status = "pending_admin"
        status_label = "En attente validation admin"
    else:
        status = "incomplete"
        status_label = "Documents incomplets"
    return {
        **docs,
        "admin_approved": admin_approved,
        "is_eligible": docs["documents_complete"] and admin_approved,
        "status": status,
        "status_label": status_label,
    }


def courier_must_complete_documents(profile: CourierProfile | None) -> bool:
    return not courier_eligibility(profile)["is_eligible"]


def courier_eligibility_message(profile: CourierProfile | None) -> str:
    elig = courier_eligibility(profile)
    if elig["is_eligible"]:
        return ""
    if elig["status"] == "pending_admin":
        return (
            "Votre dossier est complet. En attente de validation par l'administration "
            "pour devenir éligible aux missions."
        )
    preview = ", ".join(elig["missing"][:3])
    extra = len(elig["missing"]) - 3
    if extra > 0:
        preview = f"{preview} (+{extra} autre{'s' if extra > 1 else ''})"
    return (
        f"Profil incomplet ({elig['progress_label']} documents). "
        f"Complétez : {preview}."
    )


def approve_courier_eligibility(profile: CourierProfile, admin_user) -> None:
    from django.utils import timezone

    if not courier_documents_status(profile)["documents_complete"]:
        raise ValueError("Tous les documents doivent être fournis avant validation.")
    profile.eligibility_approved = True
    profile.eligibility_approved_at = timezone.now()
    profile.eligibility_approved_by = admin_user
    profile.save(
        update_fields=[
            "eligibility_approved",
            "eligibility_approved_at",
            "eligibility_approved_by",
        ]
    )


def revoke_courier_eligibility(profile: CourierProfile) -> None:
    profile.eligibility_approved = False
    profile.eligibility_approved_at = None
    profile.eligibility_approved_by = None
    profile.courier_status = CourierProfile.CourierStatus.OFFLINE
    profile.save(
        update_fields=[
            "eligibility_approved",
            "eligibility_approved_at",
            "eligibility_approved_by",
            "courier_status",
        ]
    )


def courier_vehicle_display(profile: CourierProfile) -> dict:
    label = profile.vehicle_label.strip() or profile.vehicle_type.strip() or "Véhicule principal"
    vtype = profile.vehicle_type.strip() or "Moto"
    return {
        "label": label,
        "plate": profile.vehicle_plate or "—",
        "type": vtype,
        "year": profile.vehicle_year,
        "fuel": profile.vehicle_fuel or "—",
        "cc": profile.vehicle_cc,
        "color": profile.vehicle_color or "—",
        "chassis": profile.vehicle_chassis or "—",
        "active": bool(profile.vehicle_plate or profile.vehicle_type or profile.vehicle_label),
    }


def courier_active_urgent_delivery(courier) -> Delivery | None:
    return (
        Delivery.objects.filter(
            courier=courier,
            order__is_urgent=True,
            status__in=ACTIVE_MISSION_STATUSES,
        )
        .select_related("order", "order__pharmacy", "order__client")
        .prefetch_related("order__items")
        .order_by("-updated_at")
        .first()
    )


def urgent_mission_context(delivery: Delivery) -> dict:
    order = delivery.order
    deadline = (delivery.created_at or timezone.now()) + timedelta(minutes=45)
    remaining = max(0, int((deadline - timezone.now()).total_seconds()))
    mm, ss = divmod(remaining, 60)
    tracking = None
    if delivery.status in {Delivery.Status.PICKED_UP, Delivery.Status.IN_TRANSIT}:
        from core.order_tracking import build_order_tracking_payload

        tracking = build_order_tracking_payload(order, delivery)
    metrics = (tracking or {}).get("metrics") or {}
    return {
        "delivery": delivery,
        "order": order,
        "deadline_iso": deadline.isoformat(),
        "timer_label": f"{mm:02d}:{ss:02d}",
        "distance_label": metrics.get("distance_total_label") or "—",
        "eta_minutes": metrics.get("eta_minutes") or delivery.estimated_minutes,
        "urgency_reason": order.notes.strip()
        or "Commande urgente — le client a besoin de son médicament rapidement.",
        "step": _urgent_step(delivery.status),
    }


def _urgent_step(status: str) -> int:
    mapping = {
        Delivery.Status.ASSIGNED: 1,
        Delivery.Status.PICKING_UP: 2,
        Delivery.Status.PICKED_UP: 3,
        Delivery.Status.IN_TRANSIT: 4,
    }
    return mapping.get(status, 1)
