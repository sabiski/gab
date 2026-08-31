"""Demandes de prise en charge — portail assureur (liées commande / patient / pharmacie)."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from core.insurer_profile import InsurerPortalProfile
from payments.models import InsuranceClaim


def claims_base_queryset(profile: InsurerPortalProfile):
    if not profile.insurance_provider_id:
        return InsuranceClaim.objects.none()
    return (
        InsuranceClaim.objects.filter(provider_id=profile.insurance_provider_id)
        .select_related(
            "order",
            "order__pharmacy",
            "order__linked_prescription",
            "client",
            "client__client_profile",
            "provider",
        )
        .order_by("-created_at")
    )


def claims_apply_search(qs, q: str):
    q = (q or "").strip()
    if not q:
        return qs
    return qs.filter(
        Q(order__code__icontains=q)
        | Q(client__first_name__icontains=q)
        | Q(client__last_name__icontains=q)
        | Q(client__email__icontains=q)
        | Q(client__client_profile__insurance_number__icontains=q)
        | Q(order__pharmacy__name__icontains=q)
        | Q(order__pharmacy__city__icontains=q)
        | Q(order__pharmacy__code__icontains=q)
    )


def claims_apply_filters(qs, *, pharmacy_id=None, overdue_only=False):
    if pharmacy_id:
        try:
            qs = qs.filter(order__pharmacy_id=int(pharmacy_id))
        except (TypeError, ValueError):
            pass
    if overdue_only:
        cutoff = timezone.now() - timedelta(hours=24)
        qs = qs.filter(status=InsuranceClaim.Status.PENDING, created_at__lt=cutoff)
    return qs


def claims_stats(profile: InsurerPortalProfile) -> dict:
    base = claims_base_queryset(profile)
    total = base.count()
    pending = base.filter(status=InsuranceClaim.Status.PENDING).count()
    approved = base.filter(status=InsuranceClaim.Status.APPROVED).count()
    paid = base.filter(status=InsuranceClaim.Status.PAID).count()
    rejected = base.filter(status=InsuranceClaim.Status.REJECTED).count()
    validated = approved + paid

    def pct(n):
        return round((n / total) * 100, 1) if total else 0

    cutoff = timezone.now() - timedelta(hours=24)
    overdue_pending = base.filter(
        status=InsuranceClaim.Status.PENDING, created_at__lt=cutoff
    ).count()

    return {
        "total": total,
        "pending": pending,
        "validated": validated,
        "approved": approved,
        "paid": paid,
        "rejected": rejected,
        "pct_total": 100 if total else 0,
        "pct_pending": pct(pending),
        "pct_validated": pct(validated),
        "pct_rejected": pct(rejected),
        "overdue_pending": overdue_pending,
    }


def claim_display_reference(claim: InsuranceClaim) -> str:
    order = claim.order
    if order and order.code:
        code = order.code
        if not code.upper().startswith("DP-"):
            return f"DP-{code}"
        return code
    return f"DP-{claim.pk:06d}"


def claim_has_prescription(claim: InsuranceClaim) -> bool:
    order = claim.order
    if not order:
        return False
    if order.prescription:
        return True
    if order.linked_prescription_id and order.linked_prescription.file:
        return True
    return False


def claim_prescription_url(claim: InsuranceClaim) -> str:
    order = claim.order
    if not order:
        return ""
    if order.prescription:
        return order.prescription.url
    if order.linked_prescription_id and order.linked_prescription.file:
        return order.linked_prescription.file.url
    return ""


def claim_insured_number(claim: InsuranceClaim) -> str:
    profile = getattr(claim.client, "client_profile", None)
    if profile and profile.insurance_number:
        return profile.insurance_number
    return "—"


def claim_status_ui(claim: InsuranceClaim) -> dict:
    mapping = {
        InsuranceClaim.Status.PENDING: ("En attente", "pending", "schedule"),
        InsuranceClaim.Status.APPROVED: ("Validée", "approved", "check_circle"),
        InsuranceClaim.Status.PAID: ("Payée", "paid", "payments"),
        InsuranceClaim.Status.REJECTED: ("Refusée", "rejected", "cancel"),
    }
    label, tone, icon = mapping.get(claim.status, (claim.get_status_display(), "pending", "help"))
    return {"label": label, "tone": tone, "icon": icon}


def partner_pharmacies_for_claims(profile: InsurerPortalProfile):
    from pharmacies.models import Pharmacy

    qs = claims_base_queryset(profile).filter(order__pharmacy_id__isnull=False)
    ids = qs.values_list("order__pharmacy_id", flat=True).distinct()
    return Pharmacy.objects.filter(pk__in=ids).order_by("name")


def get_claim_for_profile(profile: InsurerPortalProfile, claim_id: int) -> InsuranceClaim:
    from django.shortcuts import get_object_or_404

    return get_object_or_404(
        claims_base_queryset(profile)
        .prefetch_related("order__items", "order__items__medicine", "order__linked_prescription"),
        pk=claim_id,
    )


def build_claim_detail(claim: InsuranceClaim) -> dict:
    order = claim.order
    client = claim.client
    client_profile = getattr(client, "client_profile", None)
    pharmacy = order.pharmacy if order else None
    provider = claim.provider
    rx = order.linked_prescription if order and order.linked_prescription_id else None

    items = []
    if order:
        for idx, line in enumerate(order.items.all(), start=1):
            med = line.medicine
            form_label = med.get_form_display() if med else ""
            dosage = med.dosage if med else ""
            form_dosage = " / ".join(p for p in (form_label, dosage) if p) or "—"
            items.append(
                {
                    "index": idx,
                    "name": line.medicine_name,
                    "form_dosage": form_dosage,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "line_total": line.line_total,
                }
            )

    from core.insurance import order_gross_before_insurance, quote_insurance

    gross_total = order_gross_before_insurance(order) if order else claim.amount
    coverage_rate = provider.coverage_rate if provider else 80
    quote = quote_insurance(client, provider, gross_total) if provider and order else None
    if quote and quote.ok:
        max_coverage = quote.coverage_amount
        coverage_ok = claim.amount <= max_coverage
        coverage_rate = quote.coverage_rate
    else:
        max_coverage = min(
            claim.amount,
            int(gross_total * coverage_rate / 100) if gross_total else claim.amount,
        )
        coverage_ok = claim.amount <= max_coverage

    timeline = [
        {
            "label": "Demande reçue",
            "meta": timezone.localtime(claim.created_at).strftime("%d/%m/%Y %H:%M"),
            "state": "done",
        },
        {
            "label": "En cours d'analyse",
            "meta": "Traitement par l'assureur",
            "state": "current"
            if claim.status == InsuranceClaim.Status.PENDING
            else "done",
        },
        {
            "label": "Décision",
            "meta": claim.get_status_display() if claim.status != InsuranceClaim.Status.PENDING else "En attente",
            "state": "done"
            if claim.status
            in {
                InsuranceClaim.Status.APPROVED,
                InsuranceClaim.Status.REJECTED,
                InsuranceClaim.Status.PAID,
            }
            else "pending",
        },
        {
            "label": "Paiement",
            "meta": "Versement au bénéficiaire / pharmacie",
            "state": "done" if claim.status == InsuranceClaim.Status.PAID else "pending",
        },
    ]

    history = [
        {
            "text": "Demande créée à la validation de la commande",
            "meta": timezone.localtime(claim.created_at).strftime("%d/%m/%Y %H:%M"),
        },
    ]
    if order and pharmacy:
        history.append(
            {
                "text": f"Commande {order.code} — {pharmacy.name}",
                "meta": timezone.localtime(order.created_at).strftime("%d/%m/%Y %H:%M"),
            }
        )
    if claim.status != InsuranceClaim.Status.PENDING:
        history.append(
            {
                "text": f"Décision : {claim.get_status_display()}",
                "meta": timezone.localtime(claim.updated_at).strftime("%d/%m/%Y %H:%M"),
            }
        )

    hours_left = 24
    if claim.status == InsuranceClaim.Status.PENDING:
        deadline = claim.created_at + timedelta(hours=24)
        remaining = deadline - timezone.now()
        hours_left = max(0, int(remaining.total_seconds() // 3600))

    client_age = None
    if client_profile and client_profile.date_of_birth:
        today = timezone.localdate()
        dob = client_profile.date_of_birth
        client_age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    year = timezone.localdate().year
    plan_label = "Santé Plus" if coverage_rate >= 100 else "Essentiel" if coverage_rate >= 80 else "Base"

    if rx and rx.doctor_name:
        prescriber_label = rx.doctor_name
    elif rx:
        prescriber_label = "—"
    else:
        prescriber_label = "—"

    rx_reason = "—"
    if rx and rx.notes:
        rx_reason = rx.notes
    elif order and order.notes:
        rx_reason = order.notes

    documents = []
    if rx and rx.file:
        documents.append(
            {"label": "Ordonnance", "url": rx.file.url, "icon": "medication", "thumb": rx.file.url}
        )
    elif order and order.prescription:
        documents.append(
            {
                "label": "Ordonnance",
                "url": order.prescription.url,
                "icon": "medication",
                "thumb": order.prescription.url,
            }
        )
    if client.avatar:
        documents.append(
            {"label": "Pièce d'identité", "url": client.avatar.url, "icon": "badge", "thumb": client.avatar.url}
        )
    documents.append({"label": "Carte d'assuré", "url": "", "icon": "credit_card", "thumb": ""})

    request_history = [
        {
            "label": "Demande reçue",
            "meta": timezone.localtime(claim.created_at).strftime("%d/%m/%Y %H:%M"),
            "state": "done",
        },
        {
            "label": "En cours d'analyse",
            "meta": "Traitement par l'assureur",
            "state": "current" if claim.status == InsuranceClaim.Status.PENDING else "done",
        },
        {
            "label": "Décision",
            "meta": claim.get_status_display() if claim.status != InsuranceClaim.Status.PENDING else "En attente",
            "state": "done"
            if claim.status
            in {
                InsuranceClaim.Status.APPROVED,
                InsuranceClaim.Status.REJECTED,
                InsuranceClaim.Status.PAID,
            }
            else "pending",
        },
    ]

    return {
        "reference": claim_display_reference(claim),
        "status": claim_status_ui(claim),
        "received_at": timezone.localtime(claim.created_at),
        "request_type": "Ordonnance" if claim_has_prescription(claim) else "Commande",
        "amount": claim.amount,
        "gross_total": gross_total,
        "max_coverage": max_coverage,
        "coverage_rate": coverage_rate,
        "coverage_ok": coverage_ok,
        "coverage_hint": "Le contrat couvre cette demande" if coverage_ok else "Vérifier le plafond de garantie",
        "client_name": client.get_full_name() or client.username,
        "client_age": client_age,
        "plan_label": plan_label,
        "contract_valid_from": f"01/01/{year}",
        "contract_valid_to": f"31/12/{year}",
        "client_email": client.email,
        "client_phone": client.phone or "—",
        "client_dob": client_profile.date_of_birth if client_profile and client_profile.date_of_birth else None,
        "insured_number": claim_insured_number(claim),
        "pharmacy_name": pharmacy.name if pharmacy else "—",
        "pharmacy_address": pharmacy.address if pharmacy else "—",
        "pharmacy_city": pharmacy.city if pharmacy else "—",
        "pharmacy_code": pharmacy.code if pharmacy else "—",
        "pharmacy_phone": pharmacy.phone if pharmacy else "—",
        "provider_name": provider.name if provider else "—",
        "provider_code": provider.code if provider else "—",
        "contract_label": f"Contrat {provider.name}" if provider else "Contrat actif",
        "rx_date": rx.created_at if rx else (order.created_at if order else claim.created_at),
        "prescriber": prescriber_label,
        "prescription_ref": f"ORD-{rx.pk}" if rx else "—",
        "rx_reason": rx_reason,
        "consultation_reason": rx_reason if rx_reason != "—" else (order.notes if order and order.notes else "—"),
        "pharmacy_comment": order.notes if order and order.notes else "—",
        "items": items,
        "items_total": sum(i["line_total"] for i in items) or claim.amount,
        "documents": documents,
        "timeline": timeline,
        "request_history": request_history,
        "history": history,
        "review_notes": claim.review_notes,
        "review_attachment_url": claim.review_attachment.url if claim.review_attachment else "",
        "hours_left": hours_left,
        "can_decide": claim.status == InsuranceClaim.Status.PENDING,
    }
