"""Espace patient — Mon Assurance (prise en charge PC)."""
from __future__ import annotations

from datetime import datetime

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin

from backoffice.decorators import role_required
from backoffice.views import _audit, _ctx, _ensure_client_profile, client_roles
from core.coverage_requests import (
    IN_PROGRESS_STATUSES,
    FEE_PAYMENT_METHODS,
    coverage_fee_for,
    create_coverage_request,
    log_access,
    patient_insurance_context,
    patient_reply_info,
    record_patient_payment,
    status_ui,
)
from payments.models import (
    CoverageInvoice,
    CoverageRequest,
    InsuranceProvider,
    Payment,
)
from pharmacies.models import Pharmacy


ALLOWED_RX_CONTENT = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
}
ALLOWED_RX_EXT = {".pdf", ".jpg", ".jpeg", ".png"}


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


def _valid_prescription(upload) -> bool:
    if not upload:
        return False
    name = (upload.name or "").lower()
    ext_ok = any(name.endswith(ext) for ext in ALLOWED_RX_EXT)
    ctype = (getattr(upload, "content_type", "") or "").lower()
    type_ok = ctype in ALLOWED_RX_CONTENT or ctype.startswith("image/")
    return ext_ok or type_ok


def _annotate_request(req: CoverageRequest) -> CoverageRequest:
    req.status_ui = status_ui(req.status)
    return req


@role_required(*client_roles)
def client_insurance_home(request):
    from core.coverage_requests import expire_stale_coverage_requests

    expire_stale_coverage_requests()
    ctx_ins = patient_insurance_context(request.user)
    recent = (
        CoverageRequest.objects.filter(client=request.user)
        .select_related("provider", "pharmacy")
        .order_by("-created_at")[:5]
    )
    for r in recent:
        _annotate_request(r)
    return render(
        request,
        "backoffice/client/insurance/home.html",
        _ctx(
            request,
            "insurance",
            insurance=ctx_ins,
            recent_requests=recent,
        ),
    )


@role_required(*client_roles)
def client_insurance_card(request):
    return render(
        request,
        "backoffice/client/insurance/card.html",
        _ctx(request, "insurance_card", insurance=patient_insurance_context(request.user)),
    )


@role_required(*client_roles)
def client_insurance_guarantees(request):
    ctx_ins = patient_insurance_context(request.user)
    return render(
        request,
        "backoffice/client/insurance/guarantees.html",
        _ctx(request, "insurance_guarantees", insurance=ctx_ins),
    )


@role_required(*client_roles)
def client_insurance_request_create(request):
    ctx_ins = patient_insurance_context(request.user)
    pharmacies = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).order_by("name")
    providers = InsuranceProvider.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        provider_id = request.POST.get("provider_id") or (
            ctx_ins["provider"].pk if ctx_ins["provider"] else None
        )
        pharmacy_id = request.POST.get("pharmacy_id")
        request_type = request.POST.get("request_type") or CoverageRequest.RequestType.PHARMACY
        desired_date = _parse_date(request.POST.get("desired_date", ""))
        prescription_date = _parse_date(request.POST.get("prescription_date", ""))
        doctor_name = request.POST.get("doctor_name", "").strip()
        comment = request.POST.get("comment", "").strip()
        insured_number = request.POST.get("insured_number", "").strip() or ctx_ins["insured_number"]
        try:
            estimated_amount = int(request.POST.get("estimated_amount") or 0)
        except (TypeError, ValueError):
            estimated_amount = 0
        rx_file = request.FILES.get("prescription_file")

        provider = InsuranceProvider.objects.filter(pk=provider_id, is_active=True).first()
        pharmacy = Pharmacy.objects.filter(pk=pharmacy_id, status=Pharmacy.Status.ACTIVE).first()

        if not provider:
            messages.error(request, "Choisissez votre organisme d'assurance.")
        elif not pharmacy:
            messages.error(request, "Choisissez la pharmacie concernée.")
        elif not insured_number:
            messages.error(request, "Indiquez votre numéro d'assuré.")
        elif not _valid_prescription(rx_file):
            messages.error(request, "Ajoutez votre ordonnance (PDF, JPG, JPEG ou PNG).")
        else:
            from core.privacy_consent import (
                apply_health_consent_from_post,
                ensure_client_profile,
                has_valid_health_consent,
            )

            profile = ensure_client_profile(request.user)
            if not has_valid_health_consent(profile):
                err = apply_health_consent_from_post(profile, request.POST)
                if err:
                    messages.error(request, err)
                    return redirect("bo_client_insurance_request_create")
                profile.refresh_from_db()
            if not profile.insurance_provider_id:
                profile.insurance_provider = provider
            if insured_number and not profile.insurance_number:
                profile.insurance_number = insured_number
            profile.save()
            try:
                req = create_coverage_request(
                    client=request.user,
                    provider=provider,
                    pharmacy=pharmacy,
                    insured_number=insured_number,
                    request_type=request_type,
                    desired_date=desired_date,
                    prescription_file=rx_file,
                    prescription_date=prescription_date,
                    doctor_name=doctor_name,
                    comment=comment,
                    estimated_amount=estimated_amount,
                    payment_method=request.POST.get("fee_payment_method", ""),
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("bo_client_insurance_request_create")
            _audit(
                request,
                "coverage_request_create",
                "insurance",
                req.reference,
                True,
            )
            messages.success(
                request,
                f"Demande {req.reference} envoyée"
                + (f" — frais de dossier {req.processing_fee} F payés" if req.processing_fee else "")
                + ". Vous pouvez suivre son avancement dans Mes demandes.",
            )
            return redirect("bo_client_insurance_request_detail", pk=req.pk)

    from core.privacy_consent import consent_context

    return render(
        request,
        "backoffice/client/insurance/request_create.html",
        _ctx(
            request,
            "insurance_request",
            insurance=ctx_ins,
            pharmacies=pharmacies,
            providers=providers,
            request_types=CoverageRequest.RequestType.choices,
            coverage_fee=coverage_fee_for(request.user),
            fee_payment_methods=FEE_PAYMENT_METHODS,
            **consent_context(request.user),
        ),
    )


@role_required(*client_roles)
def client_insurance_requests(request):
    status_filter = (request.GET.get("status") or "all").strip()
    qs = (
        CoverageRequest.objects.filter(client=request.user)
        .select_related("provider", "pharmacy")
        .order_by("-created_at")
    )
    if status_filter == "progress":
        qs = qs.filter(status__in=IN_PROGRESS_STATUSES)
    elif status_filter == "accepted":
        qs = qs.filter(
            status__in=[
                CoverageRequest.Status.ACCEPTED,
                CoverageRequest.Status.READY,
            ]
        )
    elif status_filter == "rejected":
        qs = qs.filter(status=CoverageRequest.Status.REJECTED)
    elif status_filter == "done":
        qs = qs.filter(status=CoverageRequest.Status.COMPLETED)
    requests_list = list(qs[:100])
    for r in requests_list:
        _annotate_request(r)
    return render(
        request,
        "backoffice/client/insurance/requests.html",
        _ctx(
            request,
            "insurance_requests",
            requests=requests_list,
            status_filter=status_filter,
        ),
    )


@role_required(*client_roles)
def client_insurance_request_detail(request, pk):
    from payments.models import CoverageAccessLog

    req = get_object_or_404(
        CoverageRequest.objects.select_related("provider", "pharmacy"),
        pk=pk,
        client=request.user,
    )
    invoice = CoverageInvoice.objects.filter(coverage_request=req).first()
    log_access(req, request.user, CoverageAccessLog.Action.VIEW, request=request)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reply_info" and req.status == CoverageRequest.Status.INFO_NEEDED:
            reply = request.POST.get("patient_info_reply", "").strip()
            if not reply:
                messages.error(request, "Saisissez votre réponse.")
            else:
                patient_reply_info(req, reply, actor=request.user)
                messages.success(request, "Réponse transmise à l'assureur.")
                return redirect("bo_client_insurance_request_detail", pk=req.pk)
        elif action == "pay" and invoice:
            if invoice.patient_share > 0 and invoice.payment_status != CoverageInvoice.PaymentStatus.PAID:
                method = request.POST.get("payment_method") or Payment.Method.MOOV
                if method not in dict(Payment.Method.choices):
                    method = Payment.Method.MOOV
                record_patient_payment(
                    invoice,
                    method=method,
                    reference=request.POST.get("payment_reference", ""),
                    actor=request.user,
                )
                _audit(request, "coverage_payment", "insurance", invoice.reference, True)
                messages.success(request, "Paiement confirmé.")
                return redirect("bo_client_insurance_request_detail", pk=req.pk)

    _annotate_request(req)
    events = req.events.select_related("actor").all()[:40]
    rx_access_logs = req.prescription_access_logs.all()[:30]
    return render(
        request,
        "backoffice/client/insurance/request_detail.html",
        _ctx(
            request,
            "insurance_requests",
            coverage_request=req,
            events=events,
            rx_access_logs=rx_access_logs,
            invoice=invoice,
            payment_methods=[
                (Payment.Method.MOOV, "Moov Money"),
                (Payment.Method.AIRTEL, "Airtel Money"),
                (Payment.Method.CARD, "Carte bancaire"),
                (Payment.Method.PHARMACY, "Paiement en pharmacie"),
            ],
        ),
    )


@role_required(*client_roles)
def client_insurance_invoices(request):
    invoices = (
        CoverageInvoice.objects.filter(coverage_request__client=request.user)
        .select_related("coverage_request", "coverage_request__provider", "coverage_request__pharmacy")
        .order_by("-created_at")[:80]
    )
    return render(
        request,
        "backoffice/client/insurance/invoices.html",
        _ctx(request, "insurance_invoices", invoices=invoices),
    )


@role_required(*client_roles)
def client_insurance_history(request):
    return redirect(f"{reverse('bo_client_insurance_requests')}?status=all")


@role_required(*client_roles)
@xframe_options_sameorigin
def client_coverage_prescription_file(request, pk):
    from backoffice.prescription_files import secure_prescription_response
    from payments.models import CoverageAccessLog

    req = get_object_or_404(CoverageRequest, pk=pk, client=request.user)
    if not req.prescription_file:
        raise Http404
    log_access(req, request.user, CoverageAccessLog.Action.PRESCRIPTION, request=request)
    return secure_prescription_response(
        request,
        req.prescription_file,
        context=f"Dossier {req.reference}",
        owner_id=req.client_id,
    )


@role_required(*client_roles)
def client_coverage_invoice_pdf(request, pk):
    from payments.models import CoverageAccessLog

    invoice = get_object_or_404(
        CoverageInvoice.objects.select_related("coverage_request"),
        pk=pk,
        coverage_request__client=request.user,
    )
    if not invoice.pdf_file:
        from core.coverage_requests import build_invoice_pdf

        build_invoice_pdf(invoice)
    log_access(
        invoice.coverage_request,
        request.user,
        CoverageAccessLog.Action.DOWNLOAD,
        request=request,
        details=invoice.reference,
    )
    return FileResponse(
        invoice.pdf_file.open("rb"),
        as_attachment=True,
        filename=f"{invoice.reference}.pdf",
    )


@role_required(*client_roles)
def client_insurance_subscribe(request):
    """Self-service : choisir une formule et souscrire (paiement simulé pour l'instant)."""
    from accounts.models import InsuranceMemberSubscription
    from core.insurer_subscription import subscribe_user
    from payments.models import InsuranceSubscriptionPlan, Payment

    ctx_ins = patient_insurance_context(request.user)
    plans = (
        InsuranceSubscriptionPlan.objects.filter(is_active=True, provider__is_active=True)
        .select_related("provider")
        .order_by("provider__name", "sort_order", "name")
    )

    if request.method == "POST":
        plan = InsuranceSubscriptionPlan.objects.filter(
            pk=request.POST.get("plan_id"),
            is_active=True,
            provider__is_active=True,
        ).select_related("provider").first()
        method = request.POST.get("payment_method") or Payment.Method.MOOV
        if method not in dict(Payment.Method.choices):
            method = Payment.Method.MOOV
        if not plan:
            messages.error(request, "Formule invalide.")
        else:
            # Paiement simulé — branchement E-Billing prévu bientôt
            sub = subscribe_user(
                request.user,
                plan.provider,
                plan,
                status=InsuranceMemberSubscription.Status.ACTIVE,
                insurance_number=(request.POST.get("insurance_number") or "").strip(),
            )
            _audit(
                request,
                "insurance_self_subscribe",
                "insurance",
                f"{sub.reference} — {plan.name} — {method} (simulé)",
                True,
            )
            messages.success(
                request,
                (
                    f"Abonnement {sub.reference} activé — {plan.name} "
                    f"({plan.premium_amount} FCFA). Paiement {dict(Payment.Method.choices).get(method)} "
                    "enregistré (mode simulation — E-Billing bientôt)."
                ),
            )
            return redirect("bo_client_insurance_home")

    return render(
        request,
        "backoffice/client/insurance/subscribe.html",
        _ctx(
            request,
            "insurance_subscribe",
            insurance=ctx_ins,
            plans=plans,
            payment_methods=[
                (Payment.Method.MOOV, "Moov Money"),
                (Payment.Method.AIRTEL, "Airtel Money"),
                (Payment.Method.CARD, "Carte bancaire"),
            ],
        ),
    )
