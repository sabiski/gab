"""Assurés — portail compagnie d'assurance."""
from __future__ import annotations

import csv
import io

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone

from accounts.models import ClientProfile, User
from core.insurer_profile import InsurerPortalProfile


def members_base_queryset(profile: InsurerPortalProfile):
    if not profile.insurance_provider_id:
        return ClientProfile.objects.none()
    return (
        ClientProfile.objects.filter(insurance_provider_id=profile.insurance_provider_id)
        .select_related("user", "insurance_provider")
        .order_by("-user__date_joined")
    )


def members_apply_search(qs, q: str):
    q = (q or "").strip()
    if not q:
        return qs
    return qs.filter(
        Q(user__first_name__icontains=q)
        | Q(user__last_name__icontains=q)
        | Q(user__email__icontains=q)
        | Q(user__phone__icontains=q)
        | Q(insurance_number__icontains=q)
    )


def members_apply_tab(qs, tab: str):
    tab = (tab or "all").strip()
    if tab == "active":
        return qs.filter(user__status=User.Status.ACTIVE)
    if tab == "inactive":
        return qs.filter(
            user__status__in={
                User.Status.INACTIVE,
                User.Status.PENDING,
                User.Status.SUSPENDED,
            }
        )
    return qs


def members_apply_filters(qs, *, city: str | None = None, member_status: str | None = None):
    if city:
        qs = qs.filter(user__city__icontains=city)
    if member_status == "active":
        qs = qs.filter(user__status=User.Status.ACTIVE)
    elif member_status == "inactive":
        qs = qs.filter(user__status__in={User.Status.INACTIVE, User.Status.PENDING})
    elif member_status == "deregistered":
        qs = qs.filter(user__status=User.Status.SUSPENDED)
    return qs


def members_stats(profile: InsurerPortalProfile) -> dict:
    base = members_base_queryset(profile)
    total = base.count()
    active = base.filter(user__status=User.Status.ACTIVE).count()
    inactive = base.filter(
        user__status__in={User.Status.INACTIVE, User.Status.PENDING}
    ).count()
    deregistered = base.filter(user__status=User.Status.SUSPENDED).count()

    def pct(n):
        return round((n / total) * 100, 1) if total else 0

    return {
        "total": total,
        "active": active,
        "inactive": inactive,
        "deregistered": deregistered,
        "pct_total": 100 if total else 0,
        "pct_active": pct(active),
        "pct_inactive": pct(inactive),
        "pct_deregistered": pct(deregistered),
    }


def member_initials(user: User) -> str:
    parts = [p for p in (user.first_name, user.last_name) if p]
    if not parts:
        parts = [user.username or "?"]
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def member_status_ui(user: User) -> dict:
    if user.status == User.Status.ACTIVE:
        return {"label": "Actif", "tone": "active", "icon": "check_circle"}
    if user.status == User.Status.SUSPENDED:
        return {"label": "Radié", "tone": "deregistered", "icon": "block"}
    return {"label": "Inactif", "tone": "inactive", "icon": "pause_circle"}


def member_cities_for_filter(profile: InsurerPortalProfile):
    return sorted(
        members_base_queryset(profile)
        .exclude(user__city="")
        .values_list("user__city", flat=True)
        .distinct()
    )


def enrich_member_row(profile: ClientProfile) -> ClientProfile:
    user = profile.user
    profile.ui_initials = member_initials(user)
    profile.ui_status = member_status_ui(user)
    profile.ui_name = user.get_full_name() or user.username
    profile.ui_phone = user.phone or "—"
    profile.ui_adhesion = user.date_joined
    return profile


def members_export_csv(profile: InsurerPortalProfile, queryset) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Nom",
            "Téléphone",
            "E-mail",
            "N° assuré",
            "Date de naissance",
            "Statut",
            "Date d'adhésion",
            "Ville",
        ]
    )
    for row in queryset.iterator():
        user = row.user
        status = member_status_ui(user)
        writer.writerow(
            [
                user.get_full_name() or user.username,
                user.phone,
                user.email,
                row.insurance_number,
                row.date_of_birth.strftime("%d/%m/%Y") if row.date_of_birth else "",
                status["label"],
                timezone.localtime(user.date_joined).strftime("%d/%m/%Y"),
                user.city,
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="assures-{timezone.localdate().isoformat()}.csv"'
    )
    return response
