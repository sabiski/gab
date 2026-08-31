"""Algorithme de proximité — pharmacie la plus adaptée à une alerte urgence."""
import math
from decimal import Decimal, InvalidOperation

from pharmacies.models import Pharmacy

# Libreville centre (fallback si pas de GPS patient)
DEFAULT_LAT = 0.4162
DEFAULT_LNG = 9.4673


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance en km entre deux points GPS."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_coord(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, InvalidOperation):
        return None


def user_coordinates(user, post_lat=None, post_lng=None):
    lat = _parse_coord(post_lat) or _parse_coord(getattr(user, "latitude", None))
    lng = _parse_coord(post_lng) or _parse_coord(getattr(user, "longitude", None))
    if lat is not None and lng is not None:
        return lat, lng
    return DEFAULT_LAT, DEFAULT_LNG


def _category_bonus(pharmacy, category):
    """Bonus de score selon le type d'alerte (plus négatif = prioritaire)."""
    bonus = 0.0
    if pharmacy.is_on_duty:
        bonus -= 1.8
    if pharmacy.is_24h:
        bonus -= 1.2
    if category == "accident" and pharmacy.is_24h:
        bonus -= 0.8
    if category == "child" and pharmacy.is_on_duty:
        bonus -= 0.6
    return bonus


def rank_pharmacies_for_emergency(user, category=None, limit=5, post_lat=None, post_lng=None):
    """
    Classe les pharmacies actives par pertinence urgence :
    distance GPS + bonus garde/24h + note.
    Retourne une liste de dicts {pharmacy, distance_km, eta_min, score}.
    """
    lat, lng = user_coordinates(user, post_lat, post_lng)
    qs = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)

    ranked = []
    for ph in qs:
        if ph.latitude is not None and ph.longitude is not None:
            dist = haversine_km(lat, lng, float(ph.latitude), float(ph.longitude))
        elif user.city and ph.city and user.city.lower() == ph.city.lower():
            dist = 8.0
        else:
            dist = 25.0

        score = dist + _category_bonus(ph, category)
        rating = float(ph.rating or 0)
        eta = max(3, int(dist * 4 + 2))

        ranked.append(
            {
                "pharmacy": ph,
                "distance_km": round(dist, 1),
                "eta_min": eta,
                "score": score - (rating * 0.05),
            }
        )

    ranked.sort(key=lambda x: x["score"])
    return ranked[:limit]


def pick_nearest_pharmacy(user, category=None, post_lat=None, post_lng=None):
    """Retourne la meilleure officine ou None."""
    ranked = rank_pharmacies_for_emergency(
        user, category=category, limit=1, post_lat=post_lat, post_lng=post_lng
    )
    return ranked[0] if ranked else None
