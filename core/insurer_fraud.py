"""Fraudes et alertes — portail compagnie d'assurance (persistant)."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone

from core.insurer_claims import claim_has_prescription, claim_insured_number, claims_base_queryset
from core.insurer_profile import InsurerPortalProfile
from payments.models import InsurerFraudAlert, InsuranceClaim

LEVELS_UI = {
    InsurerFraudAlert.Level.CRITICAL: ("Critique", "critical", "error"),
    InsurerFraudAlert.Level.HIGH: ("Élevé", "high", "warning"),
    InsurerFraudAlert.Level.MEDIUM: ("Moyen", "medium", "info"),
    InsurerFraudAlert.Level.LOW: ("Faible", "low", "success"),
}

STATUS_UI = {
    InsurerFraudAlert.Status.OPEN: ("En cours", "open"),
    InsurerFraudAlert.Status.ANALYSIS: ("En analyse", "analysis"),
    InsurerFraudAlert.Status.RESOLVED: ("Résolue", "resolved"),
}


def _holder_from_claim(claim, alert_type: str) -> tuple[str, str]:
    order = claim.order
    pharmacy = order.pharmacy if order else None
    client = claim.client
    holder = client.get_full_name() or client.username
    sub = claim_insured_number(claim)
    if pharmacy and alert_type in {
        InsurerFraudAlert.AlertType.OVERBILLING,
        InsurerFraudAlert.AlertType.DOCUMENT,
    }:
        holder = pharmacy.name
        sub = pharmacy.code or pharmacy.city or ""
    return holder, sub


def _upsert_alert(
    provider_id: int,
    claim: InsuranceClaim,
    alert_type: str,
    level: str,
    status: str,
    detail: str,
) -> tuple[InsurerFraudAlert, bool]:
    holder, sub = _holder_from_claim(claim, alert_type)
    reference = f"AL-{claim.created_at.year}-{claim.pk:04d}"
    if alert_type != InsurerFraudAlert.AlertType.SUSPICIOUS:
        suffix = alert_type[:3].upper()
        reference = f"AL-{claim.created_at.year}-{claim.pk:04d}-{suffix}"

    existing = InsurerFraudAlert.objects.filter(
        insurance_provider_id=provider_id,
        claim=claim,
        alert_type=alert_type,
    ).first()

    if existing:
        if existing.status != InsurerFraudAlert.Status.RESOLVED:
            existing.holder_name = holder
            existing.holder_sub = sub
            existing.detail = detail
            existing.level = level
            existing.save(update_fields=["holder_name", "holder_sub", "detail", "level", "updated_at"])
        return existing, False

    alert = InsurerFraudAlert.objects.create(
        insurance_provider_id=provider_id,
        claim=claim,
        reference=reference,
        alert_type=alert_type,
        level=level,
        status=status,
        holder_name=holder,
        holder_sub=sub,
        detail=detail,
        detected_at=timezone.localtime(claim.created_at),
    )
    return alert, True


def sync_fraud_alerts_for_provider(provider) -> int:
    from core.insurer_profile import InsurerPortalProfile

    profile = InsurerPortalProfile(
        organization_name=provider.name,
        acronym=provider.code,
        insurance_provider=provider,
        preview=False,
    )
    return sync_fraud_alerts(profile)


def sync_fraud_alerts(profile: InsurerPortalProfile) -> int:
    """Détecte et synchronise les alertes depuis les demandes réelles."""
    provider_id = profile.insurance_provider_id
    if not provider_id:
        return 0

    qs = claims_base_queryset(profile)
    now = timezone.now()
    cutoff_24h = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    created = 0

    for claim in qs.select_related("order", "order__pharmacy", "client"):
        if claim.status == InsuranceClaim.Status.REJECTED:
            alert, is_new = _upsert_alert(
                provider_id, claim,
                InsurerFraudAlert.AlertType.SUSPICIOUS,
                InsurerFraudAlert.Level.CRITICAL,
                InsurerFraudAlert.Status.ANALYSIS,
                claim.review_notes or "Demande refusée — motif à vérifier",
            )
            if is_new:
                from core.insurer_notifications import on_fraud_alert_created
                on_fraud_alert_created(alert)
                created += 1

        if claim.amount >= 100_000:
            alert, is_new = _upsert_alert(
                provider_id, claim,
                InsurerFraudAlert.AlertType.OVERBILLING,
                InsurerFraudAlert.Level.HIGH,
                InsurerFraudAlert.Status.OPEN,
                f"Montant élevé : {claim.amount:,} FCFA".replace(",", " "),
            )
            if is_new:
                from core.insurer_notifications import on_fraud_alert_created
                on_fraud_alert_created(alert)
                created += 1

        if claim.status == InsuranceClaim.Status.PENDING and claim.created_at < cutoff_24h:
            _upsert_alert(
                provider_id, claim,
                InsurerFraudAlert.AlertType.ABUSE,
                InsurerFraudAlert.Level.MEDIUM,
                InsurerFraudAlert.Status.OPEN,
                "Demande en attente depuis plus de 24 h",
            )

        if claim_has_prescription(claim) and claim.status == InsuranceClaim.Status.PENDING:
            _upsert_alert(
                provider_id, claim,
                InsurerFraudAlert.AlertType.DOCUMENT,
                InsurerFraudAlert.Level.MEDIUM,
                InsurerFraudAlert.Status.ANALYSIS,
                "Ordonnance en cours de vérification",
            )

    abuse_clients = (
        qs.filter(created_at__gte=week_ago)
        .values("client_id")
        .annotate(c=Count("id"))
        .filter(c__gte=3)
    )
    for row in abuse_clients:
        latest = qs.filter(client_id=row["client_id"]).order_by("-created_at").first()
        if latest:
            _upsert_alert(
                provider_id, latest,
                InsurerFraudAlert.AlertType.ABUSE,
                InsurerFraudAlert.Level.HIGH,
                InsurerFraudAlert.Status.OPEN,
                f"{row['c']} demandes en 7 jours — utilisation anormale",
            )

    return created


def alert_to_dict(alert: InsurerFraudAlert) -> dict:
    return {
        "id": alert.pk,
        "reference": alert.reference,
        "alert_type": alert.get_alert_type_display(),
        "alert_type_key": alert.alert_type,
        "holder_name": alert.holder_name,
        "holder_sub": alert.holder_sub,
        "detected_at": timezone.localtime(alert.detected_at),
        "level": LEVELS_UI.get(alert.level, ("—", "medium", "info")),
        "status": STATUS_UI.get(alert.status, ("—", "open")),
        "detail": alert.detail,
        "claim_id": alert.claim_id,
    }


def fraud_alerts_queryset(profile: InsurerPortalProfile, date_from=None, date_to=None):
    provider_id = profile.insurance_provider_id
    if not provider_id:
        return InsurerFraudAlert.objects.none()
    qs = InsurerFraudAlert.objects.filter(insurance_provider_id=provider_id)
    if date_from:
        qs = qs.filter(detected_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(detected_at__date__lte=date_to)
    return qs.select_related("claim", "claim__order", "claim__order__pharmacy")


def fraud_stats_from_qs(qs) -> dict:
    total = qs.count()
    critical = qs.filter(level=InsurerFraudAlert.Level.CRITICAL).count()
    analysis = qs.filter(status__in=[InsurerFraudAlert.Status.OPEN, InsurerFraudAlert.Status.ANALYSIS]).count()
    resolved = qs.filter(status=InsurerFraudAlert.Status.RESOLVED).count()

    def pct(n):
        return round((n / total) * 100, 1) if total else 0

    return {
        "total": total,
        "critical": critical,
        "analysis": analysis,
        "resolved": resolved,
        "pct_total": 100 if total else 0,
        "pct_critical": pct(critical),
        "pct_analysis": pct(analysis),
        "pct_resolved": pct(resolved),
    }


def fraud_apply_search_qs(qs, q: str):
    q = (q or "").strip()
    if not q:
        return qs
    return qs.filter(
        Q(reference__icontains=q)
        | Q(holder_name__icontains=q)
        | Q(holder_sub__icontains=q)
        | Q(detail__icontains=q)
        | Q(alert_type__icontains=q)
    )


def fraud_apply_filters_qs(qs, *, alert_type=None, level=None, status=None, zone=None):
    if alert_type:
        qs = qs.filter(alert_type=alert_type)
    if level:
        qs = qs.filter(level=level)
    if status:
        qs = qs.filter(status=status)
    if zone:
        z = zone.lower()
        qs = qs.filter(Q(holder_sub__icontains=z) | Q(holder_name__icontains=z))
    return qs


def fraud_charts_data_from_qs(qs) -> dict:
    type_counts: dict[str, int] = defaultdict(int)
    for alert in qs:
        type_counts[alert.get_alert_type_display()] += 1
    labels = list(type_counts.keys()) or ["Aucune alerte"]
    data = list(type_counts.values()) or [0]
    return {
        "type_split": {
            "labels": labels,
            "data": data,
            "colors": ["#ef4444", "#f59e0b", "#3b82f6", "#8b5cf6", "#9ca3af"][: len(labels)],
            "total": sum(data),
        },
        "recommendations": _recommendations_from_qs(qs),
    }


def _recommendations_from_qs(qs) -> list[dict]:
    recos = []
    doc_count = qs.filter(alert_type=InsurerFraudAlert.AlertType.DOCUMENT).count()
    over_count = qs.filter(alert_type=InsurerFraudAlert.AlertType.OVERBILLING).count()
    if doc_count:
        recos.append({
            "title": "Renforcer la vérification des pièces justificatives",
            "text": f"{doc_count} alerte(s) document — exiger des ordonnances lisibles.",
            "icon": "description",
        })
    if over_count:
        recos.append({
            "title": "Surveiller les pharmacies à risque",
            "text": f"{over_count} alerte(s) de sur-facturation détectée(s).",
            "icon": "local_pharmacy",
        })
    recos.append({
        "title": "Former les équipes",
        "text": "Sessions sur la détection des sur-facturations et abus.",
        "icon": "school",
    })
    return recos[:3]


def update_alert_status(alert_id: int, provider_id: int, new_status: str) -> bool:
    allowed = {s for s, _ in InsurerFraudAlert.Status.choices}
    if new_status not in allowed:
        return False
    updated = InsurerFraudAlert.objects.filter(
        pk=alert_id, insurance_provider_id=provider_id,
    ).update(
        status=new_status,
        resolved_at=timezone.now() if new_status == InsurerFraudAlert.Status.RESOLVED else None,
    )
    return updated > 0


def fraud_export_csv(qs) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Référence", "Type", "Assuré/Pharmacie", "Date", "Niveau", "Statut"])
    for a in qs:
        writer.writerow([
            a.reference,
            a.get_alert_type_display(),
            a.holder_name,
            timezone.localtime(a.detected_at).strftime("%d/%m/%Y %H:%M"),
            a.get_level_display(),
            a.get_status_display(),
        ])
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="alertes-{timezone.localdate().isoformat()}.csv"'
    return response
