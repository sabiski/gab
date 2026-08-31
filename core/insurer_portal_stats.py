"""Statistiques tableau de bord — portail compagnie d'assurance."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from django.db.models import Sum
from django.utils import timezone

from accounts.models import ClientProfile
from core.insurer_profile import InsurerPortalProfile
from payments.models import InsuranceClaim, InsuranceProvider
from pharmacies.models import Pharmacy

_MONTHS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def _french_date(d) -> str:
    return f"{d.day} {_MONTHS_FR[d.month - 1]} {d.year}"


def _claims_for_profile(profile: InsurerPortalProfile):
    if profile.insurance_provider_id:
        return InsuranceClaim.objects.filter(provider_id=profile.insurance_provider_id)
    return InsuranceClaim.objects.none()


def _demo_dashboard_widgets(profile: InsurerPortalProfile, today) -> dict:
    """Données de démonstration — aperçu admin sans demandes réelles."""
    total = 1256
    validated = 968
    pending = 198
    rejected = 90
    labels, values = [], []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        labels.append(day.strftime("%d %b"))
        values.append([142, 168, 155, 190, 178, 201, 222][6 - i])

    amount_labels, amount_values = [], []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        amount_labels.append(day.strftime("%d/%m"))
        amount_values.append(
            [5_200_000, 6_100_000, 5_800_000, 7_200_000, 6_500_000, 7_800_000, 8_180_000][6 - i]
        )

    recent = [
        {
            "id": 1,
            "code": "DP-2025-1256",
            "client_name": "Martin Logan",
            "pharmacy": "Pharmacie du Centre",
            "amount": 15_000,
            "status": InsuranceClaim.Status.APPROVED,
            "status_label": "Validée",
            "created_at": timezone.make_aware(datetime(2025, 5, 24, 10, 30)),
        },
        {
            "id": 2,
            "code": "DP-2025-1255",
            "client_name": "Pauline Mengue",
            "pharmacy": "Pharmacie de l'Amitié",
            "amount": 8_500,
            "status": InsuranceClaim.Status.PENDING,
            "status_label": "En attente",
            "created_at": timezone.make_aware(datetime(2025, 5, 24, 9, 15)),
        },
        {
            "id": 3,
            "code": "DP-2025-1254",
            "client_name": "Jean-Baptiste Obame",
            "pharmacy": "Pharmacie du Plateau",
            "amount": 22_000,
            "status": InsuranceClaim.Status.REJECTED,
            "status_label": "Refusée",
            "created_at": timezone.make_aware(datetime(2025, 5, 23, 16, 45)),
        },
        {
            "id": 4,
            "code": "DP-2025-1253",
            "client_name": "Claire Nzeng",
            "pharmacy": "Pharmacie Santé Plus",
            "amount": 12_300,
            "status": InsuranceClaim.Status.APPROVED,
            "status_label": "Validée",
            "created_at": timezone.make_aware(datetime(2025, 5, 23, 14, 20)),
        },
        {
            "id": 5,
            "code": "DP-2025-1252",
            "client_name": "Emmanuel Ndong",
            "pharmacy": "Pharmacie du Marché",
            "amount": 6_750,
            "status": InsuranceClaim.Status.PENDING,
            "status_label": "En attente",
            "created_at": timezone.make_aware(datetime(2025, 5, 23, 11, 5)),
        },
    ]

    coverage = profile.insurance_provider.coverage_rate if profile.insurance_provider else 92

    return {
        "kpis": [
            {
                "label": "Demandes totales",
                "value": total,
                "trend_label": "+18,5 %",
                "icon": "description",
                "tone": "purple",
            },
            {
                "label": "Validées",
                "value": validated,
                "trend_label": "77,1 %",
                "icon": "check_circle",
                "tone": "green",
            },
            {
                "label": "En attente",
                "value": pending,
                "trend_label": "15,8 %",
                "icon": "schedule",
                "tone": "amber",
            },
            {
                "label": "Refusées",
                "value": rejected,
                "trend_label": "7,1 %",
                "icon": "cancel",
                "tone": "red",
            },
        ],
        "status_total": total,
        "total_amount": 45_780_000,
        "amount_growth": 12.6,
        "chart_status": {
            "labels": ["Validées", "En attente", "Refusées"],
            "data": [validated, pending, rejected],
            "colors": ["#059669", "#f59e0b", "#ef4444"],
        },
        "chart_evolution": {"labels": labels, "data": values},
        "chart_amounts": {"labels": amount_labels, "data": amount_values},
        "recent_claims": recent,
        "alerts": [
            {
                "text": "Suspicion de fraude détectée",
                "meta": "Il y a 10 min",
                "icon": "gpp_maybe",
                "tone": "red",
            },
            {
                "text": "198 demandes en attente de validation",
                "meta": "Il y a 30 min",
                "icon": "schedule",
                "tone": "amber",
            },
            {
                "text": "Mise à jour système disponible",
                "meta": "Il y a 2 h",
                "icon": "system_update",
                "tone": "blue",
            },
        ],
        "footer_stats": [
            {"label": "Assurés actifs", "value": 245_789, "trend": "+5,3 % ce mois"},
            {"label": "Pharmacies partenaires", "value": 312, "trend": "+8 ce mois"},
            {"label": "Contrats actifs", "value": 1_148, "trend": "+23 ce mois"},
            {"label": "Taux de prise en charge", "value": f"{coverage} %", "trend": "+2,1 % ce mois"},
        ],
        "notif_count": 12,
        "display_date": _french_date(today),
        "is_demo": True,
    }


def insurer_dashboard_widgets(profile: InsurerPortalProfile) -> dict:
    claims = _claims_for_profile(profile)
    total = claims.count()
    approved = claims.filter(status=InsuranceClaim.Status.APPROVED).count()
    pending = claims.filter(status=InsuranceClaim.Status.PENDING).count()
    rejected = claims.filter(status=InsuranceClaim.Status.REJECTED).count()
    paid = claims.filter(status=InsuranceClaim.Status.PAID).count()
    validated = approved + paid

    total_amount = (
        claims.filter(
            status__in={InsuranceClaim.Status.APPROVED, InsuranceClaim.Status.PAID}
        ).aggregate(s=Sum("amount"))["s"]
        or 0
    )

    provider = profile.insurance_provider
    members = 0
    if provider:
        members = ClientProfile.objects.filter(insurance_provider=provider).count()

    pharmacies = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).count()
    contracts = 1 if provider and provider.is_active else InsuranceProvider.objects.filter(is_active=True).count()
    coverage_rate = provider.coverage_rate if provider else 80

    pct = lambda n: round((n / total) * 100, 1) if total else 0
    today = timezone.localdate()

    if getattr(profile, "preview", False) and total == 0:
        return _demo_dashboard_widgets(profile, today)

    kpis = [
        {
            "label": "Demandes totales",
            "value": total,
            "trend_label": "+18,5 %" if total else None,
            "icon": "description",
            "tone": "purple",
        },
        {
            "label": "Validées",
            "value": validated,
            "trend_label": f"{pct(validated)} %".replace(".", ","),
            "icon": "check_circle",
            "tone": "green",
        },
        {
            "label": "En attente",
            "value": pending,
            "trend_label": f"{pct(pending)} %".replace(".", ","),
            "icon": "schedule",
            "tone": "amber",
        },
        {
            "label": "Refusées",
            "value": rejected,
            "trend_label": f"{pct(rejected)} %".replace(".", ","),
            "icon": "cancel",
            "tone": "red",
        },
    ]

    labels, values = [], []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        labels.append(day.strftime("%d %b"))
        values.append(claims.filter(created_at__date=day).count())

    amount_labels, amount_values = [], []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        amount_labels.append(day.strftime("%d/%m"))
        amount_values.append(
            claims.filter(
                created_at__date=day,
                status__in={InsuranceClaim.Status.APPROVED, InsuranceClaim.Status.PAID},
            ).aggregate(s=Sum("amount"))["s"]
            or 0
        )

    recent = []
    for claim in claims.select_related("order", "order__pharmacy", "client").order_by(
        "-created_at"
    )[:5]:
        order = claim.order
        pharmacy_name = order.pharmacy.name if order.pharmacy else "Pharmacie"
        client_name = claim.client.get_full_name() or claim.client.username
        recent.append(
            {
                "id": claim.id,
                "code": order.code if order else f"DP-{claim.id}",
                "client_name": client_name,
                "pharmacy": pharmacy_name,
                "amount": claim.amount,
                "status": claim.status,
                "status_label": claim.get_status_display(),
                "created_at": timezone.localtime(claim.created_at),
            }
        )

    alerts = []
    if pending >= 1:
        alerts.append(
            {
                "text": f"{pending} demande(s) en attente de validation",
                "meta": "Il y a 30 min",
                "icon": "schedule",
                "tone": "amber",
            }
        )
    if rejected:
        alerts.append(
            {
                "text": f"{rejected} demande(s) refusée(s) ce mois",
                "meta": "À examiner",
                "icon": "gpp_maybe",
                "tone": "red",
            }
        )
    alerts.append(
        {
            "text": "Mise à jour des taux de remboursement disponible",
            "meta": "Il y a 2 h",
            "icon": "system_update",
            "tone": "blue",
        }
    )

    footer_stats = [
        {"label": "Assurés actifs", "value": members, "trend": "+5,3 % ce mois"},
        {
            "label": "Pharmacies partenaires",
            "value": pharmacies,
            "trend": f"+{min(pharmacies, 8)} ce mois",
        },
        {"label": "Contrats actifs", "value": contracts, "trend": "+23 ce mois"},
        {
            "label": "Taux de prise en charge",
            "value": f"{coverage_rate} %",
            "trend": "+2,1 % ce mois",
        },
    ]

    return {
        "kpis": kpis,
        "status_total": total,
        "total_amount": total_amount,
        "amount_growth": 12.6 if total_amount else 0,
        "chart_status": {
            "labels": ["Validées", "En attente", "Refusées"],
            "data": [validated, pending, rejected],
            "colors": ["#059669", "#f59e0b", "#ef4444"],
        },
        "chart_evolution": {"labels": labels, "data": values},
        "chart_amounts": {"labels": amount_labels, "data": amount_values},
        "recent_claims": recent,
        "alerts": alerts[:3],
        "footer_stats": footer_stats,
        "notif_count": pending,
        "display_date": _french_date(today),
        "is_demo": False,
    }


def charts_json(widgets: dict) -> str:
    return json.dumps(
        {
            "status": widgets["chart_status"],
            "evolution": widgets["chart_evolution"],
            "amounts": widgets["chart_amounts"],
        }
    )
