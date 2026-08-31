"""Contrats et polices — portail compagnie d'assurance."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone

from accounts.models import ClientProfile, User
from core.insurer_profile import InsurerPortalProfile


def _plan_label(coverage_rate: int) -> str:
    if coverage_rate >= 100:
        return "Santé Plus"
    if coverage_rate >= 80:
        return "Santé Entreprise"
    return "Essentiel"


def _contract_year_bounds(year: int | None = None) -> tuple[date, date]:
    year = year or timezone.localdate().year
    return date(year, 1, 1), date(year, 12, 31)


def contracts_base_queryset(profile: InsurerPortalProfile):
    if not profile.insurance_provider_id:
        return ClientProfile.objects.none()
    return (
        ClientProfile.objects.filter(insurance_provider_id=profile.insurance_provider_id)
        .select_related("user", "insurance_provider")
        .order_by("-user__date_joined")
    )


def contracts_apply_search(qs, q: str):
    q = (q or "").strip()
    if not q:
        return qs
    return qs.filter(
        Q(user__first_name__icontains=q)
        | Q(user__last_name__icontains=q)
        | Q(insurance_number__icontains=q)
        | Q(user__email__icontains=q)
    )


def contracts_filter_tab(qs, tab: str):
    tab = (tab or "all").strip()
    if tab == "all":
        return qs
    ids = []
    for cp in qs.iterator():
        row = build_contract_row(cp)
        tone = row["status"]["tone"]
        if tab == "active" and tone == "active":
            ids.append(cp.pk)
        elif tab == "expiring" and tone == "expiring":
            ids.append(cp.pk)
        elif tab == "expired" and tone == "expired":
            ids.append(cp.pk)
    return qs.filter(pk__in=ids) if ids else qs.none()


def contracts_apply_filters(qs, *, plan: str | None = None, status: str | None = None):
    if plan == "plus":
        qs = qs.filter(insurance_provider__coverage_rate__gte=100)
    elif plan == "entreprise":
        qs = qs.filter(
            insurance_provider__coverage_rate__gte=80,
            insurance_provider__coverage_rate__lt=100,
        )
    elif plan == "essentiel":
        qs = qs.filter(insurance_provider__coverage_rate__lt=80)
    return qs


def contract_status_ui(profile: ClientProfile, start: date, end: date) -> dict:
    today = timezone.localdate()
    user = profile.user
    if user.status == User.Status.SUSPENDED:
        return {"label": "Résilié", "tone": "expired", "icon": "block"}
    if user.status in {User.Status.INACTIVE, User.Status.PENDING}:
        return {"label": "Expiré", "tone": "expired", "icon": "cancel"}
    if end < today:
        return {"label": "Expiré", "tone": "expired", "icon": "cancel"}
    if end <= today + timedelta(days=60):
        return {"label": "En cours", "tone": "expiring", "icon": "schedule"}
    return {"label": "Actif", "tone": "active", "icon": "check_circle"}


def build_contract_row(profile: ClientProfile) -> dict:
    user = profile.user
    provider = profile.insurance_provider
    rate = provider.coverage_rate if provider else 80
    start, end = _contract_year_bounds()
    if user.date_joined:
        joined = timezone.localtime(user.date_joined).date()
        if joined > start:
            start = joined
    status = contract_status_ui(profile, start, end)
    return {
        "reference": f"CT-{end.year}-{profile.pk:05d}",
        "holder_name": user.get_full_name() or user.username,
        "holder_sub": profile.insurance_number or user.email,
        "plan_label": _plan_label(rate),
        "start_date": start,
        "end_date": end,
        "status": status,
        "profile_id": profile.pk,
        "user_email": user.email,
    }


def enrich_contract_profile(profile: ClientProfile) -> ClientProfile:
    profile.ui_contract = build_contract_row(profile)
    return profile


def contracts_stats(profile: InsurerPortalProfile) -> dict:
    base = contracts_base_queryset(profile)
    total = base.count()
    today = timezone.localdate()
    _, end = _contract_year_bounds()

    active = 0
    expiring = 0
    expired = 0
    for cp in base.iterator():
        row = build_contract_row(cp)
        tone = row["status"]["tone"]
        if tone == "active":
            active += 1
        elif tone == "expiring":
            expiring += 1
        else:
            expired += 1

    def pct(n):
        return round((n / total) * 100, 1) if total else 0

    return {
        "total": total,
        "active": active,
        "expiring": expiring,
        "expired": expired,
        "pct_total": 100 if total else 0,
        "pct_active": pct(active),
        "pct_expiring": pct(expiring),
        "pct_expired": pct(expired),
    }


def contracts_charts_data(profile: InsurerPortalProfile) -> dict:
    base = contracts_base_queryset(profile)
    plus = entreprise = autres = 0
    upcoming = []
    today = timezone.localdate()

    for cp in base.iterator():
        row = build_contract_row(cp)
        plan = row["plan_label"]
        if plan == "Santé Plus":
            plus += 1
        elif plan == "Santé Entreprise":
            entreprise += 1
        else:
            autres += 1
        days_left = (row["end_date"] - today).days
        if 0 <= days_left <= 90 and row["status"]["tone"] in {"expiring", "active"}:
            upcoming.append(
                {
                    "name": row["holder_name"],
                    "end_date": row["end_date"],
                    "days_left": days_left,
                    "reference": row["reference"],
                }
            )

    upcoming.sort(key=lambda x: x["end_date"])
    upcoming = upcoming[:5]

    recent_docs = [
        {"label": row["reference"] + ".pdf", "reference": row["reference"]}
        for row in [build_contract_row(cp) for cp in base[:5]]
    ]

    return {
        "plan_split": {
            "labels": ["Santé Plus", "Santé Entreprise", "Autres"],
            "data": [plus, entreprise, autres],
            "colors": ["#7c3aed", "#3b82f6", "#9ca3af"],
        },
        "upcoming": upcoming,
        "recent_docs": recent_docs,
    }


def contracts_export_csv(queryset) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Référence", "Assuré", "N° assuré", "Type", "Date effet", "Date expiration", "Statut"]
    )
    for cp in queryset.iterator():
        row = build_contract_row(cp)
        writer.writerow(
            [
                row["reference"],
                row["holder_name"],
                row["holder_sub"],
                row["plan_label"],
                row["start_date"].strftime("%d/%m/%Y"),
                row["end_date"].strftime("%d/%m/%Y"),
                row["status"]["label"],
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="contrats-{timezone.localdate().isoformat()}.csv"'
    )
    return response
