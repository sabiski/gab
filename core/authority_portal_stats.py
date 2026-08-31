"""Statistiques santé publique — données réelles uniquement (portail Autorités)."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import ClientProfile, User
from catalog.models import Medicine, PharmacyStock
from core.pharmacy_compliance import (
    pharmacy_compliance_summary,
    pharmacy_document_checklist,
)
from pharmacies.models import Pharmacy, PharmacyDocument, PharmacyEmployee
from core.gabon_regions import GABON_PROVINCES, incidence_color, normalize_region_name
from deliveries.models import Delivery
from notifications.models import EmergencyAlert, SupportTicket
from orders.models import Order, OrderItem, OrderEvaluation

SURVEILLANCE_DISEASES = (
    {
        "key": "malaria",
        "label": "Paludisme",
        "color": "#228545",
        "keywords": ("palud", "artem", "quinine", "antipalud", "malar", "nivaquine"),
    },
    {
        "key": "tuberculosis",
        "label": "Tuberculose",
        "color": "#2563eb",
        "keywords": ("tubercul", "isoniaz", "rifamp", "ethamb", "myambutol"),
    },
    {
        "key": "respiratory",
        "label": "Infections respiratoires",
        "color": "#8b5cf6",
        "keywords": (
            "respirat",
            "bronch",
            "asthme",
            "toux",
            "grippe",
            "covid",
            "rhume",
            "amoxicill",
            "azithrom",
            "paracétamol",
            "paracetamol",
        ),
    },
    {
        "key": "diarrhea",
        "label": "Diarrhées",
        "color": "#f59e0b",
        "keywords": ("diarr", "gastro", "réhydrat", "rehydrat", "smecta", "loperamid", "intestinal"),
    },
    {
        "key": "cholera",
        "label": "Choléra",
        "color": "#dc2626",
        "keywords": ("choler", "vibrio"),
    },
)

CHRONIC_CONDITIONS = (
    {
        "key": "hypertension",
        "label": "Hypertension",
        "icon": "cardiology",
        "keywords": ("hypertens", "amlodip", "losartan", "énalapril", "enalapril", "tension", "ramipril"),
    },
    {
        "key": "diabetes",
        "label": "Diabète",
        "icon": "glucose",
        "keywords": ("diab", "insulin", "metform", "glycém", "glycem", "gliclazide"),
    },
    {
        "key": "asthma",
        "label": "Asthme",
        "icon": "pulmonology",
        "keywords": ("asthme", "salbutamol", "ventolin", "bronchodil", "budesonide"),
    },
    {
        "key": "cardio",
        "label": "Cardiopathies",
        "icon": "favorite",
        "keywords": ("cardiaque", "cardio", "cœur", "coeur", "warfarin", "clopidogrel", "atorvastat"),
    },
)

AGE_BRACKETS = (
    ("0-14", 0, 14),
    ("15-24", 15, 24),
    ("25-34", 25, 34),
    ("35-44", 35, 44),
    ("45-54", 45, 54),
    ("55-64", 55, 64),
    ("65+", 65, 200),
)

ACTIVE_TREATMENT_STATUSES = {
    Order.Status.CONFIRMED,
    Order.Status.AWAITING_RX,
    Order.Status.PREPARING,
    Order.Status.READY,
    Order.Status.DELIVERING,
}


def _pct_change(current: int, previous: int) -> int:
    if not previous:
        return 100 if current else 0
    return int(round(((current - previous) / previous) * 100))


def _age_years(dob: date, ref: date | None = None) -> int:
    ref = ref or timezone.now().date()
    return ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))


def _medicine_text(med: Medicine) -> str:
    cat = med.category.name if med.category else ""
    return f"{med.name} {med.dci} {cat}".lower()


def _classify_disease(med: Medicine) -> str:
    text = _medicine_text(med)
    for disease in SURVEILLANCE_DISEASES:
        if any(kw in text for kw in disease["keywords"]):
            return disease["key"]
    return ""


def _classify_chronic(med: Medicine) -> str:
    text = _medicine_text(med)
    for cond in CHRONIC_CONDITIONS:
        if any(kw in text for kw in cond["keywords"]):
            return cond["key"]
    return ""


def _region_slug_filter(region: str) -> str:
    if not region:
        return ""
    low = region.strip().lower()
    for prov in GABON_PROVINCES:
        if prov["slug"] == low or prov["name"].lower() == low:
            return prov["slug"]
    return ""


def _order_items_qs(start, end, *, region_slug: str = ""):
    qs = (
        OrderItem.objects.filter(order__created_at__gte=start, order__created_at__lte=end)
        .exclude(order__status=Order.Status.CART)
        .select_related("medicine", "medicine__category", "order", "order__pharmacy")
    )
    if region_slug:
        prov_name = next((p["name"] for p in GABON_PROVINCES if p["slug"] == region_slug), "")
        if prov_name:
            qs = qs.filter(
                Q(order__pharmacy__region__icontains=prov_name)
                | Q(order__pharmacy__city__icontains=prov_name)
            )
    return qs


def _orders_qs(start, end, *, region_slug: str = ""):
    qs = Order.objects.filter(created_at__gte=start, created_at__lte=end).exclude(
        status=Order.Status.CART
    )
    if region_slug:
        prov_name = next((p["name"] for p in GABON_PROVINCES if p["slug"] == region_slug), "")
        if prov_name:
            qs = qs.filter(
                Q(pharmacy__region__icontains=prov_name) | Q(pharmacy__city__icontains=prov_name)
            )
    return qs


def _count_diseases_from_items(items) -> dict[str, int]:
    counts = {d["key"]: 0 for d in SURVEILLANCE_DISEASES}
    for item in items:
        key = _classify_disease(item.medicine)
        if key:
            counts[key] += int(item.quantity or 0)
    return counts


def _evo_polyline(values: list[int], width: int = 280, height: int = 80) -> str:
    peak = max(values) or 1
    if len(values) <= 1:
        return f"0,{height - 4}"
    pts = []
    for i, val in enumerate(values):
        x = round((i / (len(values) - 1)) * width, 1)
        y = round(height - 4 - (val / peak) * (height - 8), 1)
        pts.append(f"{x},{y}")
    return " ".join(pts)


def _donut_from_counts(items: list[dict], total: int) -> tuple[list[dict], str]:
    cum = 0.0
    stops: list[str] = []
    for item in items:
        pct = round((item["count"] / max(total, 1)) * 100, 1)
        item["pct"] = pct
        end = cum + pct
        stops.append(f"{item['color']} {cum}% {end}%")
        cum = end
    gradient = (
        f"conic-gradient({', '.join(stops)})" if stops else "conic-gradient(#e5e7eb 0 100%)"
    )
    return items, gradient


def _region_for_item(item) -> str:
    ph = item.order.pharmacy
    if not ph:
        return ""
    return normalize_region_name(ph.region or ph.city or "")


def authority_trends_dashboard(*, region: str = "", disease: str = "") -> dict:
    """Tendances épidémiologiques — demandes médicaments et alertes SOS réelles."""
    from core.authority_analytics import _collect_authority_alerts

    now = timezone.now()
    month_ago = now - timedelta(days=30)
    prev_start = month_ago - timedelta(days=30)
    region_slug = _region_slug_filter(region)
    disease_filter = disease if disease in {d["key"] for d in SURVEILLANCE_DISEASES} else ""

    items_now = list(_order_items_qs(month_ago, now, region_slug=region_slug))
    items_prev = list(_order_items_qs(prev_start, month_ago, region_slug=region_slug))

    disease_counts = _count_diseases_from_items(items_now)
    prev_disease_counts = _count_diseases_from_items(items_prev)

    sos_now = EmergencyAlert.objects.filter(created_at__gte=month_ago, created_at__lte=now).count()
    sos_prev = EmergencyAlert.objects.filter(
        created_at__gte=prev_start, created_at__lt=month_ago
    ).count()

    total_cases = sum(disease_counts.values()) + sos_now
    prev_cases = sum(prev_disease_counts.values()) + sos_prev
    monitored = sum(1 for c in disease_counts.values() if c > 0)
    prev_monitored = sum(1 for c in prev_disease_counts.values() if c > 0)

    total_population = sum(p["population"] for p in GABON_PROVINCES) or 1
    incidence = round((total_cases / total_population) * 100_000, 1)
    prev_incidence = round((prev_cases / total_population) * 100_000, 1)

    active_alerts = len([a for a in _collect_authority_alerts() if a["status"] != "resolved"])

    evolution = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date()
        day_start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
        day_end = day_start + timedelta(days=1)
        day_items = [
            it
            for it in items_now
            if day_start <= it.order.created_at < day_end
        ]
        day_counts = _count_diseases_from_items(day_items)
        day_sos = EmergencyAlert.objects.filter(
            created_at__gte=day_start, created_at__lt=day_end
        ).count()
        point = {
            "label": day.strftime("%d/%m"),
            "total": sum(day_counts.values()) + day_sos,
        }
        for d in SURVEILLANCE_DISEASES:
            point[d["key"]] = day_counts[d["key"]]
        evolution.append(point)

    diseases = []
    evolution_lines: dict[str, str] = {"total": _evo_polyline([e["total"] for e in evolution])}
    for d in SURVEILLANCE_DISEASES:
        line = _evo_polyline([e[d["key"]] for e in evolution])
        diseases.append({**d, "count": disease_counts[d["key"]], "evolution_line": line})
        evolution_lines[d["key"]] = line

    donut_types = []
    for d in SURVEILLANCE_DISEASES:
        count = disease_counts[d["key"]]
        if count:
            donut_types.append(
                {
                    "key": d["key"],
                    "label": d["label"],
                    "count": count,
                    "color": d["color"],
                }
            )
    if sos_now:
        donut_types.append(
            {
                "key": "sos",
                "label": "Alertes SOS",
                "count": sos_now,
                "color": "#6366f1",
            }
        )
    donut_types.sort(key=lambda x: -x["count"])
    donut_types, donut_gradient = _donut_from_counts(donut_types, max(total_cases, 1))

    region_item_counts: dict[str, dict[str, int]] = defaultdict(lambda: {d["key"]: 0 for d in SURVEILLANCE_DISEASES})
    region_totals: Counter[str] = Counter()
    for item in items_now:
        region_name = _region_for_item(item)
        if not region_name:
            continue
        key = _classify_disease(item.medicine)
        if key:
            region_item_counts[region_name][key] += int(item.quantity or 0)
            region_totals[region_name] += int(item.quantity or 0)

    prev_region_totals: Counter[str] = Counter()
    for item in items_prev:
        region_name = _region_for_item(item)
        key = _classify_disease(item.medicine)
        if region_name and key:
            prev_region_totals[region_name] += int(item.quantity or 0)

    region_rows = []
    map_regions = []
    for prov in GABON_PROVINCES:
        counts = region_item_counts.get(prov["name"], {d["key"]: 0 for d in SURVEILLANCE_DISEASES})
        region_cases = region_totals.get(prov["name"], 0)
        prev_region_cases = prev_region_totals.get(prov["name"], 0)
        region_incidence = round((region_cases / max(prov["population"], 1)) * 100_000, 1)
        colors = incidence_color(region_incidence)
        cells = []
        for d in SURVEILLANCE_DISEASES:
            count = counts[d["key"]]
            cells.append(
                {
                    "key": d["key"],
                    "label": d["label"],
                    "count": count,
                    "pct": int((count / max(region_cases, 1)) * 100),
                }
            )
        region_rows.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "diseases": cells,
                "total_cases": region_cases,
                "incidence": region_incidence,
                "trend": _pct_change(region_cases, prev_region_cases),
            }
        )
        map_regions.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "lat": prov["lat"],
                "lng": prov["lng"],
                "incidence_rate": region_incidence,
                "availability_rate": min(100, int(region_incidence)),
                "fill_color": colors["fill"],
                "border_color": colors["border"],
            }
        )
    region_rows.sort(key=lambda r: -r["total_cases"])

    epi_alerts = []
    for a in _collect_authority_alerts()[:5]:
        epi_alerts.append(
            {
                "level": a["level"],
                "badge": a["level_label"],
                "title": a["type"],
                "meta": f"{a['zone']} · {a['time_ago']}",
            }
        )

    med_demand = sum(int(it.quantity or 0) for it in items_now)

    if disease_filter:
        filtered_counts = disease_counts.get(disease_filter, 0)
        total_cases = filtered_counts
        diseases = [d for d in diseases if d["key"] == disease_filter]

    return {
        "last_updated": now,
        "period_start": month_ago,
        "period_end": now,
        "filter_region": region_slug,
        "filter_disease": disease_filter,
        "diseases": diseases,
        "kpis": {
            "monitored": monitored,
            "monitored_delta": monitored - prev_monitored,
            "total_cases": total_cases,
            "total_cases_trend": _pct_change(total_cases, prev_cases),
            "sos_alerts": sos_now,
            "sos_alerts_trend": _pct_change(sos_now, sos_prev),
            "incidence": incidence,
            "incidence_trend": _pct_change(int(incidence * 10), int(prev_incidence * 10)),
            "active_alerts": active_alerts,
            "medicine_demand": med_demand,
        },
        "key_indicators": [
            {
                "label": "Incidence",
                "value": str(incidence),
                "unit": "/100k",
                "trend": _pct_change(int(incidence * 10), int(prev_incidence * 10)),
                "icon": "monitoring",
            },
            {
                "label": "Alertes SOS",
                "value": str(sos_now),
                "unit": "",
                "trend": _pct_change(sos_now, sos_prev),
                "icon": "emergency",
            },
            {
                "label": "Demandes médicaments",
                "value": str(med_demand),
                "unit": "unités",
                "trend": _pct_change(
                    med_demand,
                    sum(int(it.quantity or 0) for it in items_prev),
                ),
                "icon": "medication",
            },
            {
                "label": "Alertes actives",
                "value": str(active_alerts),
                "unit": "",
                "trend": 0,
                "icon": "warning",
            },
        ],
        "donut_types": donut_types,
        "donut_total": total_cases,
        "donut_gradient": donut_gradient,
        "evolution": evolution,
        "evolution_lines": evolution_lines,
        "region_table": region_rows,
        "epi_alerts": epi_alerts,
        "map_data": {
            "regions": map_regions,
            "geojson_url": "/static/geo/gabon-provinces.geojson",
            "map_mode": "trends",
        },
        "has_data": total_cases > 0 or med_demand > 0,
    }


def authority_patients_dashboard(*, region: str = "") -> dict:
    """Statistiques patients anonymisées — commandes et profils réels."""
    now = timezone.now()
    month_ago = now - timedelta(days=30)
    prev_start = month_ago - timedelta(days=30)
    quarter_ago = now - timedelta(days=90)
    region_slug = _region_slug_filter(region)

    orders_now = _orders_qs(month_ago, now, region_slug=region_slug)
    orders_prev = _orders_qs(prev_start, month_ago, region_slug=region_slug)
    items_now = list(_order_items_qs(month_ago, now, region_slug=region_slug))

    active_patients = orders_now.values("client_id").distinct().count()
    prev_active_patients = orders_prev.values("client_id").distinct().count()
    consultations = orders_now.count()
    prev_consultations = orders_prev.count()

    new_signups = User.objects.filter(
        role=User.Role.CLIENT, date_joined__gte=month_ago, date_joined__lte=now
    ).count()
    prev_signups = User.objects.filter(
        role=User.Role.CLIENT, date_joined__gte=prev_start, date_joined__lt=month_ago
    ).count()

    ongoing_patients = (
        orders_now.filter(status__in=ACTIVE_TREATMENT_STATUSES).values("client_id").distinct().count()
    )
    prev_ongoing = (
        orders_prev.filter(status__in=ACTIVE_TREATMENT_STATUSES)
        .values("client_id")
        .distinct()
        .count()
    )

    client_ids = list(orders_now.values_list("client_id", flat=True).distinct())
    profiles = {
        p.user_id: p
        for p in ClientProfile.objects.filter(user_id__in=client_ids).select_related("user")
    }
    ages: list[int] = []
    age_buckets = {label: 0 for label, _, _ in AGE_BRACKETS}
    ref_date = now.date()
    for cid in client_ids:
        profile = profiles.get(cid)
        if profile and profile.date_of_birth:
            age = _age_years(profile.date_of_birth, ref_date)
            ages.append(age)
            for label, low, high in AGE_BRACKETS:
                if low <= age <= high:
                    age_buckets[label] += 1
                    break
    avg_age = round(sum(ages) / len(ages), 1) if ages else None

    profiled_count = len(ages)
    max_age = max(age_buckets.values()) or 1
    age_chart = [
        {
            "label": label,
            "count": age_buckets[label],
            "pct": round((age_buckets[label] / max(profiled_count, 1)) * 100, 1),
            "bar_pct": int((age_buckets[label] / max_age) * 100),
        }
        for label, _, _ in AGE_BRACKETS
    ]

    children = sum(age_buckets[l] for l, lo, hi in AGE_BRACKETS if hi <= 14)
    youth = sum(age_buckets[l] for l, lo, hi in AGE_BRACKETS if 15 <= lo <= 24)
    adults = sum(age_buckets[l] for l, lo, hi in AGE_BRACKETS if 25 <= lo <= 54)
    seniors = sum(age_buckets[l] for l, lo, hi in AGE_BRACKETS if lo >= 55)
    profiled = len(ages) or 1
    age_summary = [
        {"label": "Enfants (0-14)", "pct": round(children / profiled * 100, 1)},
        {"label": "Jeunes (15-24)", "pct": round(youth / profiled * 100, 1)},
        {"label": "Adultes (25-54)", "pct": round(adults / profiled * 100, 1)},
        {"label": "Seniors (55+)", "pct": round(seniors / profiled * 100, 1)},
    ]

    gender_available = False
    gender_split = {"female_pct": None, "male_pct": None, "female": 0, "male": 0}

    region_patients: Counter[str] = Counter()
    for row in orders_now.values("pharmacy__region", "pharmacy__city").annotate(
        n=Count("client_id", distinct=True)
    ):
        key = normalize_region_name(row["pharmacy__region"] or row["pharmacy__city"] or "")
        if key:
            region_patients[key] += row["n"]

    geo_rows = []
    map_regions = []
    total_geo = sum(region_patients.values()) or 1
    for prov in GABON_PROVINCES:
        count = region_patients.get(prov["name"], 0)
        rate = round((count / max(prov["population"], 1)) * 1000, 1)
        colors = incidence_color(rate)
        geo_rows.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "count": count,
                "pct": round((count / total_geo) * 100, 1),
            }
        )
        map_regions.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "lat": prov["lat"],
                "lng": prov["lng"],
                "incidence_rate": rate,
                "availability_rate": min(100, int(rate)),
                "fill_color": colors["fill"],
                "border_color": colors["border"],
            }
        )
    geo_rows.sort(key=lambda r: -r["count"])

    category_qs = (
        OrderItem.objects.filter(
            order__created_at__gte=month_ago,
            order__created_at__lte=now,
        )
        .exclude(order__status=Order.Status.CART)
    )
    if region_slug:
        prov_name = next((p["name"] for p in GABON_PROVINCES if p["slug"] == region_slug), "")
        if prov_name:
            category_qs = category_qs.filter(
                Q(order__pharmacy__region__icontains=prov_name)
                | Q(order__pharmacy__city__icontains=prov_name)
            )
    category_rows = (
        category_qs.values("medicine__category__name")
        .annotate(qty=Sum("quantity"))
        .order_by("-qty")[:5]
    )
    top_motives = []
    total_qty = sum(r["qty"] or 0 for r in category_rows) or 1
    motive_labels = {
        "Antibiotiques": "Infections / antibiotiques",
        "Antalgiques": "Douleur / fièvre",
        "Cardiologie": "Tension artérielle",
        "Diabète": "Diabète",
    }
    for row in category_rows:
        name = row["medicine__category__name"] or "Sans catégorie"
        qty = int(row["qty"] or 0)
        top_motives.append(
            {
                "label": motive_labels.get(name, name),
                "count": qty,
                "pct": round((qty / total_qty) * 100, 1),
                "bar_pct": int((qty / total_qty) * 100),
            }
        )

    new_patients_evo = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date()
        count = User.objects.filter(role=User.Role.CLIENT, date_joined__date=day).count()
        new_patients_evo.append({"label": day.strftime("%d/%m"), "count": count})
    new_patients_line = _evo_polyline([p["count"] for p in new_patients_evo], height=72)

    chronic_stats = []
    chronic_clients: dict[str, set[int]] = {c["key"]: set() for c in CHRONIC_CONDITIONS}
    for item in items_now:
        key = _classify_chronic(item.medicine)
        if key:
            chronic_clients[key].add(item.order.client_id)
    chronic_total = sum(len(s) for s in chronic_clients.values()) or 1
    for cond in CHRONIC_CONDITIONS:
        n = len(chronic_clients[cond["key"]])
        if n:
            chronic_stats.append(
                {
                    "key": cond["key"],
                    "label": cond["label"],
                    "icon": cond["icon"],
                    "count": n,
                    "pct": round((n / chronic_total) * 100, 1),
                }
            )
    chronic_stats.sort(key=lambda x: -x["count"])
    other_chronic = max(0, active_patients - sum(c["count"] for c in chronic_stats))

    delivery_modes = (
        orders_now.values("delivery_mode")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    mode_labels = {
        Order.DeliveryMode.HOME: "Livraison à domicile",
        Order.DeliveryMode.PICKUP: "Retrait en pharmacie",
        Order.DeliveryMode.EXPRESS: "Livraison express",
    }
    mode_colors = {
        Order.DeliveryMode.HOME: "#228545",
        Order.DeliveryMode.PICKUP: "#2563eb",
        Order.DeliveryMode.EXPRESS: "#f59e0b",
    }
    channel_donut = []
    channel_total = sum(r["n"] for r in delivery_modes) or 1
    for row in delivery_modes:
        mode = row["delivery_mode"]
        channel_donut.append(
            {
                "label": mode_labels.get(mode, mode),
                "count": row["n"],
                "color": mode_colors.get(mode, "#9ca3af"),
            }
        )
    channel_donut, channel_gradient = _donut_from_counts(channel_donut, channel_total)

    activity_90 = []
    for i in range(89, -1, -1):
        day = (now - timedelta(days=i)).date()
        day_start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
        day_end = day_start + timedelta(days=1)
        activity_90.append(
            {
                "label": day.strftime("%d/%m") if i % 15 == 0 else "",
                "consultations": Order.objects.filter(
                    created_at__gte=day_start,
                    created_at__lt=day_end,
                )
                .exclude(status=Order.Status.CART)
                .count(),
                "medicine_searches": OrderItem.objects.filter(
                    order__created_at__gte=day_start,
                    order__created_at__lt=day_end,
                )
                .exclude(order__status=Order.Status.CART)
                .aggregate(q=Sum("quantity"))["q"]
                or 0,
                "deliveries": Delivery.objects.filter(
                    created_at__gte=day_start, created_at__lt=day_end
                ).count(),
            }
        )
    activity_lines = {
        "consultations": _evo_polyline([a["consultations"] for a in activity_90], height=72),
        "medicine_searches": _evo_polyline(
            [a["medicine_searches"] for a in activity_90], height=72
        ),
        "deliveries": _evo_polyline([a["deliveries"] for a in activity_90], height=72),
    }

    return {
        "last_updated": now,
        "period_start": month_ago,
        "period_end": now,
        "filter_region": region_slug,
        "kpis": {
            "active_patients": active_patients,
            "active_patients_trend": _pct_change(active_patients, prev_active_patients),
            "consultations": consultations,
            "consultations_trend": _pct_change(consultations, prev_consultations),
            "new_signups": new_signups,
            "new_signups_trend": _pct_change(new_signups, prev_signups),
            "ongoing_treatments": ongoing_patients,
            "ongoing_treatments_trend": _pct_change(ongoing_patients, prev_ongoing),
            "avg_age": avg_age,
            "profiles_with_age": len(ages),
            "gender_available": gender_available,
        },
        "gender_split": gender_split,
        "age_chart": age_chart,
        "age_summary": age_summary,
        "geo_rows": geo_rows,
        "top_motives": top_motives,
        "new_patients_evo": new_patients_evo,
        "new_patients_line": new_patients_line,
        "chronic_stats": chronic_stats,
        "chronic_other": other_chronic,
        "channel_donut": channel_donut,
        "channel_gradient": channel_gradient,
        "channel_total": channel_total,
        "activity_lines": activity_lines,
        "map_data": {
            "regions": map_regions,
            "geojson_url": "/static/geo/gabon-provinces.geojson",
            "map_mode": "trends",
        },
        "has_data": active_patients > 0 or consultations > 0,
    }


CRITICAL_COMPLIANCE_SCORE = 50
INSPECTION_SCORE = 70


def _pharmacy_structure_type(pharmacy: Pharmacy) -> tuple[str, str]:
    name = (pharmacy.name or "").lower()
    if pharmacy.status == Pharmacy.Status.PENDING:
        return "pending", "En attente d'agrément"
    if (
        pharmacy.is_24h
        or pharmacy.subscription_plan == Pharmacy.SubscriptionPlan.ENTERPRISE
        or any(k in name for k in ("hôpital", "hopital", "hospital", "chu", "clinique"))
    ):
        return "hospital_pharmacy", "Pharmacie hospitalière / 24h"
    if pharmacy.subscription_plan == Pharmacy.SubscriptionPlan.PROFESSIONAL:
        return "private_pharmacy", "Pharmacie privée"
    return "private_pharmacy", "Pharmacie privée"


def _pharmacy_compliance_bucket(score: int, status: str) -> str:
    if status in {Pharmacy.Status.SUSPENDED, Pharmacy.Status.REJECTED}:
        return "suspended"
    if status == Pharmacy.Status.PENDING:
        return "pending"
    if score < CRITICAL_COMPLIANCE_SCORE:
        return "non_conforme"
    if score < INSPECTION_SCORE:
        return "inspection"
    return "conforme"


def _pharmacy_nonconformity_tags(pharmacy: Pharmacy, effective_score: int) -> list[str]:
    tags: list[str] = []
    checklist, _ = pharmacy_document_checklist(pharmacy)
    missing = sum(1 for row in checklist if row["status"] == "missing")
    expired = sum(1 for row in checklist if row["status"] == "expired")
    if missing:
        tags.append("Documents réglementaires manquants")
    if expired:
        tags.append("Licence / autorisation expirée")
    ruptures = PharmacyStock.objects.filter(pharmacy=pharmacy, quantity=0).count()
    if ruptures >= 3:
        tags.append("Stock de médicaments insuffisant")
    has_pharmacist = PharmacyEmployee.objects.filter(
        pharmacy=pharmacy,
        is_active=True,
        job_role__in=[
            PharmacyEmployee.JobRole.PHARMACIST,
            PharmacyEmployee.JobRole.OWNER,
            PharmacyEmployee.JobRole.MANAGER,
        ],
    ).exists()
    if pharmacy.status == Pharmacy.Status.ACTIVE and not has_pharmacist:
        tags.append("Absence de personnel qualifié")
    if effective_score < CRITICAL_COMPLIANCE_SCORE:
        tags.append("Score de conformité sous seuil")
    return tags


def authority_pharmacies_dashboard(
    *,
    region: str = "",
    status: str = "",
    compliance: str = "",
    structure_type: str = "",
    search: str = "",
) -> dict:
    """Suivi pharmacies et conformité — données réelles du réseau Gab'Pharma."""
    from core.authority_analytics import national_stock_stats

    now = timezone.now()
    month_ago = now - timedelta(days=30)
    region_slug = _region_slug_filter(region)

    all_qs = Pharmacy.objects.exclude(status=Pharmacy.Status.REJECTED).select_related("owner")
    active_now = all_qs.filter(status=Pharmacy.Status.ACTIVE).count()
    active_prev = Pharmacy.objects.filter(
        status=Pharmacy.Status.ACTIVE, created_at__lt=month_ago
    ).count()
    pending_now = all_qs.filter(status=Pharmacy.Status.PENDING).count()
    pending_prev = Pharmacy.objects.filter(
        status=Pharmacy.Status.PENDING, created_at__lt=month_ago
    ).count()

    rows_raw = []
    conformity_counts = Counter()
    type_counts = Counter()
    region_counts = Counter()
    nc_counter = Counter()

    doc_latest = {}
    for item in PharmacyDocument.objects.values("pharmacy_id", "uploaded_at"):
        pid = item["pharmacy_id"]
        uploaded = item["uploaded_at"]
        if pid not in doc_latest or uploaded > doc_latest[pid]:
            doc_latest[pid] = uploaded

    for ph in all_qs:
        doc = pharmacy_compliance_summary(ph)
        effective_score = doc["pct"] if doc["total"] else ph.compliance_score
        bucket = _pharmacy_compliance_bucket(effective_score, ph.status)
        type_key, type_label = _pharmacy_structure_type(ph)
        region_name = normalize_region_name(ph.region or ph.city or "")

        conformity_counts[bucket] += 1
        type_counts[type_label] += 1
        if region_name:
            region_counts[region_name] += 1

        for tag in _pharmacy_nonconformity_tags(ph, effective_score):
            nc_counter[tag] += 1

        notes = []
        if doc["missing"]:
            notes.append(f"{doc['missing']} document(s) manquant(s)")
        if doc["alerts"]:
            notes.append(f"{doc['alerts']} document(s) à renouveler")

        rows_raw.append(
            {
                "id": ph.pk,
                "name": ph.name,
                "type_key": type_key,
                "type_label": type_label,
                "region": region_name or ph.region or ph.city or "—",
                "region_slug": next(
                    (p["slug"] for p in GABON_PROVINCES if p["name"] == region_name), ""
                ),
                "status": ph.status,
                "status_label": ph.get_status_display(),
                "compliance_bucket": bucket,
                "compliance_label": {
                    "conforme": "Conforme",
                    "inspection": "En inspection",
                    "non_conforme": "Non-conforme",
                    "suspended": "Suspendue",
                    "pending": "En attente",
                }[bucket],
                "compliance_score": effective_score,
                "last_inspection": doc_latest.get(ph.pk),
                "notes": " · ".join(notes) if notes else "—",
            }
        )

    conformes = conformity_counts["conforme"]
    non_conformes = conformity_counts["non_conforme"] + conformity_counts["inspection"]
    suspended = conformity_counts["suspended"]
    total_structures = len(rows_raw)
    total_for_pct = total_structures or 1

    region_conforme: Counter = Counter()
    region_total: Counter = Counter()
    for row in rows_raw:
        region_total[row["region"]] += 1
        if row["compliance_bucket"] == "conforme":
            region_conforme[row["region"]] += 1

    donut_slices = [
        {"label": "Conformes", "count": conformes, "color": "#228545"},
        {"label": "En inspection", "count": conformity_counts["inspection"], "color": "#f59e0b"},
        {"label": "Non-conformes", "count": conformity_counts["non_conforme"], "color": "#ea580c"},
        {"label": "Suspendues / en attente", "count": suspended + conformity_counts["pending"], "color": "#dc2626"},
    ]
    donut_slices = [s for s in donut_slices if s["count"]]
    donut_slices, donut_gradient = _donut_from_counts(donut_slices, total_for_pct)

    type_bars = []
    for label, count in type_counts.most_common():
        type_bars.append(
            {
                "label": label,
                "count": count,
                "pct": round((count / total_for_pct) * 100, 1),
                "bar_pct": int((count / max(type_counts.values() or [1])) * 100),
            }
        )

    geo_rows = []
    map_regions = []
    total_geo = sum(region_counts.values()) or 1
    for prov in GABON_PROVINCES:
        count = region_counts.get(prov["name"], 0)
        density = round((count / max(prov["population"], 1)) * 100_000, 1)
        colors = incidence_color(density)
        region_avail = 0
        if region_total.get(prov["name"]):
            region_avail = round(
                (region_conforme.get(prov["name"], 0) / region_total[prov["name"]]) * 100
            )
        geo_rows.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "count": count,
                "pct": round((count / total_geo) * 100, 1),
            }
        )
        map_regions.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "lat": prov["lat"],
                "lng": prov["lng"],
                "incidence_rate": density,
                "availability_rate": region_avail,
                "fill_color": colors["fill"],
                "border_color": colors["border"],
            }
        )
    geo_rows.sort(key=lambda r: -r["count"])

    nc_total = sum(nc_counter.values()) or 1
    nc_bars = [
        {
            "label": label,
            "count": nc_counter[label],
            "pct": round((nc_counter[label] / nc_total) * 100, 1),
            "bar_pct": int((nc_counter[label] / max(nc_counter.values() or [1])) * 100),
        }
        for label in [
            "Stock de médicaments insuffisant",
            "Licence / autorisation expirée",
            "Documents réglementaires manquants",
            "Absence de personnel qualifié",
            "Score de conformité sous seuil",
        ]
        if nc_counter[label]
    ]

    stock_stats = national_stock_stats()
    inspections = PharmacyDocument.objects.filter(uploaded_at__gte=month_ago).count()

    filtered = rows_raw
    if region_slug:
        filtered = [r for r in filtered if r["region_slug"] == region_slug]
    if status:
        filtered = [r for r in filtered if r["status"] == status]
    if compliance:
        filtered = [r for r in filtered if r["compliance_bucket"] == compliance]
    if structure_type:
        filtered = [r for r in filtered if r["type_key"] == structure_type]
    if search:
        q = search.lower()
        filtered = [r for r in filtered if q in r["name"].lower() or q in r["region"].lower()]

    return {
        "last_updated": now,
        "period_start": month_ago,
        "period_end": now,
        "filter_region": region_slug,
        "filter_status": status,
        "filter_compliance": compliance,
        "filter_type": structure_type,
        "filter_search": search,
        "kpis": {
            "active_pharmacies": active_now,
            "active_pharmacies_trend": _pct_change(active_now, active_prev),
            "pending_pharmacies": pending_now,
            "pending_pharmacies_trend": _pct_change(pending_now, pending_prev),
            "conformes": conformes,
            "conformes_pct": round((conformes / total_for_pct) * 100, 1),
            "non_conformes": conformity_counts["non_conforme"] + conformity_counts["inspection"],
            "non_conformes_pct": round(
                ((conformity_counts["non_conforme"] + conformity_counts["inspection"]) / total_for_pct)
                * 100,
                1,
            ),
            "suspended": suspended + conformity_counts["pending"],
            "suspended_pct": round(
                ((suspended + conformity_counts["pending"]) / total_for_pct) * 100, 1
            ),
        },
        "donut_slices": donut_slices,
        "donut_total": total_structures,
        "donut_gradient": donut_gradient,
        "type_bars": type_bars,
        "geo_rows": geo_rows,
        "table_rows": filtered[:50],
        "table_total": len(filtered),
        "key_indicators": [
            {
                "label": "Taux de conformité",
                "value": f"{round((conformes / total_for_pct) * 100, 1)}%",
                "icon": "verified",
            },
            {
                "label": "Taux de disponibilité stocks",
                "value": f"{stock_stats['availability_rate']}%",
                "icon": "inventory_2",
            },
            {
                "label": "Documents déposés (30 j)",
                "value": str(inspections),
                "icon": "fact_check",
            },
            {
                "label": "Non-conformités détectées",
                "value": str(sum(nc_counter.values())),
                "icon": "gpp_bad",
            },
        ],
        "nonconformities": nc_bars,
        "map_data": {
            "regions": map_regions,
            "geojson_url": "/static/geo/gabon-provinces.geojson",
            "map_mode": "trends",
        },
        "has_data": total_structures > 0,
        "status_choices": Pharmacy.Status.choices,
        "compliance_choices": [
            ("conforme", "Conforme"),
            ("inspection", "En inspection"),
            ("non_conforme", "Non-conforme"),
            ("suspended", "Suspendue"),
            ("pending", "En attente"),
        ],
        "type_choices": [
            ("private_pharmacy", "Pharmacie privée"),
            ("hospital_pharmacy", "Pharmacie hospitalière / 24h"),
            ("pending", "En attente d'agrément"),
        ],
    }


DELIVERY_ACTIVE_STATUSES = (
    Delivery.Status.PENDING,
    Delivery.Status.ASSIGNED,
    Delivery.Status.PICKING_UP,
    Delivery.Status.PICKED_UP,
    Delivery.Status.IN_TRANSIT,
)

DELIVERY_MODES = (Order.DeliveryMode.HOME, Order.DeliveryMode.EXPRESS)


def _delivery_duration_minutes(delivery: Delivery) -> int | None:
    order = delivery.order
    if delivery.delivered_at:
        start = delivery.picked_up_at or (order.created_at if order else None)
        if start:
            return max(int((delivery.delivered_at - start).total_seconds() / 60), 0)
    if delivery.estimated_minutes:
        return int(delivery.estimated_minutes)
    return None


def _delivery_table_bucket(status: str, order_status: str) -> tuple[str, str]:
    if status == Delivery.Status.DELIVERED:
        return "done", "Terminée"
    if status in DELIVERY_ACTIVE_STATUSES or order_status == Order.Status.DELIVERING:
        return "active", "En cours"
    if status == Delivery.Status.FAILED or order_status == Order.Status.CANCELLED:
        return "cancelled", "Annulée"
    return "pending", "En attente"


def authority_deliveries_dashboard(
    *,
    status: str = "",
    search: str = "",
) -> dict:
    """Suivi national des livraisons — données réelles Gab'Pharma."""
    from deliveries.models import DeliveryIncident

    now = timezone.now()
    month_ago = now - timedelta(days=30)
    prev_start = month_ago - timedelta(days=30)

    delivery_orders = Order.objects.exclude(status=Order.Status.CART).filter(
        delivery_mode__in=DELIVERY_MODES
    )
    deliveries_qs = Delivery.objects.select_related(
        "order", "order__client", "order__pharmacy", "courier"
    )

    period_orders = delivery_orders.filter(created_at__gte=month_ago)
    prev_orders = delivery_orders.filter(created_at__gte=prev_start, created_at__lt=month_ago)
    total_requests = period_orders.count()
    total_prev = prev_orders.count()

    period_deliveries = deliveries_qs.filter(created_at__gte=month_ago)
    prev_deliveries = deliveries_qs.filter(created_at__gte=prev_start, created_at__lt=month_ago)

    completed = period_deliveries.filter(status=Delivery.Status.DELIVERED).count()
    completed_prev = prev_deliveries.filter(status=Delivery.Status.DELIVERED).count()

    in_progress = deliveries_qs.filter(status__in=DELIVERY_ACTIVE_STATUSES).count()
    in_progress_prev = prev_deliveries.filter(status__in=DELIVERY_ACTIVE_STATUSES).count()

    cancelled = period_deliveries.filter(status=Delivery.Status.FAILED).count()
    cancelled += period_orders.filter(status=Order.Status.CANCELLED).count()
    cancelled_prev = prev_deliveries.filter(status=Delivery.Status.FAILED).count()
    cancelled_prev += prev_orders.filter(status=Order.Status.CANCELLED).count()

    delivered_period = period_deliveries.filter(status=Delivery.Status.DELIVERED)
    failed_period = period_deliveries.filter(status=Delivery.Status.FAILED).count()
    closed = completed + failed_period + period_orders.filter(status=Order.Status.CANCELLED).count()
    success_rate = round((completed / closed) * 100, 1) if closed else 0.0

    delivered_prev = prev_deliveries.filter(status=Delivery.Status.DELIVERED).count()
    failed_prev = prev_deliveries.filter(status=Delivery.Status.FAILED).count()
    closed_prev = delivered_prev + failed_prev + prev_orders.filter(status=Order.Status.CANCELLED).count()
    success_prev = round((delivered_prev / closed_prev) * 100, 1) if closed_prev else 0.0

    durations: list[int] = []
    for d in delivered_period.filter(delivered_at__isnull=False)[:300]:
        mins = _delivery_duration_minutes(d)
        if mins is not None:
            durations.append(mins)
    avg_delay = round(sum(durations) / len(durations)) if durations else None

    prev_durations: list[int] = []
    for d in prev_deliveries.filter(status=Delivery.Status.DELIVERED, delivered_at__isnull=False)[:300]:
        mins = _delivery_duration_minutes(d)
        if mins is not None:
            prev_durations.append(mins)
    avg_delay_prev = round(sum(prev_durations) / len(prev_durations)) if prev_durations else None
    delay_trend = 0
    if avg_delay is not None and avg_delay_prev:
        delay_trend = int(round(((avg_delay - avg_delay_prev) / avg_delay_prev) * 100))

    status_counts = Counter()
    for d in period_deliveries:
        bucket, _ = _delivery_table_bucket(d.status, d.order.status if d.order else "")
        status_counts[bucket] += 1
    donut_total = sum(status_counts.values()) or 1
    donut_slices = [
        {"label": "Terminées", "count": status_counts["done"], "color": "#228545"},
        {"label": "En cours", "count": status_counts["active"], "color": "#f59e0b"},
        {"label": "Annulées", "count": status_counts["cancelled"], "color": "#dc2626"},
        {"label": "En attente", "count": status_counts["pending"], "color": "#9ca3af"},
    ]
    donut_slices = [s for s in donut_slices if s["count"]]
    donut_slices, donut_gradient = _donut_from_counts(donut_slices, donut_total)

    delay_by_day: dict[date, list[int]] = defaultdict(list)
    for d in delivered_period.filter(delivered_at__gte=month_ago):
        mins = _delivery_duration_minutes(d)
        if mins is not None and d.delivered_at:
            delay_by_day[d.delivered_at.date()].append(mins)
    delay_series = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date()
        vals = delay_by_day.get(day, [])
        delay_series.append(
            {
                "label": day.strftime("%d/%m"),
                "value": round(sum(vals) / len(vals)) if vals else 0,
            }
        )
    delay_polyline = _evo_polyline([p["value"] for p in delay_series], width=320, height=72)

    zone_counts: Counter = Counter()
    for d in period_deliveries:
        city = ""
        if d.order and d.order.pharmacy:
            city = d.order.pharmacy.city or d.order.pharmacy.district or ""
        if not city and d.order:
            city = (d.order.delivery_address or "").split(",")[0][:40]
        zone_counts[city or "Non renseigné"] += 1
    zone_bars = []
    for label, count in zone_counts.most_common(8):
        zone_bars.append(
            {
                "label": label,
                "count": count,
                "pct": round((count / donut_total) * 100, 1),
                "bar_pct": int((count / max(zone_counts.values() or [1])) * 100),
            }
        )

    rows_raw = []
    for d in deliveries_qs.order_by("-updated_at")[:200]:
        order = d.order
        if not order:
            continue
        bucket, bucket_label = _delivery_table_bucket(d.status, order.status)
        client_name = order.client.get_full_name() or order.client.username
        rows_raw.append(
            {
                "code": order.code,
                "client": client_name,
                "pharmacy": order.pharmacy.name if order.pharmacy else "—",
                "address": order.delivery_address or (order.pharmacy.address if order.pharmacy else "—"),
                "status_bucket": bucket,
                "status_label": bucket_label,
                "updated_at": d.updated_at,
            }
        )

    filtered = rows_raw
    if status:
        filtered = [r for r in filtered if r["status_bucket"] == status]
    if search:
        q = search.lower()
        filtered = [
            r
            for r in filtered
            if q in r["code"].lower()
            or q in r["client"].lower()
            or q in r["pharmacy"].lower()
            or q in r["address"].lower()
        ]

    map_markers = []
    for d in deliveries_qs.filter(status__in=DELIVERY_ACTIVE_STATUSES + (Delivery.Status.DELIVERED,))[:40]:
        order = d.order
        lat = d.courier_lat
        lng = d.courier_lng
        if (not lat or not lng) and order:
            lat = order.delivery_latitude
            lng = order.delivery_longitude
        if (not lat or not lng) and order and order.pharmacy:
            lat = order.pharmacy.latitude
            lng = order.pharmacy.longitude
        if not lat or not lng:
            continue
        bucket, label = _delivery_table_bucket(d.status, order.status if order else "")
        map_markers.append(
            {
                "lat": float(lat),
                "lng": float(lng),
                "status": bucket,
                "label": f"{order.code} — {label}" if order else label,
            }
        )
    for ph in Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE, latitude__isnull=False)[:15]:
        map_markers.append(
            {
                "lat": float(ph.latitude),
                "lng": float(ph.longitude),
                "status": "pharmacy",
                "label": ph.name,
            }
        )

    delivery_alerts = []
    for inc in (
        DeliveryIncident.objects.filter(
            status__in=[DeliveryIncident.Status.OPEN, DeliveryIncident.Status.IN_PROGRESS]
        )
        .select_related("delivery", "delivery__order")
        .order_by("-created_at")[:6]
    ):
        delivery_alerts.append(
            {
                "title": inc.get_incident_type_display(),
                "detail": (inc.description or inc.delivery.order.code)[:80],
                "level": "high" if inc.priority in {DeliveryIncident.Priority.HIGH, DeliveryIncident.Priority.URGENT} else "medium",
                "time_ago": inc.created_at,
            }
        )

    center_lat, center_lng = 0.4162, 9.4673
    if map_markers:
        center_lat = sum(m["lat"] for m in map_markers) / len(map_markers)
        center_lng = sum(m["lng"] for m in map_markers) / len(map_markers)

    return {
        "last_updated": now,
        "period_start": month_ago,
        "period_end": now,
        "kpis": {
            "total_requests": total_requests,
            "total_requests_trend": _pct_change(total_requests, total_prev),
            "completed": completed,
            "completed_trend": _pct_change(completed, completed_prev),
            "in_progress": in_progress,
            "in_progress_trend": _pct_change(in_progress, in_progress_prev),
            "success_rate": success_rate,
            "success_rate_trend": int(round(success_rate - success_prev)),
            "cancelled": cancelled,
            "cancelled_trend": _pct_change(cancelled, cancelled_prev),
            "avg_delay_minutes": avg_delay,
            "avg_delay_trend": delay_trend,
        },
        "donut_slices": donut_slices,
        "donut_total": sum(status_counts.values()),
        "donut_gradient": donut_gradient,
        "delay_series": delay_series,
        "delay_polyline": delay_polyline,
        "zone_bars": zone_bars,
        "table_rows": filtered[:30],
        "table_total": len(filtered),
        "delivery_alerts": delivery_alerts,
        "has_data": total_requests > 0 or deliveries_qs.exists(),
        "status_choices": [
            ("active", "En cours"),
            ("done", "Terminée"),
            ("cancelled", "Annulée"),
            ("pending", "En attente"),
        ],
        "map_data": {
            "center": {"lat": center_lat, "lng": center_lng},
            "zoom": 11,
            "markers": map_markers,
            "map_mode": "deliveries",
        },
    }


def _ticket_priority(ticket: SupportTicket) -> tuple[str, str]:
    text = f"{ticket.subject} {ticket.description}".lower()
    if any(k in text for k in ("critique", "urgent", "danger", "sos", "grave")):
        return "critical", "Critique"
    if ticket.category in {SupportTicket.Category.DELAY, SupportTicket.Category.DAMAGED}:
        return "high", "Élevée"
    if ticket.category in {SupportTicket.Category.MISSING_ITEM, SupportTicket.Category.PAYMENT}:
        return "medium", "Modérée"
    return "low", "Faible"


def _ticket_status_bucket(status: str) -> tuple[str, str]:
    mapping = {
        SupportTicket.Status.IN_PROGRESS: ("active", "En cours"),
        SupportTicket.Status.PENDING: ("pending", "En attente"),
        SupportTicket.Status.RESOLVED: ("resolved", "Résolu"),
        SupportTicket.Status.CLOSED: ("rejected", "Rejeté"),
    }
    return mapping.get(status, ("pending", "En attente"))


def authority_disputes_dashboard(
    *,
    status: str = "",
    priority: str = "",
    search: str = "",
) -> dict:
    """Centre des litiges & réclamations — données réelles SupportTicket."""
    now = timezone.now()
    month_ago = now - timedelta(days=30)
    prev_start = month_ago - timedelta(days=30)

    tickets_qs = SupportTicket.objects.select_related("client", "order", "order__pharmacy")

    total = tickets_qs.count()
    total_prev = tickets_qs.filter(created_at__lt=month_ago).count()

    in_progress = tickets_qs.filter(status=SupportTicket.Status.IN_PROGRESS).count()
    pending = tickets_qs.filter(status=SupportTicket.Status.PENDING).count()
    resolved = tickets_qs.filter(status=SupportTicket.Status.RESOLVED).count()
    rejected = tickets_qs.filter(status=SupportTicket.Status.CLOSED).count()
    total_for_pct = total or 1

    resolved_prev = tickets_qs.filter(
        status=SupportTicket.Status.RESOLVED, resolved_at__lt=month_ago
    ).count()
    rejected_prev = tickets_qs.filter(
        status=SupportTicket.Status.CLOSED, updated_at__lt=month_ago
    ).count()

    donut_slices = [
        {"label": "En cours", "count": in_progress, "color": "#f59e0b"},
        {"label": "En attente", "count": pending, "color": "#8b5cf6"},
        {"label": "Résolus", "count": resolved, "color": "#228545"},
        {"label": "Rejetés", "count": rejected, "color": "#dc2626"},
    ]
    donut_slices = [s for s in donut_slices if s["count"]]
    donut_slices, donut_gradient = _donut_from_counts(donut_slices, total_for_pct)

    opened_series: list[int] = []
    resolved_series: list[int] = []
    rejected_series: list[int] = []
    evolution_labels: list[str] = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date()
        day_start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
        day_end = day_start + timedelta(days=1)
        evolution_labels.append(day.strftime("%d/%m"))
        opened_series.append(
            tickets_qs.filter(created_at__gte=day_start, created_at__lt=day_end).count()
        )
        resolved_series.append(
            tickets_qs.filter(resolved_at__gte=day_start, resolved_at__lt=day_end).count()
        )
        rejected_series.append(
            tickets_qs.filter(
                status=SupportTicket.Status.CLOSED,
                resolved_at__isnull=True,
                updated_at__gte=day_start,
                updated_at__lt=day_end,
            ).count()
        )

    evolution_lines = {
        "opened": _evo_polyline(opened_series, width=300, height=64),
        "resolved": _evo_polyline(resolved_series, width=300, height=64),
        "rejected": _evo_polyline(rejected_series, width=300, height=64),
    }

    priority_tickets = []
    for ticket in tickets_qs.exclude(
        status__in=[SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED]
    ).order_by("-updated_at")[:8]:
        prio_key, prio_label = _ticket_priority(ticket)
        pharmacy_name = ticket.order.pharmacy.name if ticket.order and ticket.order.pharmacy else "—"
        priority_tickets.append(
            {
                "code": ticket.code,
                "title": ticket.subject,
                "pharmacy": pharmacy_name,
                "priority": prio_key,
                "priority_label": prio_label,
            }
        )
    priority_tickets.sort(
        key=lambda t: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(t["priority"], 4)
    )

    rows_raw = []
    for ticket in tickets_qs.order_by("-created_at")[:200]:
        bucket, bucket_label = _ticket_status_bucket(ticket.status)
        prio_key, prio_label = _ticket_priority(ticket)
        client_name = ticket.client.get_full_name() or ticket.client.username
        pharmacy_name = ticket.order.pharmacy.name if ticket.order and ticket.order.pharmacy else "—"
        rows_raw.append(
            {
                "code": ticket.code,
                "type_label": ticket.get_category_display(),
                "client": client_name,
                "pharmacy": pharmacy_name,
                "status_bucket": bucket,
                "status_label": bucket_label,
                "priority": prio_key,
                "priority_label": prio_label,
                "created_at": ticket.created_at,
            }
        )

    filtered = rows_raw
    if status:
        filtered = [r for r in filtered if r["status_bucket"] == status]
    if priority:
        filtered = [r for r in filtered if r["priority"] == priority]
    if search:
        q = search.lower()
        filtered = [
            r
            for r in filtered
            if q in r["code"].lower()
            or q in r["client"].lower()
            or q in r["pharmacy"].lower()
            or q in r["type_label"].lower()
        ]

    category_counts: Counter = Counter()
    for ticket in tickets_qs:
        category_counts[ticket.get_category_display()] += 1
    cat_total = sum(category_counts.values()) or 1
    category_bars = [
        {
            "label": label,
            "count": count,
            "pct": round((count / cat_total) * 100, 1),
            "bar_pct": int((count / max(category_counts.values() or [1])) * 100),
        }
        for label, count in category_counts.most_common(8)
    ]

    zone_counts: Counter = Counter()
    for ticket in tickets_qs:
        city = ""
        if ticket.order and ticket.order.pharmacy:
            city = ticket.order.pharmacy.city or ticket.order.pharmacy.region or ""
        if not city and ticket.order:
            city = (ticket.order.delivery_address or "").split(",")[0][:40]
        zone_counts[city or "Non renseigné"] += 1
    zone_bars = [
        {
            "label": label,
            "count": count,
            "pct": round((count / cat_total) * 100, 1),
            "bar_pct": int((count / max(zone_counts.values() or [1])) * 100),
        }
        for label, count in zone_counts.most_common(8)
    ]

    region_counts: Counter = Counter()
    for ticket in tickets_qs:
        region_name = ""
        if ticket.order and ticket.order.pharmacy:
            region_name = normalize_region_name(
                ticket.order.pharmacy.region or ticket.order.pharmacy.city or ""
            )
        if region_name:
            region_counts[region_name] += 1
    map_regions = []
    geo_rows = []
    total_geo = sum(region_counts.values()) or 1
    for prov in GABON_PROVINCES:
        count = region_counts.get(prov["name"], 0)
        density = round((count / max(prov["population"], 1)) * 100_000, 1)
        colors = incidence_color(density)
        geo_rows.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "count": count,
                "pct": round((count / total_geo) * 100, 1),
            }
        )
        map_regions.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "lat": prov["lat"],
                "lng": prov["lng"],
                "incidence_rate": density,
                "availability_rate": count,
                "fill_color": colors["fill"],
                "border_color": colors["border"],
            }
        )
    geo_rows.sort(key=lambda r: -r["count"])

    resolved_tickets = tickets_qs.filter(status=SupportTicket.Status.RESOLVED)
    resolution_days: list[float] = []
    for ticket in resolved_tickets.filter(resolved_at__isnull=False)[:400]:
        if ticket.resolved_at and ticket.created_at:
            resolution_days.append((ticket.resolved_at - ticket.created_at).total_seconds() / 86400)
    avg_resolution_days = round(sum(resolution_days) / len(resolution_days), 1) if resolution_days else None

    closed_count = resolved + rejected
    resolution_rate = round((resolved / closed_count) * 100, 1) if closed_count else 0.0

    evals = OrderEvaluation.objects.filter(
        order__support_tickets__status=SupportTicket.Status.RESOLVED
    ).distinct()
    satisfaction_scores = []
    for ev in evals[:500]:
        if ev.pharmacy_rating:
            satisfaction_scores.append(ev.pharmacy_rating)
        if ev.courier_rating:
            satisfaction_scores.append(ev.courier_rating)
    satisfaction_rate = (
        round((sum(1 for s in satisfaction_scores if s >= 4) / len(satisfaction_scores)) * 100, 1)
        if satisfaction_scores
        else None
    )

    recurrent_clients = (
        tickets_qs.values("client_id")
        .annotate(c=Count("id"))
        .filter(c__gte=2)
        .count()
    )

    satisfaction_slices = Counter()
    for score in satisfaction_scores:
        if score >= 5:
            satisfaction_slices["Très satisfait"] += 1
        elif score == 4:
            satisfaction_slices["Satisfait"] += 1
        elif score == 3:
            satisfaction_slices["Neutre"] += 1
        else:
            satisfaction_slices["Insatisfait"] += 1
    sat_total = sum(satisfaction_slices.values()) or 1
    sat_colors = {
        "Très satisfait": "#15803d",
        "Satisfait": "#4ade80",
        "Neutre": "#f59e0b",
        "Insatisfait": "#dc2626",
    }
    satisfaction_donut = [
        {"label": label, "count": satisfaction_slices[label], "color": sat_colors[label]}
        for label in ["Très satisfait", "Satisfait", "Neutre", "Insatisfait"]
        if satisfaction_slices[label]
    ]
    satisfaction_donut, satisfaction_gradient = _donut_from_counts(satisfaction_donut, sat_total)

    return {
        "last_updated": now,
        "period_start": month_ago,
        "period_end": now,
        "kpis": {
            "total": total,
            "total_trend": _pct_change(total, total_prev),
            "in_progress": in_progress,
            "in_progress_pct": round((in_progress / total_for_pct) * 100, 1),
            "pending": pending,
            "pending_pct": round((pending / total_for_pct) * 100, 1),
            "resolved": resolved,
            "resolved_trend": _pct_change(resolved, resolved_prev),
            "rejected": rejected,
            "rejected_trend": _pct_change(rejected, rejected_prev),
        },
        "donut_slices": donut_slices,
        "donut_total": total,
        "donut_gradient": donut_gradient,
        "evolution_labels": evolution_labels,
        "evolution_lines": evolution_lines,
        "priority_tickets": priority_tickets[:6],
        "table_rows": filtered[:40],
        "table_total": len(filtered),
        "key_indicators": [
            {
                "label": "Taux de résolution",
                "value": f"{resolution_rate}%",
                "icon": "task_alt",
            },
            {
                "label": "Délai moyen de résolution",
                "value": f"{avg_resolution_days} jours" if avg_resolution_days is not None else "—",
                "icon": "schedule",
            },
            {
                "label": "Satisfaction clients",
                "value": f"{satisfaction_rate}%" if satisfaction_rate is not None else "—",
                "icon": "sentiment_satisfied",
            },
            {
                "label": "Litiges récurrents",
                "value": str(recurrent_clients),
                "icon": "replay",
            },
        ],
        "category_bars": category_bars,
        "zone_bars": zone_bars,
        "geo_rows": geo_rows,
        "satisfaction_donut": satisfaction_donut,
        "satisfaction_total": sum(satisfaction_slices.values()),
        "satisfaction_gradient": satisfaction_gradient,
        "has_data": total > 0,
        "status_choices": [
            ("active", "En cours"),
            ("pending", "En attente"),
            ("resolved", "Résolu"),
            ("rejected", "Rejeté"),
        ],
        "priority_choices": [
            ("critical", "Critique"),
            ("high", "Élevée"),
            ("medium", "Modérée"),
            ("low", "Faible"),
        ],
        "map_data": {
            "regions": map_regions,
            "geojson_url": "/static/geo/gabon-provinces.geojson",
            "map_mode": "trends",
        },
    }


CAMPAIGN_THEME_COLORS = {
    "vaccination": "#2563eb",
    "malaria": "#228545",
    "maternal": "#ec4899",
    "cholera": "#06b6d4",
    "hiv": "#dc2626",
    "ncd": "#8b5cf6",
    "nutrition": "#f59e0b",
    "tobacco": "#6b7280",
    "other": "#9ca3af",
}

CAMPAIGN_THEME_ICONS = {
    "vaccination": "vaccines",
    "malaria": "coronavirus",
    "maternal": "pregnant_woman",
    "cholera": "water_drop",
    "hiv": "science",
    "ncd": "cardiology",
    "nutrition": "restaurant",
    "tobacco": "smoke_free",
    "other": "campaign",
}


def _campaign_pharmacy_count(campaign) -> int:
    if campaign.pharmacies_involved:
        return campaign.pharmacies_involved
    qs = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
    slugs = campaign.region_slugs or []
    if slugs:
        names = [p["name"] for p in GABON_PROVINCES if p["slug"] in slugs]
        if names:
            q = Q()
            for name in names:
                q |= Q(region__icontains=name) | Q(city__icontains=name)
            qs = qs.filter(q)
    return qs.count()


def authority_campaigns_dashboard(
    *,
    theme: str = "",
    status: str = "",
    region: str = "",
) -> dict:
    """Pilotage campagnes santé publique — modèle HealthCampaign (contexte Gabon / OMS PEV)."""
    from core.models import HealthCampaign

    now = timezone.now()
    today = now.date()
    month_ago = now - timedelta(days=30)
    region_slug = _region_slug_filter(region)

    campaigns_qs = HealthCampaign.objects.select_related("created_by")
    active_qs = campaigns_qs.filter(status=HealthCampaign.Status.ACTIVE)
    active_now = [c for c in active_qs if c.is_active_now]

    total_target = sum(c.target_population for c in campaigns_qs)
    total_reached = sum(c.people_reached for c in campaigns_qs)
    coverage = round((total_reached / total_target) * 100, 1) if total_target else 0.0

    pharmacies_mobilized = sum(_campaign_pharmacy_count(c) for c in active_qs) or (
        Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).count()
        if active_qs.exists()
        else 0
    )

    planned = campaigns_qs.filter(status=HealthCampaign.Status.PLANNED).count()
    completed = campaigns_qs.filter(status=HealthCampaign.Status.COMPLETED).count()

    prev_active = campaigns_qs.filter(
        status=HealthCampaign.Status.ACTIVE, created_at__lt=month_ago
    ).count()

    theme_counts: Counter = Counter()
    for camp in campaigns_qs:
        theme_counts[camp.theme] += 1
    theme_total = sum(theme_counts.values()) or 1
    theme_donut = []
    for theme_key, count in theme_counts.most_common():
        theme_donut.append(
            {
                "label": dict(HealthCampaign.Theme.choices).get(theme_key, theme_key),
                "count": count,
                "color": CAMPAIGN_THEME_COLORS.get(theme_key, "#9ca3af"),
            }
        )
    theme_donut, theme_gradient = _donut_from_counts(theme_donut, theme_total)

    status_counts: Counter = Counter()
    for camp in campaigns_qs:
        status_counts[camp.status] += 1

    timeline = []
    for camp in campaigns_qs.order_by("-start_date")[:12]:
        timeline.append(
            {
                "code": camp.code,
                "title": camp.title,
                "theme_label": camp.get_theme_display(),
                "theme": camp.theme,
                "status": camp.status,
                "status_label": camp.get_status_display(),
                "start": camp.start_date,
                "end": camp.end_date,
                "coverage_pct": camp.coverage_pct,
                "partner": camp.partner or "MSNS",
            }
        )

    region_reach: Counter = Counter()
    for camp in campaigns_qs:
        if camp.region_slugs:
            for slug in camp.region_slugs:
                region_reach[slug] += camp.people_reached
        else:
            region_reach["national"] += camp.people_reached

    map_regions = []
    geo_rows = []
    max_reach = max(region_reach.values()) if region_reach else 1
    for prov in GABON_PROVINCES:
        reach = region_reach.get(prov["slug"], 0)
        density = round((reach / max(max_reach, 1)) * 100, 1)
        colors = incidence_color(density)
        geo_rows.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "count": reach,
                "pct": density,
            }
        )
        map_regions.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "lat": prov["lat"],
                "lng": prov["lng"],
                "incidence_rate": density,
                "availability_rate": density,
                "fill_color": colors["fill"],
                "border_color": colors["border"],
            }
        )
    geo_rows.sort(key=lambda r: -r["count"])

    launched_series: list[int] = []
    for i in range(11, -1, -1):
        start = (now - timedelta(days=(i + 1) * 30)).date()
        end = (now - timedelta(days=i * 30)).date()
        launched_series.append(campaigns_qs.filter(start_date__gte=start, start_date__lte=end).count())
    launch_polyline = _evo_polyline(launched_series, width=280, height=56)

    rows_raw = []
    for camp in campaigns_qs.order_by("-start_date"):
        if region_slug and camp.region_slugs and region_slug not in camp.region_slugs:
            continue
        region_labels = [
            p["name"] for p in GABON_PROVINCES if p["slug"] in (camp.region_slugs or [])
        ]
        rows_raw.append(
            {
                "code": camp.code,
                "title": camp.title,
                "theme": camp.theme,
                "theme_label": camp.get_theme_display(),
                "status": camp.status,
                "status_label": camp.get_status_display(),
                "partner": camp.partner or "—",
                "regions": ", ".join(region_labels) if region_labels else "National",
                "start_date": camp.start_date,
                "end_date": camp.end_date,
                "target": camp.target_population,
                "reached": camp.people_reached,
                "coverage_pct": camp.coverage_pct,
                "pharmacies": _campaign_pharmacy_count(camp),
                "icon": CAMPAIGN_THEME_ICONS.get(camp.theme, "campaign"),
            }
        )

    filtered = rows_raw
    if theme:
        filtered = [r for r in filtered if r["theme"] == theme]
    if status:
        filtered = [r for r in filtered if r["status"] == status]

    doses_target = sum(c.doses_target for c in campaigns_qs)
    doses_done = sum(c.doses_administered for c in campaigns_qs)
    dose_coverage = round((doses_done / doses_target) * 100, 1) if doses_target else None

    benchmarks = [
        {
            "org": "OMS / PEV",
            "label": "Programme élargi de vaccination — couverture cible ≥ 90 %",
        },
        {
            "org": "OMS AFRO",
            "label": "Initiative « High burden to high impact » — paludisme Gabon",
        },
        {
            "org": "DHIS2",
            "label": "Suivi indicateurs campagne (population, doses, points de distribution)",
        },
        {
            "org": "MSNS Gabon",
            "label": "Plan national santé 2024–2028 — prévention & promotion",
        },
    ]

    return {
        "last_updated": now,
        "period_start": month_ago,
        "period_end": now,
        "benchmarks": benchmarks,
        "kpis": {
            "active": len(active_now),
            "active_trend": _pct_change(len(active_now), prev_active),
            "planned": planned,
            "completed": completed,
            "target_population": total_target,
            "people_reached": total_reached,
            "coverage_pct": coverage,
            "pharmacies_mobilized": pharmacies_mobilized,
            "doses_administered": doses_done,
            "dose_coverage_pct": dose_coverage,
        },
        "theme_donut": theme_donut,
        "theme_gradient": theme_gradient,
        "theme_total": theme_total,
        "timeline": timeline,
        "launch_polyline": launch_polyline,
        "table_rows": filtered[:20],
        "table_total": len(filtered),
        "geo_rows": geo_rows,
        "has_data": campaigns_qs.exists(),
        "map_data": {
            "regions": map_regions,
            "geojson_url": "/static/geo/gabon-provinces.geojson",
            "map_mode": "trends",
        },
    }


WHO_ESSENTIAL_MEDICINES_TARGET = 80.0
WHO_PHARMACY_DENSITY_TARGET = 2.5


def authority_reports_dashboard(
    *,
    period_days: int = 30,
    report_type: str = "overview",
    region: str = "",
) -> dict:
    """Rapports & indicateurs — cadre OMS / ODD 3 / DHIS2."""
    from core.authority_analytics import national_stock_stats
    from core.models import HealthCampaign

    now = timezone.now()
    since = now - timedelta(days=period_days)
    prev_since = since - timedelta(days=period_days)
    region_slug = _region_slug_filter(region)

    stock = national_stock_stats()
    availability = stock["availability_rate"]
    ruptures = stock["out"]

    orders_qs = _orders_qs(since, now, region_slug=region_slug)
    orders_prev = _orders_qs(prev_since, since, region_slug=region_slug)
    orders_count = orders_qs.count()
    delivered = orders_qs.filter(status=Order.Status.DELIVERED).count()
    delivery_rate = round((delivered / orders_count) * 100, 1) if orders_count else 0.0

    pharmacies = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
    if region_slug:
        prov_name = next((p["name"] for p in GABON_PROVINCES if p["slug"] == region_slug), "")
        if prov_name:
            pharmacies = pharmacies.filter(Q(region__icontains=prov_name) | Q(city__icontains=prov_name))
    pharmacy_count = pharmacies.count()
    conformes = pharmacies.filter(compliance_score__gte=70).count()
    compliance_rate = round((conformes / pharmacy_count) * 100, 1) if pharmacy_count else 0.0
    patients_reached = orders_qs.values("client_id").distinct().count()
    pop_total = sum(p["population"] for p in GABON_PROVINCES) or 1
    density = round((pharmacy_count / pop_total) * 100_000, 2)

    region_table = []
    for prov in GABON_PROVINCES:
        if region_slug and prov["slug"] != region_slug:
            continue
        ph_n = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).filter(
            Q(region__icontains=prov["name"]) | Q(city__icontains=prov["name"])
        ).count()
        reg_orders = _orders_qs(since, now, region_slug=prov["slug"]).count()
        reg_density = round((ph_n / max(prov["population"], 1)) * 100_000, 2)
        region_table.append(
            {
                "name": prov["name"],
                "slug": prov["slug"],
                "pharmacies": ph_n,
                "density": reg_density,
                "orders": reg_orders,
                "meets_density": reg_density >= WHO_PHARMACY_DENSITY_TARGET,
            }
        )
    region_table.sort(key=lambda r: -r["orders"])

    frameworks = [
        {
            "framework": "OMS",
            "pillar": "Produits médicaux",
            "indicator": "Disponibilité médicaments essentiels",
            "code": "WHO-EML-AVAIL",
            "value": f"{availability}%",
            "target": f"≥ {WHO_ESSENTIAL_MEDICINES_TARGET:.0f}%",
            "status": "ok" if availability >= WHO_ESSENTIAL_MEDICINES_TARGET else "warn",
        },
        {
            "framework": "ODD 3",
            "pillar": "ODD 3.8 — Couverture sanitaire",
            "indicator": "Accès pharmacies (proxy UHC)",
            "code": "SDG-3.8",
            "value": f"{density} /100k hab.",
            "target": f"≥ {WHO_PHARMACY_DENSITY_TARGET} /100k",
            "status": "ok" if density >= WHO_PHARMACY_DENSITY_TARGET else "warn",
        },
        {
            "framework": "ODD 3",
            "pillar": "ODD 3.b — Médicaments essentiels",
            "indicator": "Ruptures de stock",
            "code": "SDG-3.b",
            "value": str(ruptures),
            "target": "0 rupture",
            "status": "ok" if ruptures == 0 else ("warn" if ruptures < 10 else "critical"),
        },
        {
            "framework": "DHIS2",
            "pillar": "Qualité",
            "indicator": "Pharmacies conformes (≥ 70 %)",
            "code": "DHIS-QA-PH",
            "value": f"{compliance_rate}%",
            "target": "≥ 85 %",
            "status": "ok" if compliance_rate >= 85 else "warn",
        },
        {
            "framework": "DHIS2",
            "pillar": "Services",
            "indicator": "Commandes livrées",
            "code": "DHIS-SRV-ORD",
            "value": f"{delivery_rate}%",
            "target": "≥ 90 %",
            "status": "ok" if delivery_rate >= 90 else "warn",
        },
        {
            "framework": "DHIS2",
            "pillar": "Population",
            "indicator": "Patients actifs (période)",
            "code": "DHIS-POP-ACT",
            "value": str(patients_reached),
            "target": "—",
            "status": "neutral",
        },
    ]

    dhis_dimensions = [
        {"label": "Disponibilité stocks", "value": availability, "unit": "%", "bar_pct": availability},
        {"label": "Conformité réseau", "value": compliance_rate, "unit": "%", "bar_pct": compliance_rate},
        {"label": "Taux de livraison", "value": delivery_rate, "unit": "%", "bar_pct": delivery_rate},
        {
            "label": "Densité pharmaceutique",
            "value": density,
            "unit": "/100k",
            "bar_pct": min(100, int((density / WHO_PHARMACY_DENSITY_TARGET) * 100)),
        },
    ]

    top_meds = list(
        OrderItem.objects.filter(order__created_at__gte=since)
        .exclude(order__status=Order.Status.CART)
        .values("medicine__name")
        .annotate(qty=Sum("quantity"))
        .order_by("-qty")[:8]
    )
    max_qty = max((m["qty"] or 0 for m in top_meds), default=1) or 1
    for m in top_meds:
        m["pct"] = int(((m["qty"] or 0) / max_qty) * 100)
        m["name"] = m["medicine__name"] or "—"

    campaigns = HealthCampaign.objects.filter(status=HealthCampaign.Status.ACTIVE)
    campaign_coverage = 0.0
    if campaigns.exists():
        targets = sum(c.target_population for c in campaigns)
        reached = sum(c.people_reached for c in campaigns)
        campaign_coverage = round((reached / targets) * 100, 1) if targets else 0.0

    return {
        "last_updated": now,
        "period_days": period_days,
        "report_type": report_type,
        "filter_region": region_slug,
        "frameworks": frameworks,
        "dhis_dimensions": dhis_dimensions,
        "kpis": {
            "orders": orders_count,
            "orders_trend": _pct_change(orders_count, orders_prev.count()),
            "availability": availability,
            "ruptures": ruptures,
            "low_stock": stock["low"],
            "compliance_rate": compliance_rate,
            "delivery_rate": delivery_rate,
            "pharmacies": pharmacy_count,
            "patients": patients_reached,
            "campaign_coverage": campaign_coverage,
        },
        "region_table": region_table,
        "top_meds": top_meds,
        "standards_note": (
            "Indicateurs calculés sur Gab'Pharma. Cibles OMS/ODD = référentiels internationaux."
        ),
    }


def authority_decision_dashboard() -> dict:
    """Centre de décision — scénarios stratégiques (données réelles)."""
    from core.authority_analytics import authority_alerts_dashboard, national_stock_stats
    from core.models import HealthCampaign

    now = timezone.now()
    stock = national_stock_stats()
    alerts = authority_alerts_dashboard()
    scenarios: list[dict] = []

    def add_scenario(**kwargs):
        scenarios.append(kwargs)

    availability = stock["availability_rate"]
    if availability < WHO_ESSENTIAL_MEDICINES_TARGET:
        add_scenario(
            key="stock",
            title="Risque rupture médicaments essentiels",
            severity="critical" if availability < 60 else "high",
            evidence=(
                f"Disponibilité {availability}% (cible OMS ≥ {WHO_ESSENTIAL_MEDICINES_TARGET:.0f}%). "
                f"{stock['out']} rupture(s), {stock['low']} stock(s) faible(s)."
            ),
            recommendation="Plan de redistribution inter-pharmacies et réapprovisionnement prioritaire.",
            action_url="bo_authority_stocks",
            impact=95,
            urgency=90 if availability < 60 else 70,
        )

    underserved = []
    for prov in GABON_PROVINCES:
        ph_n = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).filter(
            Q(region__icontains=prov["name"]) | Q(city__icontains=prov["name"])
        ).count()
        d = (ph_n / max(prov["population"], 1)) * 100_000
        if d < WHO_PHARMACY_DENSITY_TARGET:
            underserved.append(f"{prov['name']} ({d:.1f}/100k)")
    if underserved:
        add_scenario(
            key="equity",
            title="Inégalités d'accès pharmaceutique",
            severity="high",
            evidence=f"{len(underserved)} province(s) sous seuil OMS : {', '.join(underserved[:4])}",
            recommendation="Renforcer couverture : nouvelles pharmacies ou livraison étendue.",
            action_url="bo_authority_map",
            impact=85,
            urgency=60,
        )

    critical_ph = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE, compliance_score__lt=50).count()
    if critical_ph:
        add_scenario(
            key="compliance",
            title="Non-conformité réglementaire",
            severity="high",
            evidence=f"{critical_ph} pharmacie(s) avec score sous 50 %.",
            recommendation="Inspections ciblées et mesures correctives.",
            action_url="bo_authority_pharmacies",
            impact=80,
            urgency=75,
        )

    if alerts["kpis"]["high"]:
        add_scenario(
            key="alerts",
            title="Alertes sanitaires élevées",
            severity="critical" if alerts["kpis"]["high"] >= 3 else "high",
            evidence=f"{alerts['kpis']['high']} alerte(s) de niveau élevé.",
            recommendation="Activer la cellule de crise régionale.",
            action_url="bo_authority_alerts",
            impact=90,
            urgency=85,
        )

    for camp in HealthCampaign.objects.filter(status=HealthCampaign.Status.ACTIVE):
        days_left = (camp.end_date - now.date()).days
        if camp.target_population and camp.coverage_pct < 40 and days_left <= 21:
            add_scenario(
                key=f"camp_{camp.pk}",
                title=f"Campagne à risque : {camp.title}",
                severity="medium",
                evidence=f"Couverture {camp.coverage_pct}% — {days_left} j. restants.",
                recommendation="Mobiliser pharmacies et communication terrain.",
                action_url="bo_authority_campaigns",
                impact=70,
                urgency=65,
            )

    pending = Delivery.objects.filter(status__in=DELIVERY_ACTIVE_STATUSES).count()
    if pending >= 5:
        add_scenario(
            key="logistics",
            title="Goulot logistique livraisons",
            severity="medium",
            evidence=f"{pending} livraison(s) en cours ou en attente.",
            recommendation="Renforcer capacité livreurs zones à forte demande.",
            action_url="bo_authority_deliveries",
            impact=65,
            urgency=55,
        )

    scenarios.sort(key=lambda s: (-s["urgency"], -s["impact"]))
    severity_counts = Counter(s["severity"] for s in scenarios)

    from django.urls import reverse
    from urllib.parse import urlencode

    action_params = {
        "stock": {"focus": "essential"},
        "compliance": {"compliance": "low"},
        "alerts": {"level": "high", "action": "crisis"},
        "logistics": {"status": "active"},
    }
    action_labels = {
        "stock": "Voir les stocks →",
        "equity": "Ouvrir la carte →",
        "compliance": "Inspecter les pharmacies →",
        "alerts": "Traiter les alertes →",
        "logistics": "Piloter les livraisons →",
    }
    for s in scenarios:
        params = action_params.get(s.get("key"), {})
        if s.get("key", "").startswith("camp_"):
            params = {"status": "active"}
            action_labels.setdefault(s["key"], "Piloter la campagne →")
        base = reverse(s["action_url"])
        s["action_href"] = base + (f"?{urlencode(params)}" if params else "")
        s["action_label"] = action_labels.get(s.get("key"), "Agir →")

    return {
        "last_updated": now,
        "scenarios": scenarios[:12],
        "scenario_total": len(scenarios),
        "severity_counts": dict(severity_counts),
        "summary": {
            "critical": severity_counts.get("critical", 0),
            "high": severity_counts.get("high", 0),
            "medium": severity_counts.get("medium", 0),
            "stock_availability": stock["availability_rate"],
            "active_pharmacies": Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).count(),
            "open_alerts": alerts["kpis"]["active"],
        },
        "methodology": (
            "Scénarios dérivés des seuils OMS/ODD et des données temps réel Gab'Pharma."
        ),
    }
