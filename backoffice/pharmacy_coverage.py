"""Portail pharmacie — demandes de prise en charge patient (PC)."""
from __future__ import annotations

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin

from backoffice.decorators import pharmacy_permission_required, role_required
from backoffice.views import _audit, _ctx, _pharmacy_for, pharmacy_roles
from core.coverage_requests import confirm_pharmacy_delivery, log_access, status_ui
from payments.models import CoverageAccessLog, CoverageRequest


def _pharmacy_coverage_qs(pharmacy):
    return (
        CoverageRequest.objects.filter(pharmacy=pharmacy)
        .exclude(status=CoverageRequest.Status.REJECTED)
        .select_related("client", "provider", "invoice")
        .order_by("-created_at")
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required("orders")
def pharmacy_coverage_list(request):
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
            try:
                delivered = int(request.POST.get("delivered_amount") or 0)
            except (TypeError, ValueError):
                delivered = 0
            if delivered <= 0:
                messages.error(request, "Indiquez le montant réellement délivré.")
            else:
                try:
                    confirm_pharmacy_delivery(
                        req,
                        delivered_amount=delivered,
                        actor=request.user,
                    )
                    _audit(
                        request,
                        "coverage_delivery",
                        "insurance",
                        f"{req.reference} — {delivered} F",
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
    return render(
        request,
        "backoffice/pharmacy/coverage_detail.html",
        _ctx(request, "coverage", pharmacy=pharmacy, coverage_request=req),
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
    return FileResponse(req.prescription_file.open("rb"), as_attachment=False)
