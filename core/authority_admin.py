"""Statistiques et exports — gestion admin des autorités sanitaires."""
from __future__ import annotations

import csv
import json
from datetime import timedelta
from io import StringIO

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone

from accounts.models import AuthorityProfile, User
from core.gabon_regions import GABON_PROVINCES, normalize_region_name
from notifications.models import AuditLog


def _authority_queryset():
    return (
        User.objects.filter(role=User.Role.AUTHORITY)
        .select_related("authority_profile", "authority_profile__validated_by")
        .order_by("-date_joined")
    )


def _period_bounds(request):
    today = timezone.localdate()
    start_raw = (request.GET.get("from") or "").strip()
    end_raw = (request.GET.get("to") or "").strip()
    if start_raw and end_raw:
        try:
            from datetime import datetime

            start = datetime.strptime(start_raw, "%d/%m/%Y").date()
            end = datetime.strptime(end_raw, "%d/%m/%Y").date()
            return start, end
        except ValueError:
            pass
    start = today.replace(day=1)
    end = today
    return start, end


def filter_authorities(request):
    qs = _authority_queryset()
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    region = (request.GET.get("region") or "").strip()
    access_level = (request.GET.get("access_level") or "").strip()
    start, end = _period_bounds(request)

    if request.GET.get("from") and request.GET.get("to"):
        qs = qs.filter(date_joined__date__gte=start, date_joined__date__lte=end)

    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(username__icontains=q)
            | Q(authority_profile__institution__icontains=q)
            | Q(authority_profile__authority_code__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if region:
        if region == "national":
            qs = qs.filter(
                Q(authority_profile__region__iexact="")
                | Q(authority_profile__region__icontains="national")
                | Q(authority_profile__access_level__in=[
                    AuthorityProfile.AccessLevel.NATIONAL_ADMIN,
                    AuthorityProfile.AccessLevel.NATIONAL_READER,
                ])
            )
        else:
            qs = qs.filter(authority_profile__region__icontains=region)
    if access_level:
        qs = qs.filter(authority_profile__access_level=access_level)
    return qs, start, end


def _pct_change(current: int, previous: int) -> tuple[str, str]:
    if previous <= 0:
        if current > 0:
            return "+100 %", "up"
        return "0 %", "flat"
    delta = ((current - previous) / previous) * 100
    sign = "+" if delta >= 0 else ""
    trend = "up" if delta > 0 else "down" if delta < 0 else "flat"
    return f"{sign}{delta:.1f} %", trend


def authority_kpis(request) -> dict:
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    base = User.objects.filter(role=User.Role.AUTHORITY)
    total = base.count()
    active = base.filter(status=User.Status.ACTIVE).count()
    pending = base.filter(status=User.Status.PENDING).count()
    suspended = base.filter(status=User.Status.SUSPENDED).count()
    authorized_users = active

    def count_in_period(start, end, **filters):
        return base.filter(
            date_joined__gte=start,
            date_joined__lt=end,
            **filters,
        ).count()

    total_prev = count_in_period(prev_month_start, month_start)
    active_prev = count_in_period(
        prev_month_start, month_start, status=User.Status.ACTIVE
    )
    pending_prev = count_in_period(
        prev_month_start, month_start, status=User.Status.PENDING
    )
    suspended_prev = count_in_period(
        prev_month_start, month_start, status=User.Status.SUSPENDED
    )

    t_total, tr_total = _pct_change(total, total_prev)
    t_active, tr_active = _pct_change(active, active_prev)
    t_pending, tr_pending = _pct_change(pending, pending_prev)
    t_susp, tr_susp = _pct_change(suspended, suspended_prev)
    t_users, tr_users = _pct_change(authorized_users, active_prev)

    return {
        "total": total,
        "active": active,
        "pending": pending,
        "suspended": suspended,
        "authorized_users": authorized_users,
        "trends": {
            "total": (t_total, tr_total),
            "active": (t_active, tr_active),
            "pending": (t_pending, tr_pending),
            "suspended": (t_susp, tr_susp),
            "authorized_users": (t_users, tr_users),
        },
    }


def access_level_chart() -> dict:
    rows = (
        AuthorityProfile.objects.values("access_level")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    labels, data, colors = [], [], []
    palette = {
        AuthorityProfile.AccessLevel.NATIONAL_ADMIN: "#7c3aed",
        AuthorityProfile.AccessLevel.REGIONAL_ADMIN: "#2563eb",
        AuthorityProfile.AccessLevel.NATIONAL_READER: "#059669",
        AuthorityProfile.AccessLevel.REGIONAL_READER: "#0d9488",
    }
    for row in rows:
        level = row["access_level"]
        labels.append(dict(AuthorityProfile.AccessLevel.choices).get(level, level))
        data.append(row["count"])
        colors.append(palette.get(level, "#94a3b8"))
    return {"labels": labels, "data": data, "colors": colors}


def region_chart() -> dict:
    counts: dict[str, int] = {p["name"]: 0 for p in GABON_PROVINCES}
    counts["National"] = 0
    for profile in AuthorityProfile.objects.select_related("user").iterator():
        region = normalize_region_name(profile.region)
        if not region or region.lower() == "national":
            if profile.access_level in {
                AuthorityProfile.AccessLevel.NATIONAL_ADMIN,
                AuthorityProfile.AccessLevel.NATIONAL_READER,
            }:
                counts["National"] += 1
            elif profile.region:
                counts[region] = counts.get(region, 0) + 1
        else:
            counts[region] = counts.get(region, 0) + 1
    labels = [k for k, v in counts.items() if v > 0]
    data = [counts[k] for k in labels]
    return {"labels": labels, "data": data}


def recent_authority_activities(limit: int = 8) -> list[dict]:
    logs = (
        AuditLog.objects.select_related("user")
        .filter(
            Q(module__in=("users", "authorities"))
            & (
                Q(action__icontains="authority")
                | Q(action__icontains="autorit")
                | Q(details__icontains="autorit")
                | Q(details__icontains="AUTH-")
            )
        )
        .order_by("-created_at")[:limit]
    )
    items = []
    for log in logs:
        actor = log.user.get_full_name() if log.user else "Système"
        items.append(
            {
                "text": log.details or log.action.replace("_", " ").title(),
                "meta": f"Par {actor} · {timezone.localtime(log.created_at).strftime('%d/%m/%Y %H:%M')}",
                "icon": "verified" if "valid" in log.action else "person_add",
            }
        )

    pending = (
        User.objects.filter(role=User.Role.AUTHORITY, status=User.Status.PENDING)
        .select_related("authority_profile")[:3]
    )
    for u in pending:
        inst = (
            u.authority_profile.institution
            if getattr(u, "authority_profile", None)
            else u.get_full_name()
        )
        items.insert(
            0,
            {
                "text": f"Autorité en attente : {inst}",
                "meta": f"Inscription {timezone.localtime(u.date_joined).strftime('%d/%m/%Y')}",
                "icon": "hourglass_top",
            },
        )
    return items[:limit]


def export_authorities_csv(qs) -> HttpResponse:
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Code",
            "Institution",
            "Responsable",
            "E-mail",
            "Téléphone",
            "Région",
            "Niveau d'accès",
            "Statut",
            "Date inscription",
        ]
    )
    for user in qs:
        profile = getattr(user, "authority_profile", None)
        writer.writerow(
            [
                profile.display_code if profile else "",
                profile.institution if profile else "",
                user.get_full_name() or user.username,
                user.email,
                user.phone,
                profile.region_display if profile else "",
                profile.get_access_level_display() if profile else "",
                user.get_status_display(),
                timezone.localtime(user.date_joined).strftime("%d/%m/%Y"),
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="autorites-sanitaires.csv"'
    return response


def charts_json() -> str:
    return json.dumps(
        {
            "access_levels": access_level_chart(),
            "regions": region_chart(),
        }
    )
