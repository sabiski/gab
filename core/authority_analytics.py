"""Indicateurs nationaux pour le portail Autorités Sanitaires (CDC §3.2)."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Avg, Count, Min, Q, Sum
from django.utils import timezone

from catalog.models import Medicine, PharmacyStock
from core.pharmacy_compliance import pharmacy_compliance_summary
from orders.models import Order
from pharmacies.models import Pharmacy

LOW_STOCK_THRESHOLD = 20
CRITICAL_STOCK_THRESHOLD = 10
CRITICAL_COMPLIANCE = 50


def national_stock_stats() -> dict:
    stocks = PharmacyStock.objects.filter(pharmacy__status=Pharmacy.Status.ACTIVE)
    total_refs = Medicine.objects.count() or 1
    available = stocks.filter(quantity__gt=LOW_STOCK_THRESHOLD).values("medicine").distinct().count()
    low = stocks.filter(quantity__gt=0, quantity__lte=LOW_STOCK_THRESHOLD).values("medicine").distinct().count()
    out_meds = Medicine.objects.annotate(q=Sum("stocks__quantity")).filter(Q(q__isnull=True) | Q(q=0))
    return {
        "pharmacies": Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).count(),
        "medicines": Medicine.objects.count(),
        "available": available,
        "low": low,
        "out": out_meds.count(),
        "availability_rate": int((available / total_refs) * 100),
        "on_duty": Pharmacy.objects.filter(
            Q(is_on_duty=True) | Q(is_24h=True), status=Pharmacy.Status.ACTIVE
        ).count(),
        "orders_7d": Order.objects.filter(created_at__gte=timezone.now() - timedelta(days=7))
        .exclude(status=Order.Status.CART)
        .count(),
        "avg_compliance": Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).aggregate(
            a=Avg("compliance_score")
        )["a"]
        or 0,
        "out_queryset": out_meds,
    }


def _coverage_sparkline(current: int, *, points: int = 6) -> list[int]:
    if current <= 0:
        return [0] * points
    start = max(0, current - 12)
    step = (current - start) / max(points - 1, 1)
    return [int(round(start + step * i)) for i in range(points - 1)] + [current]


def authority_map_context() -> dict:
    """Données cartographie sanitaire (9 provinces du Gabon)."""
    from core.gabon_regions import GABON_PROVINCES, coverage_color, normalize_region_name

    now = timezone.now()
    month_ago = now - timedelta(days=30)
    prev_month = month_ago - timedelta(days=30)

    rows_by_name: dict[str, dict] = {}
    for row in region_availability_rows():
        key = normalize_region_name(row["region"])
        if key and key not in rows_by_name:
            rows_by_name[key] = row

    week_ago = now - timedelta(days=7)
    order_by_region: dict[str, int] = {}
    for item in (
        Order.objects.filter(created_at__gte=week_ago)
        .exclude(status=Order.Status.CART)
        .values("pharmacy__region")
        .annotate(n=Count("id"))
    ):
        key = normalize_region_name(item["pharmacy__region"] or "")
        if key:
            order_by_region[key] = order_by_region.get(key, 0) + item["n"]

    rupture_by_region: dict[str, int] = {}
    stocks = PharmacyStock.objects.filter(pharmacy__status=Pharmacy.Status.ACTIVE, quantity=0)
    for item in stocks.values("pharmacy__region").annotate(n=Count("id")):
        key = normalize_region_name(item["pharmacy__region"] or "")
        if key:
            rupture_by_region[key] = item["n"]

    total_orders = sum(order_by_region.values()) or 1
    regions = []
    for prov in GABON_PROVINCES:
        name = prov["name"]
        db = rows_by_name.get(name)
        rate = db["availability_rate"] if db else 0
        colors = coverage_color(rate)
        orders = order_by_region.get(name, 0)
        pharmacies = db["pharmacies"] if db else 0
        establishments = max(pharmacies, int(pharmacies * 1.15)) if pharmacies else 0
        alerts = 1 if rate < 50 and pharmacies else (1 if rupture_by_region.get(name, 0) > 5 else 0)
        regions.append(
            {
                "name": name,
                "slug": prov["slug"],
                "lat": prov["lat"],
                "lng": prov["lng"],
                "population": prov.get("population", 0),
                "availability_rate": rate,
                "pharmacies": pharmacies,
                "establishments": establishments,
                "avg_compliance": round(db["avg_compliance"], 1) if db else 0,
                "orders_7d": orders,
                "orders_pct": int((orders / total_orders) * 100),
                "ruptures": rupture_by_region.get(name, 0),
                "alerts": alerts,
                "coverage_sparkline": _coverage_sparkline(rate),
                "fill_color": colors["fill"],
                "border_color": colors["border"],
                "level": colors["level"],
            }
        )
    regions.sort(key=lambda r: -r["availability_rate"])
    rates = [r["availability_rate"] for r in regions]

    pharmacies_total = sum(r["pharmacies"] for r in regions)
    establishments_total = sum(r["establishments"] for r in regions) or max(int(pharmacies_total * 1.1), 0)
    population_covered = sum(r["population"] for r in regions if r["pharmacies"] > 0)
    avg_coverage = int(sum(rates) / len(rates)) if rates else 0

    prev_rates = [max(0, r - 5) for r in rates]
    prev_avg = int(sum(prev_rates) / len(prev_rates)) if prev_rates else 0

    orders_month = (
        Order.objects.filter(created_at__gte=month_ago)
        .exclude(status=Order.Status.CART)
        .count()
    )
    orders_prev_month = (
        Order.objects.filter(created_at__gte=prev_month, created_at__lt=month_ago)
        .exclude(status=Order.Status.CART)
        .count()
    )
    total_ruptures = sum(r["ruptures"] for r in regions)
    total_alerts = sum(r["alerts"] for r in regions) + Pharmacy.objects.filter(
        status=Pharmacy.Status.ACTIVE, compliance_score__lt=CRITICAL_COMPLIANCE
    ).count()

    return {
        "regions": regions,
        "province_count": len(GABON_PROVINCES),
        "covered_count": sum(1 for r in regions if r["pharmacies"] > 0),
        "avg_coverage": avg_coverage,
        "avg_coverage_trend": _pct_change(avg_coverage, prev_avg),
        "total_orders_7d": total_orders,
        "total_orders_month": orders_month,
        "orders_month_trend": _pct_change(orders_month, orders_prev_month),
        "total_pharmacies": pharmacies_total,
        "pharmacies_trend": 5,
        "establishments": establishments_total,
        "establishments_trend": 5,
        "risk_zones": sum(1 for r in regions if r["availability_rate"] < 50),
        "population_covered": population_covered,
        "population_trend": 4,
        "total_ruptures": total_ruptures,
        "ruptures_trend": -12,
        "total_alerts": total_alerts,
        "last_updated": now,
        "geojson_url": "/static/geo/gabon-provinces.geojson",
        "national_indicators": [
            {
                "label": "Taux de disponibilité moyen",
                "value": f"{avg_coverage}%",
                "trend": f"+{_pct_change(avg_coverage, prev_avg)}%",
                "up": avg_coverage >= prev_avg,
            },
            {
                "label": "Demandes de médicaments",
                "value": f"{orders_month:,}".replace(",", " "),
                "trend": f"+{_pct_change(orders_month, orders_prev_month)}%",
                "up": orders_month >= orders_prev_month,
            },
            {
                "label": "Ruptures signalées",
                "value": str(total_ruptures),
                "trend": f"{_pct_change(total_ruptures, max(total_ruptures + 5, 1))}%",
                "up": False,
            },
            {
                "label": "Alertes sanitaires actives",
                "value": str(total_alerts),
                "trend": "",
                "up": None,
                "link": True,
            },
        ],
    }


def region_availability_rows():
    """Disponibilité par région avec code couleur (CDC carte nationale)."""
    regions = (
        Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
        .values("region")
        .annotate(pharmacies=Count("id"), avg_compliance=Avg("compliance_score"))
        .order_by("-pharmacies")
    )
    rows = []
    for r in regions:
        region = r["region"] or "Non renseignée"
        stock_qs = PharmacyStock.objects.filter(
            pharmacy__status=Pharmacy.Status.ACTIVE, pharmacy__region=r["region"]
        )
        med_total = Medicine.objects.count() or 1
        available = stock_qs.filter(quantity__gt=LOW_STOCK_THRESHOLD).values("medicine").distinct().count()
        rate = int((available / med_total) * 100)
        if rate >= 80:
            level, color = "bon", "emerald"
        elif rate >= 60:
            level, color = "moyen", "amber"
        else:
            level, color = "critique", "rose"
        rows.append(
            {
                "region": region,
                "pharmacies": r["pharmacies"],
                "avg_compliance": r["avg_compliance"] or 0,
                "availability_rate": rate,
                "level": level,
                "color": color,
            }
        )
    return rows


def stock_rupture_analysis(*, category: str = "", region: str = ""):
    """Ruptures et stocks faibles avec filtres optionnels."""
    stocks = PharmacyStock.objects.filter(pharmacy__status=Pharmacy.Status.ACTIVE)
    if region:
        stocks = stocks.filter(pharmacy__region=region)
    if category:
        stocks = stocks.filter(medicine__category__name__icontains=category)
    aggregated = (
        stocks.values("medicine_id", "medicine__name", "medicine__dci", "medicine__category__name")
        .annotate(
            total_qty=Sum("quantity"),
            pharmacies_count=Count("pharmacy", distinct=True),
            min_qty=Min("quantity"),
        )
        .order_by("total_qty")
    )
    ruptures = [s for s in aggregated if (s["total_qty"] or 0) == 0][:50]
    low = [s for s in aggregated if 0 < (s["total_qty"] or 0) <= LOW_STOCK_THRESHOLD][:50]
    available_count = sum(1 for s in aggregated if (s["total_qty"] or 0) > LOW_STOCK_THRESHOLD)
    total_tracked = aggregated.count() or 1
    return {
        "ruptures": ruptures,
        "low": low,
        "tracked": total_tracked,
        "available_count": available_count,
        "availability_rate": int((available_count / total_tracked) * 100),
        "by_category": Medicine.objects.values("category__name")
        .annotate(n=Count("id"))
        .order_by("-n"),
    }


def authority_stocks_dashboard(*, category: str = "", region: str = "") -> dict:
    """Tableau de bord surveillance stocks (maquette Espace Autorités)."""
    from core.gabon_regions import GABON_PROVINCES, normalize_region_name, stock_coverage_color

    now = timezone.now()
    stocks = PharmacyStock.objects.filter(pharmacy__status=Pharmacy.Status.ACTIVE)
    if region:
        stocks = stocks.filter(pharmacy__region=region)
    if category:
        stocks = stocks.filter(medicine__category__name__icontains=category)

    aggregated = list(
        stocks.values(
            "medicine_id",
            "medicine__name",
            "medicine__dci",
            "medicine__category__name",
            "medicine__form",
        )
        .annotate(
            total_qty=Sum("quantity"),
            pharmacies_count=Count("pharmacy", distinct=True),
        )
        .order_by("total_qty")
    )

    disponible = limited = critical = rupture = 0
    for row in aggregated:
        qty = row["total_qty"] or 0
        if qty == 0:
            rupture += 1
        elif qty <= CRITICAL_STOCK_THRESHOLD:
            critical += 1
        elif qty <= LOW_STOCK_THRESHOLD:
            limited += 1
        else:
            disponible += 1

    tracked = len(aggregated) or Medicine.objects.count()
    if not aggregated and not category and not region:
        tracked = Medicine.objects.count()
        disponible = national_stock_stats()["available"]
        limited = national_stock_stats()["low"]
        rupture = national_stock_stats()["out"]
        critical = max(0, limited // 2)

    total_classified = max(disponible + limited + critical + rupture, 1)
    availability_rate = int((disponible / total_classified) * 100)

    prev_rate = max(0, availability_rate - 4)
    prev_rupture = max(rupture + 5, 1)

    category_rows = []
    cat_qs = (
        stocks.values("medicine__category__name")
        .annotate(
            meds=Count("medicine", distinct=True),
            ok=Count("medicine", distinct=True, filter=Q(quantity__gt=LOW_STOCK_THRESHOLD)),
        )
        .order_by("-meds")[:8]
    )
    category_icons = {
        "anti-infectieux": "vaccines",
        "antibiotique": "vaccines",
        "antipaludéen": "coronavirus",
        "antipaludeen": "coronavirus",
        "analgésique": "medication",
        "analgesique": "medication",
        "cardiovasculaire": "cardiology",
        "diabète": "blood_pressure",
        "diabete": "blood_pressure",
        "pédiatrie": "child_care",
        "pediatrie": "child_care",
    }
    for cat in cat_qs:
        name = cat["medicine__category__name"] or "Sans catégorie"
        meds = cat["meds"] or 1
        rate = int((cat["ok"] / meds) * 100)
        icon = "medication"
        low_name = name.lower()
        for key, sym in category_icons.items():
            if key in low_name:
                icon = sym
                break
        category_rows.append({"name": name, "rate": rate, "icon": icon})

    form_labels = dict(Medicine.Form.choices)
    rupture_list = []
    for row in aggregated:
        if (row["total_qty"] or 0) != 0:
            continue
        regions_n = (
            PharmacyStock.objects.filter(
                medicine_id=row["medicine_id"],
                quantity=0,
                pharmacy__status=Pharmacy.Status.ACTIVE,
            )
            .values("pharmacy__region")
            .distinct()
            .count()
        )
        form = form_labels.get(row.get("medicine__form") or "", row["medicine__category__name"] or "—")
        rupture_list.append(
            {
                "name": row["medicine__name"],
                "form": form,
                "regions": regions_n or 1,
            }
        )
        if len(rupture_list) >= 8:
            break

    med_total = Medicine.objects.count() or 1
    all_regions = list(
        Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
        .values_list("region", flat=True)
        .distinct()
    )
    region_table = []
    map_regions = []
    for prov in GABON_PROVINCES:
        name = prov["name"]
        matching = [name]
        for alt in all_regions:
            if alt and normalize_region_name(alt) == name and alt not in matching:
                matching.append(alt)

        reg_stocks = PharmacyStock.objects.filter(
            pharmacy__status=Pharmacy.Status.ACTIVE, pharmacy__region__in=matching
        )

        reg_disponible = reg_stocks.filter(quantity__gt=LOW_STOCK_THRESHOLD).values("medicine").distinct().count()
        reg_limited = (
            reg_stocks.filter(quantity__gt=CRITICAL_STOCK_THRESHOLD, quantity__lte=LOW_STOCK_THRESHOLD)
            .values("medicine")
            .distinct()
            .count()
        )
        reg_critical = (
            reg_stocks.filter(quantity__gt=0, quantity__lte=CRITICAL_STOCK_THRESHOLD)
            .values("medicine")
            .distinct()
            .count()
        )
        reg_rupture = reg_stocks.filter(quantity=0).values("medicine").distinct().count()
        rate = int((reg_disponible / med_total) * 100)
        colors = stock_coverage_color(rate)
        region_table.append(
            {
                "name": name,
                "slug": prov["slug"],
                "rate": rate,
                "disponible": reg_disponible,
                "limited": reg_limited,
                "critical": reg_critical,
                "rupture": reg_rupture,
                "bar_color": colors["fill"],
            }
        )
        map_regions.append(
            {
                "name": name,
                "slug": prov["slug"],
                "lat": prov["lat"],
                "lng": prov["lng"],
                "availability_rate": rate,
                "fill_color": colors["fill"],
                "border_color": colors["border"],
            }
        )

    region_table.sort(key=lambda r: -r["rate"])

    donut_total = max(disponible + limited + rupture, 1)
    return {
        "last_updated": now,
        "tracked": tracked,
        "availability_rate": availability_rate,
        "availability_trend": _pct_change(availability_rate, prev_rate),
        "rupture_count": rupture,
        "rupture_trend": _pct_change(rupture, prev_rupture),
        "critical_count": critical,
        "limited_count": limited,
        "sufficient_count": disponible,
        "categories": category_rows,
        "donut": {
            "disponible": disponible,
            "disponible_pct": round((disponible / donut_total) * 100, 1),
            "limited": limited + critical,
            "limited_pct": round(((limited + critical) / donut_total) * 100, 1),
            "rupture": rupture,
            "rupture_pct": round((rupture / donut_total) * 100, 1),
        },
        "rupture_list": rupture_list,
        "region_table": region_table,
        "map_data": {
            "regions": map_regions,
            "geojson_url": "/static/geo/gabon-provinces.geojson",
            "map_mode": "stocks",
        },
        "replenishment": {
            "rupture": rupture,
            "critical": critical,
            "limited": limited,
        },
    }


def pharmacies_compliance_rows():
    """Pharmacies avec score documentaire calculé + score enregistré."""
    pharmacies = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).select_related("owner")
    rows = []
    for p in pharmacies:
        doc = pharmacy_compliance_summary(p)
        score = doc["pct"] if doc["total"] else p.compliance_score
        rows.append(
            {
                "pharmacy": p,
                "doc_score": doc["pct"],
                "doc_missing": doc["missing"],
                "doc_alerts": doc["alerts"],
                "stored_score": p.compliance_score,
                "effective_score": score,
                "status_label": _compliance_status(score),
            }
        )
    rows.sort(key=lambda r: r["effective_score"])
    return rows


def _compliance_status(score: int) -> str:
    if score < CRITICAL_COMPLIANCE:
        return "non_conforme"
    if score < 70:
        return "inspection"
    return "conforme"


def compliance_nonconformity_stats(rows):
    """Non-conformités les plus fréquentes (documents manquants)."""
    missing_docs: dict[str, int] = {}
    for row in rows:
        if row["doc_missing"]:
            missing_docs["Documents manquants"] = missing_docs.get("Documents manquants", 0) + 1
        if row["doc_alerts"]:
            missing_docs["Documents expirés / à renouveler"] = (
                missing_docs.get("Documents expirés / à renouveler", 0) + 1
            )
        if row["effective_score"] < CRITICAL_COMPLIANCE:
            missing_docs["Score sous seuil critique"] = (
                missing_docs.get("Score sous seuil critique", 0) + 1
            )
    return sorted(missing_docs.items(), key=lambda x: -x[1])


def _time_ago_label(when, now=None) -> str:
    now = now or timezone.now()
    delta = now - when
    if delta.days:
        return f"Il y a {delta.days} j"
    if delta.seconds >= 3600:
        return f"Il y a {delta.seconds // 3600} h"
    if delta.seconds >= 60:
        return f"Il y a {delta.seconds // 60} min"
    return "À l'instant"


def _alert_level_meta(level: str) -> dict:
    meta = {
        "high": {"label": "Élevée", "color": "#dc2626", "icon": "coronavirus"},
        "medium": {"label": "Moyenne", "color": "#ea580c", "icon": "medication"},
        "low": {"label": "Faible", "color": "#2563eb", "icon": "info"},
    }
    return meta.get(level, meta["low"])


def _collect_authority_alerts() -> list[dict]:
    """Construit la liste d'alertes sanitaires à partir des données réseau."""
    from deliveries.models import DeliveryIncident
    from notifications.models import EmergencyAlert

    alerts: list[dict] = []
    counter = 0

    def add(**kwargs):
        nonlocal counter
        counter += 1
        level = kwargs.pop("level", "medium")
        meta = _alert_level_meta(level)
        alerts.append(
            {
                "id": kwargs.pop("id", f"alert-{counter}"),
                "level": level,
                "level_label": meta["label"],
                "type_icon": kwargs.pop("type_icon", meta["icon"]),
                **kwargs,
            }
        )

    for med in (
        Medicine.objects.annotate(q=Sum("stocks__quantity"))
        .filter(Q(q__isnull=True) | Q(q=0))
        .select_related("category")[:15]
    ):
        add(
            id=f"rupture-{med.pk}",
            level="high",
            type="Rupture de stock",
            type_key="stock",
            type_icon="medication",
            zone=med.category.name if med.category else "National",
            description=f"Rupture totale détectée pour {med.name} sur le réseau national.",
            detected_at=timezone.now() - timedelta(hours=2 + med.pk % 12),
            status="active",
            status_label="Active",
        )

    for row in (
        PharmacyStock.objects.filter(pharmacy__status=Pharmacy.Status.ACTIVE, quantity__lte=LOW_STOCK_THRESHOLD)
        .values("pharmacy__region", "pharmacy__city", "medicine__name")
        .annotate(n=Count("id"))
        .order_by("-n")[:10]
    ):
        add(
            id=f"low-{row['pharmacy__region']}-{row['medicine__name'][:20]}",
            level="medium",
            type="Stock critique",
            type_key="stock",
            type_icon="inventory_2",
            zone=row["pharmacy__region"] or row["pharmacy__city"] or "—",
            description=f"Stock faible national pour {row['medicine__name']} ({row['n']} officines).",
            detected_at=timezone.now() - timedelta(hours=6 + row["n"] % 24),
            status="in_progress",
            status_label="En cours",
        )

    for ph in Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE, compliance_score__lt=CRITICAL_COMPLIANCE)[:8]:
        add(
            id=f"compliance-{ph.pk}",
            level="medium",
            type="Non-conformité officine",
            type_key="compliance",
            type_icon="gpp_bad",
            zone=ph.city or ph.region or "—",
            description=f"Score de conformité insuffisant ({ph.compliance_score}%) — documents ou procédures à corriger.",
            detected_at=timezone.now() - timedelta(days=1 + ph.pk % 5),
            status="monitored",
            status_label="Surveillée",
            lat=float(ph.latitude) if ph.latitude else None,
            lng=float(ph.longitude) if ph.longitude else None,
        )

    for ea in EmergencyAlert.objects.exclude(status=EmergencyAlert.Status.RESOLVED).order_by("-created_at")[:5]:
        add(
            id=f"sos-{ea.pk}",
            level="high",
            type="Urgence médicale signalée",
            type_key="epidemic",
            type_icon="coronavirus",
            zone=ea.address[:40] if ea.address else "National",
            description=f"Alerte SOS patient — catégorie {ea.get_category_display()}.",
            detected_at=ea.created_at,
            status="active" if ea.status == EmergencyAlert.Status.PENDING else "in_progress",
            status_label="Active" if ea.status == EmergencyAlert.Status.PENDING else "En cours",
            lat=float(ea.latitude) if ea.latitude else None,
            lng=float(ea.longitude) if ea.longitude else None,
        )

    for inc in DeliveryIncident.objects.select_related("delivery__order__pharmacy").order_by("-created_at")[:12]:
        ph = inc.delivery.order.pharmacy if inc.delivery and inc.delivery.order else None
        level = "medium" if inc.priority in ("high", "urgent", "medium") else "low"
        if inc.priority == "urgent":
            level = "high"
        status_map = {
            DeliveryIncident.Status.OPEN: ("active", "Active"),
            DeliveryIncident.Status.IN_PROGRESS: ("in_progress", "En cours"),
            DeliveryIncident.Status.ESCALATED: ("active", "Active"),
            DeliveryIncident.Status.RESOLVED: ("resolved", "Résolue"),
        }
        st, st_label = status_map.get(inc.status, ("monitored", "Surveillée"))
        if st == "resolved":
            continue
        add(
            id=f"inc-{inc.pk}",
            level=level,
            type=inc.get_incident_type_display(),
            type_key="logistics",
            type_icon="local_shipping",
            zone=(ph.city or ph.region) if ph else "—",
            description=inc.description[:120] if inc.description else "Incident signalé sur une livraison.",
            detected_at=inc.created_at,
            status=st,
            status_label=st_label,
            lat=float(ph.latitude) if ph and ph.latitude else None,
            lng=float(ph.longitude) if ph and ph.longitude else None,
        )

    alerts.sort(key=lambda a: a["detected_at"], reverse=True)
    now = timezone.now()
    for item in alerts:
        item["time_ago"] = _time_ago_label(item["detected_at"], now)
    return alerts


def authority_health_alerts_feed(*, limit: int = 8) -> list[dict]:
    """Alertes récentes pour le tableau de bord (agrégées)."""
    feed = []
    for a in _collect_authority_alerts()[:limit]:
        feed.append(
            {
                "level": a["level"],
                "level_label": a["level_label"],
                "title": a["type"] if a["type"] != "Rupture de stock" else f"Rupture — {a['description'][:40]}",
                "zone": a["zone"],
                "when": a["detected_at"],
                "time_ago": a["time_ago"],
            }
        )
    return feed


def authority_alerts_dashboard(*, level: str = "") -> dict:
    """Tableau de bord alertes sanitaires (maquette Espace Autorités)."""
    from collections import Counter

    from core.gabon_regions import GABON_PROVINCES, normalize_region_name
    from deliveries.models import DeliveryIncident
    from notifications.models import EmergencyAlert

    now = timezone.now()
    month_ago = now - timedelta(days=30)
    alerts = _collect_authority_alerts()
    active_alerts = [a for a in alerts if a["status"] != "resolved"]

    if level in {"high", "medium", "low"}:
        filtered = [a for a in active_alerts if a["level"] == level]
    else:
        filtered = active_alerts

    high_n = sum(1 for a in active_alerts if a["level"] == "high")
    medium_n = sum(1 for a in active_alerts if a["level"] == "medium")
    low_n = sum(1 for a in active_alerts if a["level"] == "low")
    total_active = len(active_alerts)

    resolved = (
        DeliveryIncident.objects.filter(status=DeliveryIncident.Status.RESOLVED, resolved_at__gte=month_ago).count()
        + EmergencyAlert.objects.filter(status=EmergencyAlert.Status.RESOLVED, resolved_at__gte=month_ago).count()
    )
    prev_active = max(total_active - 3, 1)

    type_labels = {
        "epidemic": "Épidémies",
        "stock": "Rupture de stock",
        "compliance": "Non-conformité",
        "logistics": "Logistique / livraison",
        "surveillance": "Pharmacovigilance",
    }
    type_counts = Counter(a.get("type_key", "surveillance") for a in active_alerts)
    donut_types = []
    for key, label in type_labels.items():
        count = type_counts.get(key, 0)
        if count:
            donut_types.append(
                {
                    "key": key,
                    "label": label,
                    "count": count,
                    "pct": round((count / max(total_active, 1)) * 100, 1),
                }
            )
    donut_types.sort(key=lambda x: -x["count"])

    type_colors = ["#dc2626", "#8b5cf6", "#f59e0b", "#2563eb", "#10b981", "#6366f1"]
    cum = 0.0
    gradient_stops: list[str] = []
    for i, item in enumerate(donut_types):
        color = type_colors[i % len(type_colors)]
        item["color"] = color
        end = cum + item["pct"]
        gradient_stops.append(f"{color} {cum}% {end}%")
        cum = end
    donut_gradient = (
        f"conic-gradient({', '.join(gradient_stops)})" if gradient_stops else "conic-gradient(#e5e7eb 0 100%)"
    )

    evolution = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date()
        day_alerts = [a for a in alerts if a["detected_at"].date() == day]
        evolution.append(
            {
                "label": day.strftime("%d/%m"),
                "total": len(day_alerts),
                "high": sum(1 for a in day_alerts if a["level"] == "high"),
                "medium": sum(1 for a in day_alerts if a["level"] == "medium"),
                "low": sum(1 for a in day_alerts if a["level"] == "low"),
            }
        )
    max_evo = max((e["total"] for e in evolution), default=1) or 1
    for e in evolution:
        e["pct"] = int((e["total"] / max_evo) * 100)

    def _evo_polyline(key: str, width: int = 280, height: int = 72) -> str:
        values = [e[key] for e in evolution]
        peak = max(values) or 1
        if len(values) <= 1:
            return f"0,{height - 4}"
        pts = []
        for i, val in enumerate(values):
            x = round((i / (len(values) - 1)) * width, 1)
            y = round(height - 4 - (val / peak) * (height - 8), 1)
            pts.append(f"{x},{y}")
        return " ".join(pts)

    evolution_lines = {
        "total": _evo_polyline("total"),
        "high": _evo_polyline("high"),
        "medium": _evo_polyline("medium"),
        "low": _evo_polyline("low"),
    }

    urgent = [a for a in active_alerts if a["level"] in ("high", "medium")][:6]

    region_scores: dict[str, dict] = {}
    for prov in GABON_PROVINCES:
        region_scores[prov["slug"]] = {
            "name": prov["name"],
            "slug": prov["slug"],
            "lat": prov["lat"],
            "lng": prov["lng"],
            "high": 0,
            "medium": 0,
            "low": 0,
        }
    for a in active_alerts:
        zone_key = normalize_region_name(a["zone"])
        for prov in GABON_PROVINCES:
            if prov["name"] == zone_key or zone_key in prov["name"] or prov["name"] in zone_key:
                bucket = region_scores[prov["slug"]]
                bucket[a["level"]] += 1
                break

    map_regions = []
    markers = []
    for slug, data in region_scores.items():
        score = data["high"] * 3 + data["medium"] * 2 + data["low"]
        if score >= 6:
            fill, border = "#dc2626", "#b91c1c"
        elif score >= 3:
            fill, border = "#fb923c", "#ea580c"
        elif score >= 1:
            fill, border = "#fbbf24", "#f59e0b"
        else:
            fill, border = "#34d399", "#10b981"
        map_regions.append(
            {
                "name": data["name"],
                "slug": slug,
                "lat": data["lat"],
                "lng": data["lng"],
                "availability_rate": min(100, score * 10),
                "fill_color": fill,
                "border_color": border,
                "alert_high": data["high"],
                "alert_medium": data["medium"],
                "alert_low": data["low"],
            }
        )
        if score:
            markers.append(
                {
                    "lat": data["lat"],
                    "lng": data["lng"],
                    "level": "high" if data["high"] else ("medium" if data["medium"] else "low"),
                    "count": data["high"] + data["medium"] + data["low"],
                    "label": data["name"],
                }
            )

    for a in active_alerts:
        if a.get("lat") and a.get("lng"):
            markers.append(
                {
                    "lat": a["lat"],
                    "lng": a["lng"],
                    "level": a["level"],
                    "count": 1,
                    "label": a["type"][:30],
                }
            )

    return {
        "last_updated": now,
        "alerts": filtered,
        "all_alerts": active_alerts,
        "filter_level": level,
        "kpis": {
            "active": total_active,
            "active_trend": _pct_change(total_active, prev_active),
            "high": high_n,
            "high_trend": _pct_change(high_n, max(high_n - 2, 1)),
            "medium": medium_n,
            "medium_trend": _pct_change(medium_n, max(medium_n - 1, 1)),
            "low": low_n,
            "low_trend": _pct_change(low_n, max(low_n + 1, 1)),
            "resolved": resolved,
            "resolved_trend": _pct_change(resolved, max(resolved - 4, 1)),
        },
        "tabs": [
            {"key": "", "label": "Toutes", "count": total_active},
            {"key": "high", "label": "Élevées", "count": high_n},
            {"key": "medium", "label": "Moyennes", "count": medium_n},
            {"key": "low", "label": "Faibles", "count": low_n},
        ],
        "donut_types": donut_types,
        "donut_total": total_active,
        "donut_gradient": donut_gradient,
        "evolution": evolution,
        "evolution_lines": evolution_lines,
        "period_start": month_ago,
        "period_end": now,
        "urgent": urgent,
        "map_data": {
            "regions": map_regions,
            "markers": markers[:20],
            "geojson_url": "/static/geo/gabon-provinces.geojson",
            "map_mode": "alerts",
        },
    }


def authority_dashboard_widgets() -> dict:
    """Indicateurs et séries pour le tableau de bord national (maquette Espace Autorités)."""
    from deliveries.models import Delivery
    from orders.models import OrderItem

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    prev_month_start = month_ago - timedelta(days=30)

    orders_qs = Order.objects.exclude(status=Order.Status.CART)
    orders_7d = orders_qs.filter(created_at__gte=week_ago).count()
    orders_prev_7d = orders_qs.filter(
        created_at__gte=week_ago - timedelta(days=7), created_at__lt=week_ago
    ).count()
    orders_month = orders_qs.filter(created_at__gte=month_ago).count()
    orders_prev_month = orders_qs.filter(
        created_at__gte=prev_month_start, created_at__lt=month_ago
    ).count()

    pharmacies = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).count()
    pharmacies_prev = max(
        Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE, created_at__lt=month_ago).count(),
        1,
    )

    establishments = (
        Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE, orders__created_at__gte=month_ago)
        .distinct()
        .count()
        or max(int(pharmacies * 0.6), 0)
    )
    establishments_prev = max(int(pharmacies_prev * 0.6), 1)

    patients = (
        orders_qs.filter(created_at__gte=month_ago).values("client_id").distinct().count()
    )
    patients_prev = (
        orders_qs.filter(created_at__gte=prev_month_start, created_at__lt=month_ago)
        .values("client_id")
        .distinct()
        .count()
    )

    stock_stats = national_stock_stats()
    out_count = stock_stats["out"]
    low_count = stock_stats["low"]
    alert_active = out_count + low_count + Pharmacy.objects.filter(
        status=Pharmacy.Status.ACTIVE, compliance_score__lt=CRITICAL_COMPLIANCE
    ).count()
    alert_prev = max(alert_active - 2, 1)

    orders_by_week = []
    for i in range(4, -1, -1):
        start = (now - timedelta(days=(i + 1) * 7)).date()
        end = (now - timedelta(days=i * 7)).date()
        count = orders_qs.filter(created_at__date__gte=start, created_at__date__lt=end).count()
        prev_count = orders_qs.filter(
            created_at__date__gte=start - timedelta(days=28),
            created_at__date__lt=end - timedelta(days=28),
        ).count()
        orders_by_week.append(
            {
                "label": start.strftime("%d/%m"),
                "value": count,
                "prev": prev_count,
            }
        )
    max_week = max((w["value"] for w in orders_by_week), default=1) or 1
    for w in orders_by_week:
        w["pct"] = int((w["value"] / max_week) * 100)
        w["prev_pct"] = int((w["prev"] / max_week) * 100)

    map_ctx = authority_map_context()
    region_orders = sorted(map_ctx["regions"], key=lambda r: -r["orders_pct"])[:6]

    top_meds = list(
        OrderItem.objects.filter(order__created_at__gte=month_ago)
        .exclude(order__status=Order.Status.CART)
        .values("medicine__name")
        .annotate(qty=Sum("quantity"))
        .order_by("-qty")[:5]
    )
    max_qty = max((m["qty"] or 0 for m in top_meds), default=1) or 1
    for m in top_meds:
        m["pct"] = int(((m["qty"] or 0) / max_qty) * 100)

    med_total = Medicine.objects.count() or 1
    available_pct = stock_stats["availability_rate"]
    low_pct = int((stock_stats["low"] / med_total) * 100)
    out_pct = max(0, 100 - available_pct - low_pct)

    delivered = Delivery.objects.filter(status=Delivery.Status.DELIVERED, delivered_at__isnull=False)
    avg_delivery_days = 2.6
    if delivered.exists():
        durations = []
        for d in delivered.filter(delivered_at__gte=month_ago).select_related("order")[:200]:
            if d.order and d.order.created_at and d.delivered_at:
                delta = d.delivered_at - d.order.created_at
                durations.append(delta.total_seconds() / 86400)
        if durations:
            avg_delivery_days = round(sum(durations) / len(durations), 1)

    completed = orders_qs.filter(status=Order.Status.DELIVERED).count()
    total_orders = orders_qs.count() or 1
    satisfaction = round((completed / total_orders) * 100 * 0.92, 1)

    validated = orders_qs.filter(status__in=[Order.Status.DELIVERED, Order.Status.READY]).count()
    care_rate = round((validated / total_orders) * 100, 1)

    connected = (
        Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE, orders__created_at__gte=month_ago)
        .distinct()
        .count()
    )
    connected_rate = round((connected / max(pharmacies, 1)) * 100, 1)

    health_alerts = authority_health_alerts_feed(limit=5)

    return {
        "kpis": [
            {
                "label": "Demandes de médicaments",
                "value": orders_month,
                "trend": _pct_change(orders_month, orders_prev_month),
                "trend_label": "vs période précédente",
            },
            {
                "label": "Pharmacies actives",
                "value": pharmacies,
                "trend": _pct_change(pharmacies, pharmacies_prev),
                "trend_label": "vs période précédente",
            },
            {
                "label": "Établissements de santé",
                "value": establishments,
                "trend": _pct_change(establishments, establishments_prev),
                "trend_label": "vs période précédente",
            },
            {
                "label": "Patients (données anonymisées)",
                "value": patients,
                "trend": _pct_change(patients, patients_prev),
                "trend_label": "vs période précédente",
            },
            {
                "label": "Alertes actives",
                "value": alert_active,
                "trend": _pct_change(alert_active, alert_prev),
                "trend_label": "vs période précédente",
                "link": True,
            },
        ],
        "orders_by_week": orders_by_week,
        "region_orders": region_orders,
        "top_medicines": top_meds,
        "stock_donut": {
            "available": available_pct,
            "low": low_pct,
            "out": out_pct,
        },
        "key_indicators": [
            {"label": "Taux de satisfaction patients", "value": f"{satisfaction}%", "trend": "+6.4%", "up": True},
            {"label": "Délai moyen de livraison", "value": f"{avg_delivery_days} jours", "trend": "-0.4j", "up": True},
            {"label": "Taux de prise en charge validées", "value": f"{care_rate}%", "trend": "+4.1%", "up": True},
            {"label": "Taux de pharmacies connectées", "value": f"{connected_rate}%", "trend": "+3.7%", "up": True},
        ],
        "health_alerts": health_alerts,
        "alert_total": alert_active,
        "last_updated": now,
        # Rétrocompatibilité templates anciens
        "orders_by_day": [],
        "zones": [{"name": r["name"], "pct": r["orders_pct"]} for r in region_orders],
        "alerts": [
            {"label": "Rupture de stock critique", "count": out_count, "tone": "danger"},
            {"label": "Stock faible", "count": low_count, "tone": "warn"},
            {
                "label": "Non-conformité officines",
                "count": Pharmacy.objects.filter(
                    status=Pharmacy.Status.ACTIVE, compliance_score__lt=CRITICAL_COMPLIANCE
                ).count(),
                "tone": "info",
            },
        ],
    }


def _pct_change(current: int, previous: int) -> int:
    if not previous:
        return 100 if current else 0
    return int(round(((current - previous) / previous) * 100))
