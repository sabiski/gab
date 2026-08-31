"""Pharmacies partenaires — portail compagnie d'assurance."""
from __future__ import annotations

import csv
import io

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone

from core.insurer_profile import InsurerPortalProfile
from pharmacies.models import Pharmacy


def pharmacies_base_queryset(profile: InsurerPortalProfile):
    """Réseau des officines conventionnées sur la plateforme."""
    del profile  # réservé pour filtrage futur par contrat assureur
    return Pharmacy.objects.exclude(status=Pharmacy.Status.REJECTED).order_by("-created_at")


def pharmacies_apply_search(qs, q: str):
    q = (q or "").strip()
    if not q:
        return qs
    return qs.filter(
        Q(name__icontains=q)
        | Q(code__icontains=q)
        | Q(city__icontains=q)
        | Q(district__icontains=q)
        | Q(address__icontains=q)
        | Q(region__icontains=q)
    )


def pharmacies_apply_tab(qs, tab: str):
    tab = (tab or "all").strip()
    if tab == "active":
        return qs.filter(status=Pharmacy.Status.ACTIVE)
    if tab == "pending":
        return qs.filter(status=Pharmacy.Status.PENDING)
    if tab == "suspended":
        return qs.filter(status=Pharmacy.Status.SUSPENDED)
    return qs


def pharmacies_apply_filters(qs, *, city: str | None = None, region: str | None = None, status: str | None = None):
    if city:
        qs = qs.filter(city__icontains=city)
    if region:
        qs = qs.filter(region__icontains=region)
    if status == "active":
        qs = qs.filter(status=Pharmacy.Status.ACTIVE)
    elif status == "pending":
        qs = qs.filter(status=Pharmacy.Status.PENDING)
    elif status == "suspended":
        qs = qs.filter(status=Pharmacy.Status.SUSPENDED)
    return qs


def pharmacies_stats(profile: InsurerPortalProfile) -> dict:
    base = pharmacies_base_queryset(profile)
    total = base.count()
    active = base.filter(status=Pharmacy.Status.ACTIVE).count()
    pending = base.filter(status=Pharmacy.Status.PENDING).count()
    suspended = base.filter(status=Pharmacy.Status.SUSPENDED).count()

    def pct(n):
        return round((n / total) * 100, 1) if total else 0

    return {
        "total": total,
        "active": active,
        "pending": pending,
        "suspended": suspended,
        "pct_total": 100 if total else 0,
        "pct_active": pct(active),
        "pct_pending": pct(pending),
        "pct_suspended": pct(suspended),
    }


def pharmacy_status_ui(pharmacy: Pharmacy) -> dict:
    mapping = {
        Pharmacy.Status.ACTIVE: ("Active", "active", "check_circle"),
        Pharmacy.Status.PENDING: ("En attente", "pending", "schedule"),
        Pharmacy.Status.SUSPENDED: ("Suspendue", "suspended", "pause_circle"),
    }
    label, tone, icon = mapping.get(
        pharmacy.status, (pharmacy.get_status_display(), "pending", "help")
    )
    return {"label": label, "tone": tone, "icon": icon}


def pharmacy_location_line(pharmacy: Pharmacy) -> str:
    parts = [p for p in (pharmacy.district, pharmacy.address) if p]
    if parts:
        return ", ".join(parts[:2])
    return pharmacy.address or pharmacy.city or "—"


def pharmacy_cities_for_filter(profile: InsurerPortalProfile):
    return sorted(
        pharmacies_base_queryset(profile)
        .exclude(city="")
        .values_list("city", flat=True)
        .distinct()
    )


def pharmacy_regions_for_filter(profile: InsurerPortalProfile):
    return sorted(
        pharmacies_base_queryset(profile)
        .exclude(region="")
        .values_list("region", flat=True)
        .distinct()
    )


def enrich_pharmacy_row(pharmacy: Pharmacy) -> Pharmacy:
    pharmacy.ui_status = pharmacy_status_ui(pharmacy)
    pharmacy.ui_location = pharmacy_location_line(pharmacy)
    pharmacy.ui_adhesion = pharmacy.created_at
    pharmacy.ui_logo_url = pharmacy.logo.url if pharmacy.logo else ""
    return pharmacy


def get_pharmacy_for_insurer(profile: InsurerPortalProfile, pharmacy_id: int) -> Pharmacy:
    from django.shortcuts import get_object_or_404

    return get_object_or_404(pharmacies_base_queryset(profile), pk=pharmacy_id)


def build_pharmacy_detail(profile: InsurerPortalProfile, pharmacy: Pharmacy) -> dict:
    from django.db.models import Count, Sum

    from core.insurer_claims import claims_base_queryset

    enrich_pharmacy_row(pharmacy)
    claims_qs = claims_base_queryset(profile).filter(order__pharmacy_id=pharmacy.pk)
    agg = claims_qs.aggregate(
        total_claims=Count("id"),
        total_amount=Sum("amount"),
        pending=Count("id", filter=Q(status="pending")),
        approved=Count("id", filter=Q(status="approved")),
        paid=Count("id", filter=Q(status="paid")),
    )
    recent_claims = (
        claims_qs.select_related("client", "order")
        .order_by("-created_at")[:8]
    )
    return {
        "pharmacy": pharmacy,
        "total_claims": agg["total_claims"] or 0,
        "total_amount": int(agg["total_amount"] or 0),
        "pending_claims": agg["pending"] or 0,
        "approved_claims": agg["approved"] or 0,
        "paid_claims": agg["paid"] or 0,
        "recent_claims": recent_claims,
    }


def pharmacies_export_csv(profile: InsurerPortalProfile, queryset) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Pharmacie", "Code partenaire", "Ville", "Région", "Adresse", "Statut", "Date d'adhésion", "Téléphone"]
    )
    for ph in queryset.iterator():
        status = pharmacy_status_ui(ph)
        writer.writerow(
            [
                ph.name,
                ph.code,
                ph.city,
                ph.region,
                ph.address,
                status["label"],
                timezone.localtime(ph.created_at).strftime("%d/%m/%Y"),
                ph.phone,
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="pharmacies-partenaires-{timezone.localdate().isoformat()}.csv"'
    )
    return response
