"""Demandes de prise en charge patient (PC-XXXXXX) — workflow, calcul, facture, notifs."""
from __future__ import annotations

import io
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from accounts.models import ClientProfile, InsuranceDependent, InsuranceMemberSubscription
from notifications.models import Notification
from notifications.services import notify_user
from payments.models import (
    CoverageAccessLog,
    CoverageInvoice,
    CoverageInvoiceLine,
    CoverageRequest,
    CoverageRequestEvent,
    InsuranceProvider,
    PlatformPaymentSettings,
)


STATUS_UI = {
    CoverageRequest.Status.SENT: ("Envoyée", "sent", "send", "#F59E0B"),
    CoverageRequest.Status.UNDER_REVIEW: ("En vérification", "review", "hourglass_top", "#3B82F6"),
    CoverageRequest.Status.INFO_NEEDED: ("Infos complémentaires", "info", "info", "#F97316"),
    CoverageRequest.Status.ACCEPTED: ("Acceptée", "accepted", "check_circle", "#10B981"),
    CoverageRequest.Status.REJECTED: ("Refusée", "rejected", "cancel", "#EF4444"),
    CoverageRequest.Status.EXPIRED: ("Expirée", "expired", "timer_off", "#6B7280"),
    CoverageRequest.Status.READY: ("Préparée", "ready", "local_pharmacy", "#8B5CF6"),
    CoverageRequest.Status.COMPLETED: ("Terminée", "completed", "task_alt", "#059669"),
}

IN_PROGRESS_STATUSES = {
    CoverageRequest.Status.SENT,
    CoverageRequest.Status.UNDER_REVIEW,
    CoverageRequest.Status.INFO_NEEDED,
    CoverageRequest.Status.ACCEPTED,
    CoverageRequest.Status.READY,
}


@dataclass
class CoverageBreakdown:
    total: int
    insurance_amount: int
    patient_amount: int
    uncovered_amount: int
    coverage_rate: int


def status_ui(status: str) -> dict:
    label, tone, icon, color = STATUS_UI.get(
        status, (status, "muted", "help", "#6B7280")
    )
    return {"label": label, "tone": tone, "icon": icon, "color": color}


def generate_pc_reference() -> str:
    for _ in range(20):
        ref = f"PC-{secrets.randbelow(1_000_000):06d}"
        if not CoverageRequest.objects.filter(reference=ref).exists():
            return ref
    return f"PC-{timezone.now().strftime('%H%M%S')}{secrets.randbelow(100):02d}"


def generate_fac_reference() -> str:
    for _ in range(20):
        ref = f"FAC-{secrets.randbelow(1_000_000):06d}"
        if not CoverageInvoice.objects.filter(reference=ref).exists():
            return ref
    return f"FAC-{timezone.now().strftime('%H%M%S')}{secrets.randbelow(100):02d}"


def ensure_client_profile(user) -> ClientProfile:
    profile, _ = ClientProfile.objects.get_or_create(user=user)
    return profile


def active_subscription_for(user, provider: InsuranceProvider | None = None):
    qs = (
        InsuranceMemberSubscription.objects.filter(
            status=InsuranceMemberSubscription.Status.ACTIVE,
        )
        .filter(
            models_q_user_or_profile(user),
        )
        .select_related("insurance_provider", "subscription_plan")
        .order_by("-starts_at")
    )
    if provider:
        qs = qs.filter(insurance_provider=provider)
    return qs.first()


def models_q_user_or_profile(user):
    from django.db.models import Q

    profile = getattr(user, "client_profile", None)
    q = Q(user=user)
    if profile:
        q |= Q(client_profile=profile)
    return q


def resolve_coverage_rate(user, provider: InsuranceProvider) -> int:
    sub = active_subscription_for(user, provider)
    if sub and sub.coverage_rate:
        return int(sub.coverage_rate)
    if sub and sub.subscription_plan_id and sub.subscription_plan.coverage_rate:
        return int(sub.subscription_plan.coverage_rate)
    return int(provider.coverage_rate or 80)


def remaining_annual_cap(user, provider: InsuranceProvider) -> int:
    annual_cap = getattr(provider, "annual_cap", None) or 500_000
    year = timezone.localdate().year
    used = (
        CoverageRequest.objects.filter(
            client=user,
            provider=provider,
            created_at__year=year,
            status__in=[
                CoverageRequest.Status.ACCEPTED,
                CoverageRequest.Status.READY,
                CoverageRequest.Status.COMPLETED,
            ],
        ).aggregate(s=Sum("insurance_amount"))["s"]
        or 0
    )
    profile = ensure_client_profile(user)
    if profile.insurance_available_ceiling is not None:
        return max(0, int(profile.insurance_available_ceiling))
    return max(0, int(annual_cap) - int(used))


def calculate_coverage(
    *,
    total: int,
    coverage_rate: int,
    remaining_cap: int | None = None,
    uncovered: int = 0,
) -> CoverageBreakdown:
    total = max(0, int(total))
    uncovered = max(0, min(int(uncovered), total))
    covered_base = max(0, total - uncovered)
    rate = max(0, min(100, int(coverage_rate)))
    theoretical = int(covered_base * rate / 100)
    if remaining_cap is not None:
        theoretical = min(theoretical, max(0, int(remaining_cap)))
    insurance_amount = min(theoretical, covered_base)
    patient_amount = max(0, covered_base - insurance_amount) + uncovered
    return CoverageBreakdown(
        total=total,
        insurance_amount=insurance_amount,
        patient_amount=patient_amount,
        uncovered_amount=uncovered,
        coverage_rate=rate,
    )


def apply_amounts(req: CoverageRequest, breakdown: CoverageBreakdown, *, save=True) -> None:
    req.coverage_rate = breakdown.coverage_rate
    req.insurance_amount = breakdown.insurance_amount
    req.patient_amount = breakdown.patient_amount
    req.uncovered_amount = breakdown.uncovered_amount
    if save:
        req.save(
            update_fields=[
                "coverage_rate",
                "insurance_amount",
                "patient_amount",
                "uncovered_amount",
                "updated_at",
            ]
        )


def log_event(req: CoverageRequest, action: str, message: str = "", actor=None) -> CoverageRequestEvent:
    return CoverageRequestEvent.objects.create(
        coverage_request=req,
        actor=actor,
        action=action,
        message=message or "",
    )


def log_access(req: CoverageRequest, user, action: str, request=None, details: str = "") -> None:
    ip = None
    if request is not None:
        ip = request.META.get("REMOTE_ADDR")
    CoverageAccessLog.objects.create(
        coverage_request=req,
        user=user if getattr(user, "is_authenticated", False) else None,
        action=action,
        ip_address=ip,
        details=details[:255],
    )


def _notify_patient(req: CoverageRequest, title: str, message: str, *, ntype=None, extra=None):
    data = {
        "event": "coverage_request",
        "coverage_request_id": req.id,
        "reference": req.reference,
        "url": reverse("bo_client_insurance_request_detail", kwargs={"pk": req.pk}),
    }
    if extra:
        data.update(extra)
    notify_user(
        req.client,
        title,
        message,
        notification_type=ntype or Notification.Type.INFO,
        data=data,
        transactional=True,
    )


def _insurer_users(provider: InsuranceProvider):
    from accounts.models import PartnerProfile, User

    partner_ids = PartnerProfile.objects.filter(
        partner_type=PartnerProfile.PartnerType.INSURER,
        insurance_provider=provider,
    ).values_list("user_id", flat=True)
    return User.objects.filter(id__in=partner_ids, status=User.Status.ACTIVE)


def _pharmacy_staff(pharmacy):
    from accounts.models import User
    from pharmacies.models import PharmacyEmployee

    users = []
    if pharmacy.owner_id:
        users.append(pharmacy.owner)
    member_ids = PharmacyEmployee.objects.filter(
        pharmacy=pharmacy,
        is_active=True,
    ).values_list("user_id", flat=True)
    for u in User.objects.filter(id__in=member_ids, status=User.Status.ACTIVE):
        if u not in users:
            users.append(u)
    return users


def notify_insurer_new_request(req: CoverageRequest) -> None:
    url = reverse("bo_insurer_coverage_detail", kwargs={"pk": req.pk})
    for user in _insurer_users(req.provider):
        notify_user(
            user,
            f"Nouvelle prise en charge {req.reference}",
            (
                f"Patient : {req.client.get_full_name() or req.client.username}. "
                f"Pharmacie : {req.pharmacy.name}. "
                f"Montant estimé : {req.estimated_amount} FCFA."
            ),
            notification_type=Notification.Type.ORDER,
            data={
                "event": "coverage_request",
                "coverage_request_id": req.id,
                "url": url,
            },
            transactional=True,
        )


def notify_pharmacy_coverage(req: CoverageRequest, *, event: str = "new") -> None:
    url = reverse("bo_pharmacy_coverage_detail", kwargs={"pk": req.pk})
    if event == "new":
        title = f"Nouvelle prise en charge {req.reference}"
        message = (
            f"Patient : {req.client.get_full_name() or req.client.username}. "
            f"Assurance : {req.provider.name}. "
            f"Total estimé : {req.total_amount} FCFA — "
            f"Part assurance : {req.insurance_amount} FCFA — "
            f"Part patient : {req.patient_amount} FCFA."
        )
    else:
        title = f"Prise en charge mise à jour {req.reference}"
        message = f"Statut : {req.get_status_display()}. Part assurance : {req.insurance_amount} FCFA."
    for user in _pharmacy_staff(req.pharmacy):
        notify_user(
            user,
            title,
            message,
            notification_type=Notification.Type.ORDER,
            data={
                "event": "coverage_pharmacy",
                "coverage_request_id": req.id,
                "url": url,
            },
            transactional=True,
        )


@transaction.atomic
def create_coverage_request(
    *,
    client,
    provider: InsuranceProvider,
    pharmacy,
    insured_number: str,
    request_type: str,
    desired_date,
    prescription_file,
    prescription_date,
    doctor_name: str,
    comment: str,
    estimated_amount: int = 0,
) -> CoverageRequest:
    sub = active_subscription_for(client, provider)
    rate = resolve_coverage_rate(client, provider)
    remaining = remaining_annual_cap(client, provider)
    breakdown = calculate_coverage(
        total=estimated_amount,
        coverage_rate=rate,
        remaining_cap=remaining,
    )
    req = CoverageRequest(
        reference=generate_pc_reference(),
        client=client,
        provider=provider,
        pharmacy=pharmacy,
        subscription=sub,
        insured_number=(insured_number or "").strip().upper(),
        request_type=request_type or CoverageRequest.RequestType.PHARMACY,
        desired_date=desired_date or None,
        prescription_file=prescription_file,
        prescription_date=prescription_date or None,
        doctor_name=(doctor_name or "").strip(),
        comment=(comment or "").strip(),
        estimated_amount=max(0, int(estimated_amount or 0)),
        status=CoverageRequest.Status.SENT,
    )
    apply_amounts(req, breakdown, save=False)
    req.save()
    log_event(req, "created", "Demande envoyée par le patient.", actor=client)
    _notify_patient(
        req,
        f"Demande envoyée — {req.reference}",
        (
            f"Votre demande de prise en charge a été transmise à {provider.name}. "
            "Vous serez notifié dès qu'elle sera examinée."
        ),
        ntype=Notification.Type.SUCCESS,
    )
    notify_insurer_new_request(req)
    return req


@transaction.atomic
def mark_under_review(req: CoverageRequest, actor=None) -> CoverageRequest:
    if req.status != CoverageRequest.Status.SENT:
        return req
    req.status = CoverageRequest.Status.UNDER_REVIEW
    req.save(update_fields=["status", "updated_at"])
    log_event(req, "under_review", "L'assurance examine la demande.", actor=actor)
    _notify_patient(
        req,
        f"En vérification — {req.reference}",
        f"{req.provider.name} examine votre demande de prise en charge.",
        ntype=Notification.Type.INFO,
    )
    return req


@transaction.atomic
def request_more_info(req: CoverageRequest, message: str, actor=None) -> CoverageRequest:
    req.status = CoverageRequest.Status.INFO_NEEDED
    req.info_request_message = (message or "").strip()
    req.save(update_fields=["status", "info_request_message", "updated_at"])
    log_event(req, "info_needed", req.info_request_message, actor=actor)
    _notify_patient(
        req,
        f"Document demandé — {req.reference}",
        (
            f"{req.provider.name} demande des informations complémentaires. "
            f"{req.info_request_message}"
        ),
        ntype=Notification.Type.WARNING,
    )
    return req


@transaction.atomic
def patient_reply_info(req: CoverageRequest, reply: str, actor=None) -> CoverageRequest:
    req.patient_info_reply = (reply or "").strip()
    req.status = CoverageRequest.Status.UNDER_REVIEW
    req.save(update_fields=["patient_info_reply", "status", "updated_at"])
    log_event(req, "patient_reply", req.patient_info_reply, actor=actor)
    notify_insurer_new_request(req)
    _notify_patient(
        req,
        f"Réponse envoyée — {req.reference}",
        "Votre réponse a été transmise à l'assureur.",
        ntype=Notification.Type.INFO,
    )
    return req


@transaction.atomic
def accept_coverage(
    req: CoverageRequest,
    *,
    coverage_rate: int | None = None,
    estimated_amount: int | None = None,
    uncovered_amount: int = 0,
    actor=None,
) -> CoverageRequest:
    if estimated_amount is not None:
        req.estimated_amount = max(0, int(estimated_amount))
    rate = int(coverage_rate) if coverage_rate is not None else req.coverage_rate
    remaining = remaining_annual_cap(req.client, req.provider)
    breakdown = calculate_coverage(
        total=req.estimated_amount,
        coverage_rate=rate,
        remaining_cap=remaining,
        uncovered=uncovered_amount,
    )
    apply_amounts(req, breakdown, save=False)
    req.status = CoverageRequest.Status.ACCEPTED
    req.accepted_at = timezone.now()
    req.expires_at = timezone.now() + timedelta(days=30)
    req.rejection_reason = ""
    req.save()
    log_event(
        req,
        "accepted",
        (
            f"Acceptée — taux {breakdown.coverage_rate} %, "
            f"assurance {breakdown.insurance_amount} FCFA, "
            f"patient {breakdown.patient_amount} FCFA."
        ),
        actor=actor,
    )
    invoice = ensure_invoice(req)
    _notify_patient(
        req,
        f"Prise en charge acceptée — {req.reference}",
        (
            f"Votre demande a été validée par {req.provider.name}. "
            f"Part assurance : {req.insurance_amount} FCFA. "
            f"Reste à payer : {req.patient_amount} FCFA. "
            f"Facture {invoice.reference} disponible."
        ),
        ntype=Notification.Type.SUCCESS,
        extra={"invoice_id": invoice.id},
    )
    notify_pharmacy_coverage(req, event="new")
    req.pharmacy_notified_at = timezone.now()
    req.save(update_fields=["pharmacy_notified_at", "updated_at"])
    return req


@transaction.atomic
def reject_coverage(req: CoverageRequest, reason: str, actor=None) -> CoverageRequest:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Le motif du refus est obligatoire.")
    req.status = CoverageRequest.Status.REJECTED
    req.rejection_reason = reason
    req.insurance_amount = 0
    req.patient_amount = req.estimated_amount
    req.save(
        update_fields=[
            "status",
            "rejection_reason",
            "insurance_amount",
            "patient_amount",
            "updated_at",
        ]
    )
    log_event(req, "rejected", reason, actor=actor)
    _notify_patient(
        req,
        f"Prise en charge refusée — {req.reference}",
        f"Motif : {reason}",
        ntype=Notification.Type.WARNING,
    )
    return req


@transaction.atomic
def confirm_pharmacy_delivery(
    req: CoverageRequest,
    *,
    delivered_amount: int,
    lines: list[dict] | None = None,
    actor=None,
) -> CoverageRequest:
    """Recalcule parts assurance / patient après délivrance réelle."""
    if req.status not in {
        CoverageRequest.Status.ACCEPTED,
        CoverageRequest.Status.READY,
    }:
        raise ValueError("La demande doit être acceptée avant confirmation de délivrance.")
    delivered = max(0, int(delivered_amount))
    remaining = remaining_annual_cap(req.client, req.provider) + req.insurance_amount
    breakdown = calculate_coverage(
        total=delivered,
        coverage_rate=req.coverage_rate,
        remaining_cap=remaining,
        uncovered=req.uncovered_amount,
    )
    req.delivered_amount = delivered
    apply_amounts(req, breakdown, save=False)
    req.status = CoverageRequest.Status.READY
    req.prepared_at = timezone.now()
    req.save()
    log_event(
        req,
        "pharmacy_delivery",
        f"Montant délivré {delivered} FCFA — recalcul effectué.",
        actor=actor,
    )
    invoice = ensure_invoice(req, lines=lines, force_refresh=True)
    _notify_patient(
        req,
        f"Commande préparée — {req.reference}",
        (
            f"La pharmacie {req.pharmacy.name} a confirmé la délivrance. "
            f"Nouveau total : {delivered} FCFA. "
            f"Part assurance : {req.insurance_amount} FCFA. "
            f"Reste patient : {req.patient_amount} FCFA. "
            f"Facture {invoice.reference} mise à jour."
        ),
        ntype=Notification.Type.ORDER,
        extra={"invoice_id": invoice.id},
    )
    return req


def ensure_invoice(
    req: CoverageRequest,
    *,
    lines: list[dict] | None = None,
    force_refresh: bool = False,
) -> CoverageInvoice:
    settings_obj = PlatformPaymentSettings.load()
    total = req.total_amount
    commission = int(total * float(settings_obj.platform_commission_rate) / 100)
    payment_status = (
        CoverageInvoice.PaymentStatus.WAIVED
        if req.patient_amount <= 0
        else CoverageInvoice.PaymentStatus.PENDING
    )
    invoice, created = CoverageInvoice.objects.get_or_create(
        coverage_request=req,
        defaults={
            "reference": generate_fac_reference(),
            "total": total,
            "insurance_share": req.insurance_amount,
            "patient_share": req.patient_amount,
            "uncovered_share": req.uncovered_amount,
            "commission": commission,
            "payment_status": payment_status,
        },
    )
    if not created and (force_refresh or invoice.payment_status != CoverageInvoice.PaymentStatus.PAID):
        invoice.total = total
        invoice.insurance_share = req.insurance_amount
        invoice.patient_share = req.patient_amount
        invoice.uncovered_share = req.uncovered_amount
        invoice.commission = commission
        if invoice.payment_status != CoverageInvoice.PaymentStatus.PAID:
            invoice.payment_status = payment_status
        invoice.save()

    if lines is not None:
        invoice.lines.all().delete()
        for i, line in enumerate(lines):
            qty = max(1, int(line.get("quantity") or 1))
            unit = max(0, int(line.get("unit_price") or 0))
            CoverageInvoiceLine.objects.create(
                invoice=invoice,
                name=(line.get("name") or "Produit")[:200],
                quantity=qty,
                unit_price=unit,
                line_total=qty * unit,
                is_covered=bool(line.get("is_covered", True)),
                sort_order=i,
            )
    elif created and not invoice.lines.exists():
        CoverageInvoiceLine.objects.create(
            invoice=invoice,
            name="Ordonnance / médicaments",
            quantity=1,
            unit_price=total,
            line_total=total,
            is_covered=True,
        )

    if not invoice.pdf_file or force_refresh:
        build_invoice_pdf(invoice)
    return invoice


def build_invoice_pdf(invoice: CoverageInvoice) -> CoverageInvoice:
    req = invoice.coverage_request
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleGP",
        parent=styles["Heading1"],
        textColor=colors.HexColor("#015533"),
        fontSize=16,
    )
    body = []
    body.append(Paragraph("Gab'Pharma — Facture prise en charge", title))
    body.append(Spacer(1, 0.4 * cm))
    body.append(Paragraph(f"<b>Facture</b> {invoice.reference}", styles["Normal"]))
    body.append(Paragraph(f"<b>Prise en charge</b> {req.reference}", styles["Normal"]))
    body.append(
        Paragraph(
            f"<b>Patient</b> {req.client.get_full_name() or req.client.username}",
            styles["Normal"],
        )
    )
    body.append(Paragraph(f"<b>Assurance</b> {req.provider.name}", styles["Normal"]))
    body.append(Paragraph(f"<b>Pharmacie</b> {req.pharmacy.name}", styles["Normal"]))
    if req.doctor_name:
        body.append(Paragraph(f"<b>Médecin</b> {req.doctor_name}", styles["Normal"]))
    body.append(Spacer(1, 0.5 * cm))

    rows = [["Produit", "Qté", "P.U.", "Total", "Couvert"]]
    for line in invoice.lines.all():
        rows.append(
            [
                line.name,
                str(line.quantity),
                f"{line.unit_price} F",
                f"{line.line_total} F",
                "Oui" if line.is_covered else "Non",
            ]
        )
    if len(rows) == 1:
        rows.append(["Ordonnance", "1", f"{invoice.total} F", f"{invoice.total} F", "Oui"])
    table = Table(rows, colWidths=[7 * cm, 2 * cm, 3 * cm, 3 * cm, 2 * cm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#228545")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    body.append(table)
    body.append(Spacer(1, 0.6 * cm))
    summary = [
        ["Total", f"{invoice.total} FCFA"],
        ["Montant pris en charge", f"{invoice.insurance_share} FCFA"],
        ["Reste à payer", f"{invoice.patient_share} FCFA"],
        ["Commission Gab'Pharma", f"{invoice.commission} FCFA"],
        ["Statut paiement", invoice.get_payment_status_display()],
    ]
    st = Table(summary, colWidths=[8 * cm, 6 * cm])
    st.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    body.append(st)
    doc.build(body)
    invoice.pdf_file.save(
        f"{invoice.reference}.pdf",
        ContentFile(buffer.getvalue()),
        save=True,
    )
    return invoice


@transaction.atomic
def record_patient_payment(
    invoice: CoverageInvoice,
    *,
    method: str,
    reference: str = "",
    actor=None,
) -> CoverageInvoice:
    invoice.payment_method = method
    invoice.payment_reference = (reference or "").strip() or f"PAY-{invoice.reference}"
    invoice.payment_status = CoverageInvoice.PaymentStatus.PAID
    invoice.paid_at = timezone.now()
    invoice.save(
        update_fields=[
            "payment_method",
            "payment_reference",
            "payment_status",
            "paid_at",
            "updated_at",
        ]
    )
    req = invoice.coverage_request
    if req.status in {
        CoverageRequest.Status.ACCEPTED,
        CoverageRequest.Status.READY,
    }:
        req.status = CoverageRequest.Status.COMPLETED
        req.save(update_fields=["status", "updated_at"])
    log_event(
        req,
        "payment",
        (
            f"Paiement confirmé — {invoice.patient_share} FCFA "
            f"via {invoice.get_payment_method_display()} "
            f"(réf. {invoice.payment_reference})."
        ),
        actor=actor,
    )
    _notify_patient(
        req,
        f"Paiement confirmé — {invoice.reference}",
        (
            f"Votre paiement de {invoice.patient_share} FCFA a été enregistré. "
            f"Référence : {invoice.payment_reference}."
        ),
        ntype=Notification.Type.SUCCESS,
        extra={"invoice_id": invoice.id},
    )
    return invoice


def patient_insurance_context(user) -> dict:
    profile = ensure_client_profile(user)
    provider = profile.insurance_provider
    sub = active_subscription_for(user, provider) if provider else active_subscription_for(user)
    if sub and not provider:
        provider = sub.insurance_provider
    dependents = (
        InsuranceDependent.objects.filter(client_profile=profile, is_active=True)
        if profile
        else InsuranceDependent.objects.none()
    )
    rate = resolve_coverage_rate(user, provider) if provider else 0
    ceiling = None
    if provider:
        ceiling = remaining_annual_cap(user, provider)
    if profile.insurance_available_ceiling is not None:
        ceiling = profile.insurance_available_ceiling

    card_status = profile.insurance_card_status or ClientProfile.InsuranceCardStatus.ACTIVE
    starts = profile.insurance_starts_at or (sub.starts_at if sub else None)
    ends = profile.insurance_ends_at or (sub.ends_at if sub else None)
    if ends and ends < timezone.localdate():
        card_status = ClientProfile.InsuranceCardStatus.EXPIRED
    elif sub and sub.status == InsuranceMemberSubscription.Status.SUSPENDED:
        card_status = ClientProfile.InsuranceCardStatus.SUSPENDED
    elif sub and sub.status == InsuranceMemberSubscription.Status.ACTIVE:
        card_status = ClientProfile.InsuranceCardStatus.ACTIVE

    coverage_type = profile.insurance_coverage_type
    if not coverage_type and sub:
        coverage_type = sub.get_plan_display()
        if sub.subscription_plan_id:
            coverage_type = sub.subscription_plan.name

    return {
        "profile": profile,
        "provider": provider,
        "subscription": sub,
        "dependents": dependents,
        "coverage_rate": rate,
        "available_ceiling": ceiling,
        "card_status": card_status,
        "card_status_label": dict(ClientProfile.InsuranceCardStatus.choices).get(
            card_status, card_status
        ),
        "starts_at": starts,
        "ends_at": ends,
        "contract_number": profile.insurance_contract_number
        or (sub.reference if sub else ""),
        "insured_number": profile.insurance_number
        or (sub.reference if sub else ""),
        "coverage_type": coverage_type or "—",
        "has_insurance": bool(provider and (profile.insurance_number or sub)),
    }


def admin_coverage_stats() -> dict:
    qs = CoverageRequest.objects.all()
    return {
        "received": qs.count(),
        "accepted": qs.filter(status=CoverageRequest.Status.ACCEPTED).count()
        + qs.filter(status=CoverageRequest.Status.READY).count()
        + qs.filter(status=CoverageRequest.Status.COMPLETED).count(),
        "rejected": qs.filter(status=CoverageRequest.Status.REJECTED).count(),
        "pending": qs.filter(
            status__in=[
                CoverageRequest.Status.SENT,
                CoverageRequest.Status.UNDER_REVIEW,
                CoverageRequest.Status.INFO_NEEDED,
            ]
        ).count(),
        "total_coverage": qs.filter(
            status__in=[
                CoverageRequest.Status.ACCEPTED,
                CoverageRequest.Status.READY,
                CoverageRequest.Status.COMPLETED,
            ]
        ).aggregate(s=Sum("insurance_amount"))["s"]
        or 0,
        "patient_paid": CoverageInvoice.objects.filter(
            payment_status=CoverageInvoice.PaymentStatus.PAID
        ).aggregate(s=Sum("patient_share"))["s"]
        or 0,
        "insurer_due": qs.filter(
            status__in=[
                CoverageRequest.Status.ACCEPTED,
                CoverageRequest.Status.READY,
                CoverageRequest.Status.COMPLETED,
            ]
        ).aggregate(s=Sum("insurance_amount"))["s"]
        or 0,
        "invoices": CoverageInvoice.objects.count(),
    }
