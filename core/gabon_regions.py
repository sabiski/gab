"""Provinces du Gabon — centres géographiques pour la cartographie autorités."""
from __future__ import annotations

GABON_PROVINCES = (
    {"slug": "estuaire", "name": "Estuaire", "lat": 0.39, "lng": 9.45, "population": 842_120},
    {"slug": "haut-ogooue", "name": "Haut-Ogooué", "lat": -1.63, "lng": 13.58, "population": 250_000},
    {"slug": "moyen-ogooue", "name": "Moyen-Ogooué", "lat": -0.70, "lng": 10.20, "population": 69_000},
    {"slug": "ngounie", "name": "Ngounié", "lat": -1.49, "lng": 11.00, "population": 115_000},
    {"slug": "nyanga", "name": "Nyanga", "lat": -2.92, "lng": 11.00, "population": 52_000},
    {"slug": "ogooue-ivindo", "name": "Ogooué-Ivindo", "lat": 0.70, "lng": 13.00, "population": 64_000},
    {"slug": "ogooue-lolo", "name": "Ogooué-Lolo", "lat": -0.85, "lng": 12.45, "population": 65_000},
    {"slug": "ogooue-maritime", "name": "Ogooué-Maritime", "lat": -0.72, "lng": 8.78, "population": 157_000},
    {"slug": "woleu-ntem", "name": "Woleu-Ntem", "lat": 1.60, "lng": 11.50, "population": 154_000},
)

_ALIASES = {
    "libreville": "Estuaire",
    "port-gentil": "Ogooué-Maritime",
    "franceville": "Haut-Ogooué",
    "oyem": "Woleu-Ntem",
    "lambarene": "Moyen-Ogooué",
    "mouila": "Ngounié",
    "tchibanga": "Nyanga",
    "makokou": "Ogooué-Ivindo",
    "koulamoutou": "Ogooué-Lolo",
}


def normalize_region_name(value: str) -> str:
    raw = (value or "").strip().replace("\t", "")
    if not raw:
        return ""
    low = raw.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    for prov in GABON_PROVINCES:
        if prov["name"].lower() == low or prov["slug"] == low:
            return prov["name"]
    return raw


def province_slug_from_shape_name(value: str) -> str:
    """Associe un nom GeoJSON (shapeName) au slug interne."""
    name = normalize_region_name(value)
    for prov in GABON_PROVINCES:
        if prov["name"] == name:
            return prov["slug"]
    return ""


CRITICAL_STOCK_THRESHOLD = 10


def stock_coverage_color(rate: int) -> dict:
    """Couleurs carte stocks (seuils maquette surveillance stocks)."""
    if rate >= 80:
        return {"fill": "#059669", "border": "#047857"}
    if rate >= 60:
        return {"fill": "#34d399", "border": "#10b981"}
    if rate >= 30:
        return {"fill": "#fbbf24", "border": "#f59e0b"}
    return {"fill": "#f87171", "border": "#dc2626"}


def coverage_color(rate: int) -> dict:
    if rate >= 90:
        return {"fill": "#059669", "border": "#047857", "level": "excellent"}
    if rate >= 70:
        return {"fill": "#34d399", "border": "#10b981", "level": "bon"}
    if rate >= 50:
        return {"fill": "#fbbf24", "border": "#f59e0b", "level": "moyen"}
    if rate >= 30:
        return {"fill": "#fb923c", "border": "#ea580c", "level": "faible"}
    return {"fill": "#f87171", "border": "#dc2626", "level": "critique"}


def incidence_color(rate: float) -> dict:
    """Couleurs carte tendances épidémiologiques (incidence / 100k hab.)."""
    if rate <= 0:
        return {"fill": "#e5e7eb", "border": "#d1d5db", "bucket": "none"}
    if rate < 10:
        return {"fill": "#fce7f3", "border": "#fbcfe8", "bucket": "0-10"}
    if rate < 30:
        return {"fill": "#f9a8d4", "border": "#f472b6", "bucket": "10-30"}
    if rate < 60:
        return {"fill": "#f472b6", "border": "#ec4899", "bucket": "30-60"}
    return {"fill": "#db2777", "border": "#be185d", "bucket": "60+"}
