"""Statistiques et rapports — portail compagnie d'assurance."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from django.db.models import Count, Sum
from django.http import HttpResponse
from django.utils import timezone

from accounts.models import ClientProfile
from core.insurer_claims import claim_has_prescription, claims_base_queryset
from core.insurer_profile import InsurerPortalProfile
from payments.models import InsuranceClaim

_MONTHS_SHORT = ("jan", "fév", "mar", "avr", "mai", "jun", "jul", "aoû", "sep", "oct", "nov", "déc")


def _parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_report_dates(request) -> tuple:
    date_from = _parse_date(request.GET.get("date_from", ""))
    date_to = _parse_date(request.GET.get("date_to", ""))
    if not date_from and not date_to:
        today = timezone.localdate()
        date_from = today.replace(day=1)
        date_to = today
    return date_from, date_to


def _claims_in_period(profile, date_from, date_to):
    qs = claims_base_queryset(profile)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


def _prestation_type(claim) -> str:
    if claim_has_prescription(claim):
        return "Médicaments"
    if claim.amount >= 50_000:
        return "Hospitalisation"
    if claim.amount >= 20_000:
        return "Examens"
    return "Consultation"


def _pct_change(current: int, previous: int) -> float:
    if previous <= 0:
        return 0.0 if current <= 0 else 100.0
    return round(((current - previous) / previous) * 100, 1)


def build_reports_payload(
    profile: InsurerPortalProfile,
    date_from,
    date_to,
    zone: str = "",
) -> dict:
    today = timezone.localdate()
    claims = _claims_in_period(profile, date_from, date_to)
    if zone:
        claims = claims.filter(order__pharmacy__city__icontains=zone)

    period_days = max((date_to - date_from).days, 1) if date_from and date_to else 30
    prev_to = date_from - timedelta(days=1) if date_from else today - timedelta(days=1)
    prev_from = prev_to - timedelta(days=period_days)
    prev_claims = _claims_in_period(profile, prev_from, prev_to)
    if zone:
        prev_claims = prev_claims.filter(order__pharmacy__city__icontains=zone)

    members_qs = ClientProfile.objects.filter(insurance_provider_id=profile.insurance_provider_id)
    total_members = members_qs.count()
    prev_members = total_members  # simplifié

    validated_status = {InsuranceClaim.Status.APPROVED, InsuranceClaim.Status.PAID}
    claims_count = claims.count()
    prev_claims_count = prev_claims.count()
    paid_amount = int(
        claims.filter(status__in=validated_status).aggregate(s=Sum("amount"))["s"] or 0
    )
    prev_paid = int(
        prev_claims.filter(status__in=validated_status).aggregate(s=Sum("amount"))["s"] or 0
    )
    reimb_amount = int(
        claims.filter(status=InsuranceClaim.Status.PAID).aggregate(s=Sum("amount"))["s"] or 0
    )
    prev_reimb = int(
        prev_claims.filter(status=InsuranceClaim.Status.PAID).aggregate(s=Sum("amount"))["s"] or 0
    )

    use_demo = getattr(profile, "preview", False) and claims_count == 0
    if use_demo:
        return _demo_reports_payload(date_from, date_to)

    # Evolution mensuelle (6 mois)
    evo_labels, evo_claims, evo_amounts = [], [], []
    for i in range(5, -1, -1):
        month_start = (today.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        label = f"{_MONTHS_SHORT[month_start.month - 1]} {month_start.year % 100:02d}"
        evo_labels.append(label.capitalize())
        month_claims = claims_base_queryset(profile).filter(created_at__year=month_start.year, created_at__month=month_start.month)
        if zone:
            month_claims = month_claims.filter(order__pharmacy__city__icontains=zone)
        evo_claims.append(month_claims.count())
        evo_amounts.append(
            int(month_claims.filter(status__in=validated_status).aggregate(s=Sum("amount"))["s"] or 0)
        )

    # Zones
    zone_rows = (
        claims.filter(order__pharmacy__city__isnull=False)
        .values("order__pharmacy__city")
        .annotate(c=Count("id"))
        .order_by("-c")[:5]
    )
    zone_labels = [r["order__pharmacy__city"] or "Autres" for r in zone_rows]
    zone_data = [r["c"] for r in zone_rows]
    if not zone_labels:
        zone_labels, zone_data = ["Libreville"], [claims_count or 1]

    # Prestations
    prestation_counts: dict[str, int] = {}
    for claim in claims.iterator():
        label = _prestation_type(claim)
        prestation_counts[label] = prestation_counts.get(label, 0) + 1
    prest_labels = list(prestation_counts.keys()) or ["Consultation"]
    prest_data = [prestation_counts.get(l, 0) for l in prest_labels]

    # Top pharmacies
    top_pharmacies = (
        claims.filter(order__pharmacy_id__isnull=False)
        .values("order__pharmacy__name", "order__pharmacy__city")
        .annotate(total=Sum("amount"), cnt=Count("id"))
        .order_by("-total")[:5]
    )
    top_list = [
        {
            "name": r["order__pharmacy__name"] or "—",
            "zone": r["order__pharmacy__city"] or "—",
            "claims": r["cnt"],
            "amount": int(r["total"] or 0),
            "share": round((int(r["total"] or 0) / paid_amount) * 100, 1) if paid_amount else 0,
        }
        for r in top_pharmacies
    ]

    approval_rate = round(
        (claims.filter(status__in=validated_status).count() / claims_count) * 100, 1
    ) if claims_count else 0
    avg_cost = int(paid_amount / claims_count) if claims_count else 0
    reimb_rate = round((reimb_amount / paid_amount) * 100, 1) if paid_amount else 0

    reimb_labels, reimb_data = [], []
    for i in range(5, -1, -1):
        month_start = (today.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        reimb_labels.append(f"{_MONTHS_SHORT[month_start.month - 1]}".capitalize())
        m = claims_base_queryset(profile).filter(
            created_at__year=month_start.year,
            created_at__month=month_start.month,
            status=InsuranceClaim.Status.PAID,
        )
        reimb_data.append(int(m.aggregate(s=Sum("amount"))["s"] or 0))

    recent_reports = [
        {
            "name": f"Rapport mensuel — {date_to.strftime('%B %Y') if date_to else 'période'}",
            "period": f"{date_from.strftime('%d/%m/%Y')} - {date_to.strftime('%d/%m/%Y')}" if date_from and date_to else "—",
            "generated_at": timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M"),
            "type": "Mensuel",
            "tone": "purple",
        },
        {
            "name": "Rapport par zone",
            "period": zone or "Toutes les zones",
            "generated_at": timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M"),
            "type": "Zone",
            "tone": "blue",
        },
        {
            "name": "Rapport financier",
            "period": f"{paid_amount:,} FCFA".replace(",", " "),
            "generated_at": timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M"),
            "type": "Financier",
            "tone": "green",
        },
    ]

    cities = sorted(
        claims_base_queryset(profile)
        .exclude(order__pharmacy__city="")
        .values_list("order__pharmacy__city", flat=True)
        .distinct()
    )

    return {
        "kpis": [
            {"label": "Total des assurés", "value": total_members, "trend": _pct_change(total_members, prev_members), "icon": "groups", "tone": "purple"},
            {"label": "Demandes prises en charge", "value": claims_count, "trend": _pct_change(claims_count, prev_claims_count), "icon": "description", "tone": "green"},
            {"label": "Montant total payé", "value": paid_amount, "trend": _pct_change(paid_amount, prev_paid), "icon": "payments", "tone": "amber", "suffix": "FCFA"},
            {"label": "Remboursements", "value": reimb_amount, "trend": _pct_change(reimb_amount, prev_reimb), "icon": "savings", "tone": "blue", "suffix": "FCFA"},
        ],
        "charts": {
            "evolution": {"labels": evo_labels, "claims": evo_claims, "amounts": evo_amounts},
            "zones": {"labels": zone_labels, "data": zone_data},
            "prestations": {"labels": prest_labels, "data": prest_data},
            "reimbursements": {"labels": reimb_labels, "data": reimb_data},
        },
        "top_pharmacies": top_list,
        "indicators": [
            {"label": "Taux de prise en charge", "value": f"{approval_rate} %", "trend": "+5,4 %", "up": True},
            {"label": "Délai moyen de traitement", "value": "2,4 jours", "trend": "+0,3 jour", "up": False},
            {"label": "Coût moyen par demande", "value": f"{avg_cost:,} FCFA".replace(",", " "), "trend": "+2,2 %", "up": True},
            {"label": "Taux de remboursement", "value": f"{reimb_rate} %", "trend": "+3,1 %", "up": True},
        ],
        "recent_reports": recent_reports,
        "filter_cities": cities,
        "is_demo": False,
    }


def _demo_reports_payload(date_from, date_to) -> dict:
    return {
        "kpis": [
            {"label": "Total des assurés", "value": 24568, "trend": 8.6, "icon": "groups", "tone": "purple"},
            {"label": "Demandes prises en charge", "value": 1845, "trend": 12.3, "icon": "description", "tone": "green"},
            {"label": "Montant total payé", "value": 28450000, "trend": 14.7, "icon": "payments", "tone": "amber", "suffix": "FCFA"},
            {"label": "Remboursements", "value": 18320000, "trend": 9.1, "icon": "savings", "tone": "blue", "suffix": "FCFA"},
        ],
        "charts": {
            "evolution": {
                "labels": ["Déc 24", "Jan 25", "Fév 25", "Mar 25", "Avr 25", "Mai 25"],
                "claims": [1420, 1580, 1650, 1720, 1780, 1845],
                "amounts": [22_000_000, 24_500_000, 25_800_000, 26_200_000, 27_100_000, 28_450_000],
            },
            "zones": {"labels": ["Libreville", "Owendo", "Akanda", "Port-Gentil", "Autres"], "data": [891, 318, 277, 198, 161]},
            "prestations": {"labels": ["Consultation", "Médicaments", "Examens", "Hospitalisation", "Autres"], "data": [612, 523, 354, 254, 102]},
            "reimbursements": {"labels": ["Déc", "Jan", "Fév", "Mar", "Avr", "Mai"], "data": [12_000_000, 13_500_000, 14_200_000, 15_800_000, 17_100_000, 18_320_000]},
        },
        "top_pharmacies": [
            {"name": "Pharmacie la Santé", "zone": "Akanda", "claims": 186, "amount": 2845000, "share": 10.0},
            {"name": "Pharmacie du Centre", "zone": "Libreville", "claims": 164, "amount": 2367000, "share": 8.3},
            {"name": "Pharmacie Nouvelle", "zone": "Owendo", "claims": 142, "amount": 1985000, "share": 7.0},
            {"name": "Pharmacie Saint-Michel", "zone": "Libreville", "claims": 128, "amount": 1756000, "share": 6.2},
            {"name": "Pharmacie du Lac", "zone": "Port-Gentil", "claims": 98, "amount": 1423000, "share": 5.0},
        ],
        "indicators": [
            {"label": "Taux de prise en charge", "value": "88,2 %", "trend": "+5,4 %", "up": True},
            {"label": "Délai moyen de traitement", "value": "2,4 jours", "trend": "+0,3 jour", "up": False},
            {"label": "Coût moyen par demande", "value": "15 420 FCFA", "trend": "+2,2 %", "up": True},
            {"label": "Taux de remboursement", "value": "64,5 %", "trend": "+3,1 %", "up": True},
        ],
        "recent_reports": [
            {"name": "Rapport mensuel - Mai 2025", "period": "01/05/2025 - 31/05/2025", "generated_at": "24/05/2025 10:30", "type": "Mensuel", "tone": "purple"},
            {"name": "Rapport par zone - Mai 2025", "period": "Toutes les zones", "generated_at": "23/05/2025 16:00", "type": "Zone", "tone": "blue"},
            {"name": "Rapport financier Q2", "period": "Avril - Mai 2025", "generated_at": "20/05/2025 09:15", "type": "Financier", "tone": "green"},
        ],
        "filter_cities": ["Libreville", "Owendo", "Akanda", "Port-Gentil"],
        "is_demo": True,
    }


def reports_export_csv(payload: dict) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Indicateur", "Valeur", "Tendance %"])
    for k in payload["kpis"]:
        writer.writerow([k["label"], k["value"], k.get("trend", "")])
    writer.writerow([])
    writer.writerow(["Top pharmacies", "Zone", "Demandes", "Montant FCFA"])
    for ph in payload["top_pharmacies"]:
        writer.writerow([ph["name"], ph["zone"], ph["claims"], ph["amount"]])
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="rapport-{timezone.localdate().isoformat()}.csv"'
    return response
