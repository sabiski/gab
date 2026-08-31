"""Position GPS obligatoire pour les livreurs (prise de course, mise en ligne)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from deliveries.models import Delivery


class CourierGpsRequired(ValueError):
    """Le livreur doit partager sa position GPS."""


def _parse_coord(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, TypeError):
        return None


def courier_has_gps(user) -> bool:
    return user.latitude is not None and user.longitude is not None


def save_courier_position(
    user,
    lat: Decimal,
    lng: Decimal,
    *,
    delivery: Delivery | None = None,
) -> None:
    user.latitude = lat
    user.longitude = lng
    user.save(update_fields=["latitude", "longitude", "updated_at"])
    if delivery is not None:
        delivery.courier_lat = lat
        delivery.courier_lng = lng
        delivery.save(update_fields=["courier_lat", "courier_lng", "updated_at"])


def require_gps_from_post(request, *, delivery: Delivery | None = None) -> tuple[Decimal, Decimal]:
    lat = _parse_coord(request.POST.get("latitude"))
    lng = _parse_coord(request.POST.get("longitude"))
    if lat is None or lng is None:
        raise CourierGpsRequired(
            "Partagez votre position GPS avant de continuer (autorisez la localisation dans le navigateur)."
        )
    save_courier_position(request.user, lat, lng, delivery=delivery)
    return lat, lng
