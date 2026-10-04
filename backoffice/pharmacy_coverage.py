"""Portail pharmacie — demandes de prise en charge patient (PC)."""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin

from backoffice.decorators import pharmacy_permission_required, role_required
from backoffice.views import _audit, _ctx, _pharmacy_for, pharmacy_roles
from core.coverage_requests import confirm_pharmacy_delivery, log_access, status_ui
from backoffice.prescription_files import secure_prescription_response
from core.prescription_privacy import log_prescription_view
from payments.models import CoverageAccessLog, CoverageRequest


def _pharmacy_coverage_qs(pharmacy):
    return (
        CoverageRequest.objects.filter(pharmacy=pharmacy)
        .exclude(status=CoverageRequest.Status.REJECTED)
        .select_related("client", "provider", "invoice")
        .order_by("-created_at")
    )


def _parse_delivery_lines(post) -> list[dict]:
    names = post.getlist("line_name")
    qtys = post.getlist("line_qty")
    prices = post.getlist("line_price")
    covered = post.getlist("line_covered")
    lines = []
    for i, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        try:
            qty = max(1, int(qtys[i] if i < len(qtys) else 1))
        except (TypeError, ValueError):
            qty = 1
        try:
            unit = max(0, int(prices[i] if i < len(prices) else 0))
        except (TypeError, ValueError):
            unit = 0
        is_covered = True
        if i < len(covered):
            is_covered = covered[i] in {"1", "true", "on", "yes"}
        lines.append(
            {
                "name": name[:200],
                "quantity": qty,
                "unit_price": unit,
                "is_covered": is_covered,
            }
        )
    return lines


@role_required(*pharmacy_roles)
@pharmacy_permission_required("orders")
def pharmacy_coverage_list(request):
    from core.coverage_requests import expire_stale_coverage_requests

    expire_stale_coverage_requests()
    pharmacy = _pharmacy_for(request.user, request)
    if not pharmacy:
        messages.error(request, "Aucune pharmacie associée.")
        return redirect("bo_pharmacy_dashboard")
    items = list(_pharmacy_coverage_qs(pharmacy)[:80])
    for item in items:
        item.status_ui = status_ui(item.status)
    return render(
        request,
        "backoffice/pharmacy/coverage_list.html",
        _ctx(request, "coverage", pharmacy=pharmacy, coverage_requests=items),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required("orders")
def pharmacy_coverage_detail(request, pk):
    pharmacy = _pharmacy_for(request.user, request)
    if not pharmacy:
        return redirect("bo_pharmacy_dashboard")
    req = get_object_or_404(
        CoverageRequest.objects.select_related("client", "provider", "invoice"),
        pk=pk,
        pharmacy=pharmacy,
    )
    log_access(req, request.user, CoverageAccessLog.Action.VIEW, request=request)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "confirm_delivery":
            lines = _parse_delivery_lines(request.POST)
            try:
                delivered = int(request.POST.get("delivered_amount") or 0)
            except (TypeError, ValueError):
                delivered = 0
            try:
                if lines:
                    confirm_pharmacy_delivery(req, lines=lines, actor=request.user)
                elif delivered > 0:
                    confirm_pharmacy_delivery(
                        req, delivered_amount=delivered, actor=request.user
                    )
                else:
                    raise ValueError("Ajoutez des produits délivrés ou un montant total.")
                _audit(
                    request,
                    "coverage_delivery",
                    "insurance",
                    f"{req.reference} — lignes={len(lines)}",
                    True,
                )
                messages.success(
                    request,
                    "Délivrance confirmée. Les parts assurance / patient ont été recalculées.",
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("bo_pharmacy_coverage_detail", pk=req.pk)

    req.status_ui = status_ui(req.status)
    from payments.models import CoverageInvoice

    invoice = CoverageInvoice.objects.filter(coverage_request=req).prefetch_related("lines").first()
    invoice_lines = list(invoice.lines.all()) if invoice else []
    return render(
        request,
        "backoffice/pharmacy/coverage_detail.html",
        _ctx(
            request,
            "coverage",
            pharmacy=pharmacy,
            coverage_request=req,
            invoice_lines=invoice_lines,
        ),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required("orders")
@xframe_options_sameorigin
def pharmacy_coverage_prescription(request, pk):
    pharmacy = _pharmacy_for(request.user, request)
    req = get_object_or_404(CoverageRequest, pk=pk, pharmacy=pharmacy)
    if not req.prescription_file:
        raise Http404
    log_access(req, request.user, CoverageAccessLog.Action.PRESCRIPTION, request=request)
    log_prescription_view(request.user, coverage_request=req, request=request)
    return secure_prescription_response(
        request,
        req.prescription_file,
        context=f"Dossier {req.reference}",
        owner_id=req.client_id,
    )
