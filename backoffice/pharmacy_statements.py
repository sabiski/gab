"""Portail pharmacie — relevés mensuels de tiers payant (remboursements assureurs)."""
from __future__ import annotations

import csv
from datetime import date, datetime

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from backoffice.decorators import pharmacy_permission_required, role_required
from backoffice.views import _audit, _ctx, _pharmacy_for, pharmacy_roles
from core.insurance_statements import (
    build_statement_pdf,
    generate_statement,
    mark_sent,
    month_overview,
    month_start,
    outstanding_summary,
    record_payment,
    statement_lines,
    totals_by_source,
)
from payments.models import InsuranceProvider, InsuranceStatement

MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


def _parse_month(value) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except (TypeError, ValueError):
        return month_start(timezone.localdate())


def _month_label(d: date) -> str:
    return f"{MONTHS_FR[d.month - 1]} {d.year}"


def _shift_month(d: date, delta: int) -> date:
    m = d.month - 1 + delta
    return date(d.year + m // 12, m % 12 + 1, 1)


@role_required(*pharmacy_roles)
@pharmacy_permission_required("stats")
def pharmacy_statements(request):
    pharmacy = _pharmacy_for(request.user, request)
    if not pharmacy:
        return redirect("bo_pharmacy_dashboard")
    period = _parse_month(request.GET.get("month") or request.POST.get("month"))

    if request.method == "POST" and request.POST.get("action") == "generate":
        provider = get_object_or_404(InsuranceProvider, pk=request.POST.get("provider_id"))
        try:
            st = generate_statement(pharmacy, provider, period, user=request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(f"{request.path}?month={period:%Y-%m}")
        _audit(request, "insurance_statement_generate", "pharmacy", st.reference, True)
        messages.success(request, f"Relevé {st.reference} prêt pour {provider.name}.")
        return redirect("bo_pharmacy_statement_detail", pk=st.pk)

    rows = month_overview(pharmacy, period)
    history = (
        InsuranceStatement.objects.filter(pharmacy=pharmacy)
        .select_related("provider")
        .order_by("-period", "provider__name")[:24]
    )
    today_month = month_start(timezone.localdate())
    return render(
        request,
        "backoffice/pharmacy/statements.html",
        _ctx(
            request,
            "statements",
            period=period,
            period_label=_month_label(period),
            prev_month=_shift_month(period, -1),
            next_month=_shift_month(period, 1) if period < today_month else None,
            rows=rows,
            month_total=sum(r["live"]["total"] for r in rows),
            history=history,
            outstanding=outstanding_summary(pharmacy),
        ),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required("stats")
def pharmacy_statement_detail(request, pk):
    pharmacy = _pharmacy_for(request.user, request)
    st = get_object_or_404(
        InsuranceStatement.objects.select_related("provider", "pharmacy"), pk=pk, pharmacy=pharmacy
    )

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "regenerate":
                generate_statement(pharmacy, st.provider, st.period, user=request.user)
                messages.success(request, "Relevé recalculé avec les dernières opérations.")
            elif action == "mark_sent":
                mark_sent(st)
                messages.success(request, "Relevé marqué comme envoyé à l'assureur. Il est maintenant figé.")
            elif action == "payment":
                record_payment(st, int(request.POST.get("amount") or 0))
                messages.success(request, "Règlement enregistré.")
            elif action == "notes":
                st.notes = request.POST.get("notes", "").strip()[:255]
                st.save(update_fields=["notes", "updated_at"])
        except (ValueError, TypeError) as exc:
            messages.error(request, str(exc) or "Action impossible.")
        else:
            _audit(request, f"insurance_statement_{action}", "pharmacy", st.reference, True)
        return redirect("bo_pharmacy_statement_detail", pk=st.pk)

    lines = statement_lines(pharmacy, st.provider, st.period)
    fmt = request.GET.get("format")
    if fmt == "pdf":
        response = HttpResponse(build_statement_pdf(st, lines), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{st.reference}.pdf"'
        return response
    if fmt == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{st.reference}.csv"'
        response.write("﻿")
        writer = csv.writer(response, delimiter=";")
        writer.writerow(["Date", "Origine", "Référence", "Bénéficiaire", "N° assuré", "Produits", "Montant", "Part assurance"])
        for l in lines:
            writer.writerow([
                timezone.localtime(l.date).strftime("%d/%m/%Y"), l.source, l.reference,
                l.beneficiary, l.insured_number, l.items, l.gross, l.insurance_amount,
            ])
        return response

    live = totals_by_source(lines)
    return render(
        request,
        "backoffice/pharmacy/statement_detail.html",
        _ctx(
            request,
            "statements",
            st=st,
            lines=lines,
            live=live,
            out_of_date=live["total"] != st.total,
            period_label=_month_label(st.period),
            missing_insured=sum(1 for l in lines if not l.insured_number),
        ),
    )
