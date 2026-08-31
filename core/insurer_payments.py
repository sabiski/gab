"""Paiements et remboursements — portail compagnie d'assurance."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.utils import timezone

from core.insurer_claims import claims_base_queryset, claim_insured_number
from core.insurer_profile import InsurerPortalProfile
from payments.models import InsuranceClaim


def payments_base_queryset(profile: InsurerPortalProfile):
    return (
        claims_base_queryset(profile)
        .exclude(status=InsuranceClaim.Status.REJECTED)
        .select_related("client", "client__client_profile", "order", "order__pharmacy", "provider")
    )


def payments_apply_search(qs, q: str):
    q = (q or "").strip()
    if not q:
        return qs
    return qs.filter(
        Q(order__code__icontains=q)
        | Q(client__first_name__icontains=q)
        | Q(client__last_name__icontains=q)
        | Q(client__client_profile__insurance_number__icontains=q)
        | Q(order__pharmacy__name__icontains=q)
        | Q(order__pharmacy__code__icontains=q)
    )


def payments_apply_filters(
    qs,
    *,
    tx_type: str | None = None,
    status: str | None = None,
    date_from=None,
    date_to=None,
):
    if tx_type == "payment":
        qs = qs.filter(status=InsuranceClaim.Status.APPROVED)
    elif tx_type == "reimbursement":
        qs = qs.filter(status=InsuranceClaim.Status.PAID)
    if status == "paid":
        qs = qs.filter(status=InsuranceClaim.Status.PAID)
    elif status == "processing":
        qs = qs.filter(status=InsuranceClaim.Status.APPROVED)
    elif status == "pending":
        qs = qs.filter(status=InsuranceClaim.Status.PENDING)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


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


def payments_stats(profile: InsurerPortalProfile, qs=None) -> dict:
    base = payments_base_queryset(profile)
    if qs is None:
        qs = base

    total_payments = int(
        base.filter(status=InsuranceClaim.Status.APPROVED).aggregate(s=Sum("amount"))["s"] or 0
    ) + int(base.filter(status=InsuranceClaim.Status.PAID).aggregate(s=Sum("amount"))["s"] or 0)
    total_reimbursements = int(
        base.filter(status=InsuranceClaim.Status.PAID).aggregate(s=Sum("amount"))["s"] or 0
    )
    today = timezone.localdate()
    today_qs = base.filter(created_at__date=today)
    today_count = today_qs.count()
    today_amount = int(today_qs.aggregate(s=Sum("amount"))["s"] or 0)
    pending_settlement = int(
        base.filter(status=InsuranceClaim.Status.APPROVED).aggregate(s=Sum("amount"))["s"] or 0
    )
    pending_count = base.filter(status=InsuranceClaim.Status.APPROVED).count()

    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    current_month = int(
        base.filter(created_at__date__gte=month_start).aggregate(s=Sum("amount"))["s"] or 0
    )
    prev_month = int(
        base.filter(
            created_at__date__gte=prev_month_start,
            created_at__date__lte=prev_month_end,
        ).aggregate(s=Sum("amount"))["s"]
        or 0
    )
    payment_trend = round(((current_month - prev_month) / prev_month) * 100, 1) if prev_month else 0
    reimb_trend = payment_trend  # simplifié

    return {
        "total_payments": total_payments,
        "total_reimbursements": total_reimbursements,
        "today_count": today_count,
        "today_amount": today_amount,
        "pending_settlement": pending_settlement,
        "pending_count": pending_count,
        "payment_trend": payment_trend,
        "reimb_trend": reimb_trend,
    }


def transaction_reference(claim: InsuranceClaim) -> str:
    year = claim.created_at.year if claim.created_at else timezone.localdate().year
    prefix = "RMB" if claim.status == InsuranceClaim.Status.PAID else "PAY"
    return f"{prefix}-{year}-{claim.pk:05d}"


def transaction_type_ui(claim: InsuranceClaim) -> dict:
    if claim.status == InsuranceClaim.Status.PAID:
        return {"label": "Remboursement", "tone": "reimbursement"}
    return {"label": "Paiement", "tone": "payment"}


def transaction_status_ui(claim: InsuranceClaim) -> dict:
    mapping = {
        InsuranceClaim.Status.PAID: ("Remboursé", "paid", "check_circle"),
        InsuranceClaim.Status.APPROVED: ("En traitement", "processing", "schedule"),
        InsuranceClaim.Status.PENDING: ("En attente", "pending", "hourglass_top"),
    }
    label, tone, icon = mapping.get(
        claim.status, (claim.get_status_display(), "pending", "help")
    )
    return {"label": label, "tone": tone, "icon": icon}


def enrich_transaction(claim: InsuranceClaim) -> InsuranceClaim:
    claim.tx_reference = transaction_reference(claim)
    claim.tx_type = transaction_type_ui(claim)
    claim.tx_status = transaction_status_ui(claim)
    order = claim.order
    pharmacy = order.pharmacy if order else None
    claim.tx_pharmacy_name = pharmacy.name if pharmacy else "—"
    claim.tx_pharmacy_location = ""
    if pharmacy:
        parts = [p for p in (pharmacy.district, pharmacy.city) if p]
        claim.tx_pharmacy_location = ", ".join(parts) if parts else pharmacy.city
    claim.tx_insured_number = claim_insured_number(claim)
    claim.tx_client_name = claim.client.get_full_name() or claim.client.username
    return claim


def payments_charts_data(profile: InsurerPortalProfile) -> dict:
    base = payments_base_queryset(profile).exclude(status=InsuranceClaim.Status.PENDING)
    payment_total = int(
        base.filter(status=InsuranceClaim.Status.APPROVED).aggregate(s=Sum("amount"))["s"] or 0
    )
    reimb_total = int(
        base.filter(status=InsuranceClaim.Status.PAID).aggregate(s=Sum("amount"))["s"] or 0
    )
    grand = payment_total + reimb_total or 1

    today = timezone.localdate()
    labels, pay_vals, reimb_vals = [], [], []
    for i in range(29, -1, -1):
        day = today - timedelta(days=i)
        labels.append(day.strftime("%d/%m"))
        day_qs = base.filter(created_at__date=day)
        pay_vals.append(
            int(
                day_qs.filter(status=InsuranceClaim.Status.APPROVED).aggregate(s=Sum("amount"))["s"]
                or 0
            )
        )
        reimb_vals.append(
            int(day_qs.filter(status=InsuranceClaim.Status.PAID).aggregate(s=Sum("amount"))["s"] or 0)
        )

    top_pharmacies = (
        base.filter(order__pharmacy_id__isnull=False)
        .values("order__pharmacy__name", "order__pharmacy__city")
        .annotate(total=Sum("amount"))
        .order_by("-total")[:5]
    )
    top_list = [
        {
            "name": row["order__pharmacy__name"] or "—",
            "city": row["order__pharmacy__city"] or "",
            "total": int(row["total"] or 0),
        }
        for row in top_pharmacies
    ]

    return {
        "type_split": {
            "labels": ["Paiements", "Remboursements"],
            "data": [payment_total, reimb_total],
            "colors": ["#10b981", "#3b82f6"],
            "total": grand,
            "pct_payment": round(payment_total / grand * 100, 1),
            "pct_reimb": round(reimb_total / grand * 100, 1),
        },
        "monthly": {"labels": labels, "payments": pay_vals, "reimbursements": reimb_vals},
        "top_pharmacies": top_list,
    }


def payments_export_csv(queryset) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Référence", "Type", "Assuré", "N° assuré", "Pharmacie", "Montant", "Date", "Statut"]
    )
    for claim in queryset.iterator():
        enrich_transaction(claim)
        writer.writerow(
            [
                claim.tx_reference,
                claim.tx_type["label"],
                claim.tx_client_name,
                claim.tx_insured_number,
                claim.tx_pharmacy_name,
                claim.amount,
                timezone.localtime(claim.created_at).strftime("%d/%m/%Y %H:%M"),
                claim.tx_status["label"],
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="paiements-{timezone.localdate().isoformat()}.csv"'
    )
    return response


def parse_payment_dates(request) -> tuple:
    date_from = _parse_date(request.GET.get("date_from", ""))
    date_to = _parse_date(request.GET.get("date_to", ""))
    if not date_from and not date_to:
        today = timezone.localdate()
        date_from = today.replace(day=1)
        date_to = today
    return date_from, date_to
