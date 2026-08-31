"""Traçabilité remise pharmacie → livreur (QR + code suivi)."""
from __future__ import annotations

import io
import json
from typing import Any

from django.utils import timezone

from deliveries.models import Delivery
from orders.models import Order, generate_pharmacy_handoff_code, normalize_validation_code


class HandoffError(ValueError):
    pass


class DeliveryValidationError(ValueError):
    pass


def order_needs_pharmacy_handoff(order: Order) -> bool:
    return order.delivery_mode != Order.DeliveryMode.PICKUP


def ensure_pharmacy_handoff_code(order: Order, *, save: bool = True) -> str:
    """Génère le code suivi pharmacie pour les commandes en livraison."""
    if not order_needs_pharmacy_handoff(order):
        return ""
    if not order.pharmacy_handoff_code:
        order.pharmacy_handoff_code = generate_pharmacy_handoff_code()
        if save:
            order.save(update_fields=["pharmacy_handoff_code", "updated_at"])
    return order.pharmacy_handoff_code


def build_traceability_payload(order: Order) -> dict[str, Any]:
    ensure_pharmacy_handoff_code(order)
    items = [
        {"medicine": it.medicine_name, "quantity": it.quantity}
        for it in order.items.all()
    ]
    client = order.client
    return {
        "v": 1,
        "type": "gabpharma_handoff",
        "order": order.code,
        "pharmacy": order.pharmacy.name if order.pharmacy_id else "",
        "pharmacy_code": order.pharmacy.code if order.pharmacy_id else "",
        "client": client.get_full_name() or client.username,
        "client_phone": getattr(client, "phone", "") or "",
        "items": items,
        "handoff_code": order.pharmacy_handoff_code,
        "delivery_mode": order.delivery_mode,
    }


def traceability_qr_text(order: Order) -> str:
    return json.dumps(build_traceability_payload(order), ensure_ascii=False, separators=(",", ":"))


def render_traceability_qr_png(order: Order) -> bytes:
    import qrcode

    ensure_pharmacy_handoff_code(order)
    img = qrcode.make(traceability_qr_text(order))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def parse_traceability_qr(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise HandoffError("QR code vide ou illisible.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HandoffError("QR code non reconnu (format invalide).") from exc
    if data.get("type") != "gabpharma_handoff" or not data.get("order"):
        raise HandoffError("Ce QR code n'est pas une étiquette Gab'Pharma.")
    return data


def _handoff_code_matches(order: Order, code: str) -> bool:
    expected = normalize_validation_code(order.pharmacy_handoff_code)
    entered = normalize_validation_code(code)
    return bool(expected and entered and expected == entered)


def validate_pharmacy_handoff(
    order: Order,
    user,
    *,
    code: str | None = None,
    qr_payload: dict | None = None,
) -> Order:
    """Valide la remise du colis au livreur (scan QR ou code saisi)."""
    if not order_needs_pharmacy_handoff(order):
        raise HandoffError("Cette commande ne nécessite pas de remise livreur.")
    if order.status not in {Order.Status.READY, Order.Status.DELIVERING}:
        raise HandoffError("La commande n'est pas prête pour une remise livreur.")
    if order.pharmacy_handoff_at:
        raise HandoffError("La remise a déjà été validée par la pharmacie.")

    ensure_pharmacy_handoff_code(order)

    if qr_payload:
        if qr_payload.get("order") != order.code:
            raise HandoffError("Le QR code ne correspond pas à cette commande.")
        if not _handoff_code_matches(order, qr_payload.get("handoff_code", "")):
            raise HandoffError("Code de traçabilité QR invalide.")
    elif code:
        if not _handoff_code_matches(order, code):
            raise HandoffError("Code pharmacie incorrect.")
    else:
        raise HandoffError("Scannez le QR ou saisissez le code pharmacie.")

    order.pharmacy_handoff_at = timezone.now()
    order.pharmacy_handoff_by = user
    order.save(update_fields=["pharmacy_handoff_at", "pharmacy_handoff_by", "updated_at"])

    delivery = Delivery.objects.filter(order=order).first()
    if delivery:
        from core.delivery_transfer import log_delivery_step

        log_delivery_step(
            delivery,
            f"Remise pharmacie validée par {user.get_full_name() or user.username}",
            delivery.status,
        )
    return order


def courier_can_pick_up_from_pharmacy(order: Order, code: str | None = None) -> bool:
    if order.pharmacy_handoff_at:
        return True
    if code and _handoff_code_matches(order, code):
        return True
    return False


def confirm_courier_pharmacy_pickup(order: Order, code: str | None = None) -> bool:
    """Confirme le retrait livreur (code pharmacie ou validation déjà faite)."""
    if order.pharmacy_handoff_at:
        return True
    if not code or not _handoff_code_matches(order, code):
        return False
    ensure_pharmacy_handoff_code(order)
    order.pharmacy_handoff_at = timezone.now()
    order.save(update_fields=["pharmacy_handoff_at", "updated_at"])
    delivery = Delivery.objects.filter(order=order).first()
    if delivery:
        from core.delivery_transfer import log_delivery_step

        log_delivery_step(delivery, "Remise pharmacie confirmée (code livreur)", delivery.status)
    return True


def resolve_courier_pharmacy_pickup(
    order: Order,
    *,
    manual_code: str | None = None,
    qr_raw: str | None = None,
) -> bool:
    """Retrait pharmacie : remise déjà validée, code suivi ou scan QR sur le colis."""
    if order.pharmacy_handoff_at:
        return True

    if qr_raw:
        payload = parse_traceability_qr(qr_raw)
        if payload.get("order") != order.code:
            raise HandoffError("Le QR ne correspond pas à cette commande.")
        ensure_pharmacy_handoff_code(order)
        if not _handoff_code_matches(order, payload.get("handoff_code", "")):
            raise HandoffError("QR colis invalide — vérifiez l'étiquette.")
        order.pharmacy_handoff_at = timezone.now()
        order.save(update_fields=["pharmacy_handoff_at", "updated_at"])
        delivery = Delivery.objects.filter(order=order).first()
        if delivery:
            from core.delivery_transfer import log_delivery_step

            log_delivery_step(delivery, "Colis retiré (scan QR pharmacie)", delivery.status)
        return True

    code = normalize_validation_code(manual_code or "")
    if code and _handoff_code_matches(order, code):
        return confirm_courier_pharmacy_pickup(order, code)

    raise HandoffError(
        "Saisissez le code pharmacie, scannez le QR sur le colis, "
        "ou attendez la validation par la pharmacie."
    )


def resolve_handoff_order_from_qr(
    qr_payload: dict,
    *,
    pharmacy_id: int | None = None,
) -> Order:
    order_code = (qr_payload.get("order") or "").strip()
    if not order_code:
        raise HandoffError("QR code incomplet.")
    qs = Order.objects.filter(code=order_code).select_related("pharmacy", "client")
    if pharmacy_id:
        qs = qs.filter(pharmacy_id=pharmacy_id)
    order = qs.first()
    if not order:
        raise HandoffError("Commande introuvable pour cette pharmacie.")
    return order


def _order_items_payload(order: Order) -> list[dict[str, Any]]:
    return [
        {"medicine": it.medicine_name, "quantity": it.quantity}
        for it in order.items.all()
    ]


def build_client_delivery_payload(order: Order) -> dict[str, Any]:
    """QR présenté par le patient au livreur (contient le code de validation)."""
    client = order.client
    return {
        "v": 1,
        "type": "gabpharma_delivery",
        "order": order.code,
        "pharmacy": order.pharmacy.name if order.pharmacy_id else "",
        "pharmacy_code": order.pharmacy.code if order.pharmacy_id else "",
        "client": client.get_full_name() or client.username,
        "client_phone": getattr(client, "phone", "") or "",
        "items": _order_items_payload(order),
        "validation_code": normalize_validation_code(order.validation_code),
        "delivery_mode": order.delivery_mode,
    }


def client_delivery_qr_text(order: Order) -> str:
    return json.dumps(
        build_client_delivery_payload(order), ensure_ascii=False, separators=(",", ":")
    )


def render_client_delivery_qr_png(order: Order) -> bytes:
    import qrcode

    img = qrcode.make(client_delivery_qr_text(order))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def parse_client_delivery_qr(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise DeliveryValidationError("QR code vide ou illisible.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeliveryValidationError("QR code non reconnu (format invalide).") from exc
    if data.get("type") != "gabpharma_delivery" or not data.get("order"):
        raise DeliveryValidationError("Ce QR n'est pas un bon de livraison Gab'Pharma.")
    return data


def resolve_courier_delivery_validation(
    delivery: Delivery,
    *,
    manual_code: str | None = None,
    qr_raw: str | None = None,
) -> str:
    """Retourne le code client validé (saisie ou scan QR du patient)."""
    order = delivery.order
    if delivery.status != Delivery.Status.IN_TRANSIT:
        raise DeliveryValidationError("La livraison doit être en cours chez le client.")

    from orders.models import ensure_validation_code_normalized

    ensure_validation_code_normalized(order, save=True)

    if qr_raw:
        payload = parse_client_delivery_qr(qr_raw)
        if payload.get("order") != order.code:
            raise DeliveryValidationError("Le QR ne correspond pas à cette livraison.")
        code = normalize_validation_code(payload.get("validation_code", ""))
        expected = normalize_validation_code(order.validation_code)
        if not code or code != expected:
            raise DeliveryValidationError("QR client invalide — demandez au patient d'actualiser son écran.")
        return code

    code = normalize_validation_code(manual_code or "")
    expected = normalize_validation_code(order.validation_code)
    if not code or code != expected:
        raise DeliveryValidationError("Code incorrect — demandez au client de vous le communiquer.")
    return code


def order_shows_client_delivery_qr(order: Order) -> bool:
    from core.insurance import order_awaiting_insurance

    if order_awaiting_insurance(order):
        return False
    return order.status not in {
        Order.Status.CART,
        Order.Status.CANCELLED,
        Order.Status.DELIVERED,
        Order.Status.REFUNDED,
    }
