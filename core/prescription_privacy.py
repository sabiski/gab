"""Confidentialité des ordonnances (données de santé — Loi n°025/2023).

Principes :
- seuls le patient, la pharmacie choisie et (pour une prise en charge) l'assureur
  concerné voient une ordonnance ;
- l'équipe Gab'Pharma (admin / support) n'y accède que par un accès exceptionnel
  motivé, limité dans le temps, journalisé et notifié au patient ;
- chaque consultation par un tiers est journalisée et visible par le patient ;
- les fichiers sont purgés après la durée de conservation ; seule l'empreinte
  SHA-256 reste comme preuve de vérification.
"""
from __future__ import annotations

import hashlib
import logging
import math
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

VIEW_LOG_DEDUP_MINUTES = 10
BREAK_GLASS_MIN_REASON = 15

FINAL_ORDER_STATUSES = {"delivered", "cancelled", "refunded"}
FINAL_COVERAGE_STATUSES = {"completed", "rejected", "expired"}


# ── Identité affichée au patient ───────────────────────────────────────────


def actor_label(user) -> str:
    if not user:
        return "Système"
    from accounts.models import User

    name = user.get_full_name() or user.username
    role = getattr(user, "role", "")
    if role == User.Role.PHARMACIST:
        from core.pharmacy_access import pharmacies_for_user

        pharmacy = pharmacies_for_user(user).first()
        return f"{pharmacy.name} ({name})" if pharmacy else f"Pharmacien ({name})"
    if role == User.Role.PARTNER:
        partner = getattr(user, "partner_profile", None)
        org = getattr(partner, "organization_name", "") if partner else ""
        return f"{org or 'Assureur'} ({name})"
    if role in (User.Role.ADMIN, User.Role.SUPERADMIN, User.Role.SUPPORT):
        return f"Équipe Gab'Pharma ({name})"
    return name


def is_platform_staff(user) -> bool:
    from accounts.models import User

    return getattr(user, "role", None) in (
        User.Role.ADMIN,
        User.Role.SUPERADMIN,
        User.Role.SUPPORT,
    )


def _ip(request):
    return request.META.get("REMOTE_ADDR") if request is not None else None


def _target_filter(*, prescription=None, order=None, coverage_request=None) -> dict:
    if prescription is not None:
        return {"prescription": prescription}
    if order is not None:
        return {"order": order}
    if coverage_request is not None:
        return {"coverage_request": coverage_request}
    raise ValueError("Cible d'ordonnance manquante.")


def _patient_of(*, prescription=None, order=None, coverage_request=None):
    target = prescription or order or coverage_request
    return target.client if target is not None else None


# ── Journal des consultations ──────────────────────────────────────────────


def log_prescription_view(user, *, prescription=None, order=None, coverage_request=None, request=None):
    """Journalise une consultation par un tiers (dédoublonnée sur 10 min)."""
    from orders.models import PrescriptionAccessLog

    patient = _patient_of(prescription=prescription, order=order, coverage_request=coverage_request)
    if not user or (patient and patient.pk == user.pk):
        return None
    target = _target_filter(prescription=prescription, order=order, coverage_request=coverage_request)
    since = timezone.now() - timedelta(minutes=VIEW_LOG_DEDUP_MINUTES)
    if PrescriptionAccessLog.objects.filter(
        user=user,
        action=PrescriptionAccessLog.Action.VIEW,
        created_at__gte=since,
        **target,
    ).exists():
        return None
    return PrescriptionAccessLog.objects.create(
        user=user,
        actor_label=actor_label(user),
        action=PrescriptionAccessLog.Action.VIEW,
        ip_address=_ip(request),
        **target,
    )


# ── Accès exceptionnel (break-glass) ───────────────────────────────────────


def break_glass_active(user, *, prescription=None, order=None, coverage_request=None) -> bool:
    from orders.models import PrescriptionAccessLog

    if not user or not getattr(user, "is_authenticated", False):
        return False
    since = timezone.now() - timedelta(minutes=settings.PRESCRIPTION_BREAK_GLASS_MINUTES)
    qs = PrescriptionAccessLog.objects.filter(
        user=user,
        action=PrescriptionAccessLog.Action.BREAK_GLASS,
        created_at__gte=since,
    )
    if order is not None and order.linked_prescription_id:
        from django.db.models import Q

        return qs.filter(Q(order=order) | Q(prescription_id=order.linked_prescription_id)).exists()
    target = _target_filter(prescription=prescription, order=order, coverage_request=coverage_request)
    return qs.filter(**target).exists()


def grant_break_glass(user, reason: str, *, prescription=None, order=None, coverage_request=None, request=None):
    """Ouvre un accès exceptionnel motivé : journalisé, audité, notifié au patient."""
    from notifications.models import AuditLog, Notification
    from notifications.services import notify_user
    from orders.models import PrescriptionAccessLog

    reason = (reason or "").strip()
    if len(reason) < BREAK_GLASS_MIN_REASON:
        raise ValueError(
            f"Motif obligatoire ({BREAK_GLASS_MIN_REASON} caractères minimum) : "
            "litige, réclamation du patient, réquisition…"
        )
    target = _target_filter(prescription=prescription, order=order, coverage_request=coverage_request)
    log = PrescriptionAccessLog.objects.create(
        user=user,
        actor_label=actor_label(user),
        action=PrescriptionAccessLog.Action.BREAK_GLASS,
        reason=reason,
        ip_address=_ip(request),
        **target,
    )
    ref = (
        f"ordonnance #{prescription.pk}" if prescription is not None
        else f"commande {order.code}" if order is not None
        else f"dossier {coverage_request.reference}"
    )
    AuditLog.objects.create(
        user=user,
        action="Accès exceptionnel ordonnance",
        module="privacy",
        details=f"{ref} — motif : {reason}"[:2000],
        ip_address=_ip(request),
        is_sensitive=True,
    )
    patient = _patient_of(prescription=prescription, order=order, coverage_request=coverage_request)
    if patient:
        notify_user(
            patient,
            "Accès exceptionnel à votre ordonnance",
            (
                f"Un membre de l'équipe Gab'Pharma a consulté votre {ref} le "
                f"{timezone.localtime().strftime('%d/%m/%Y à %H:%M')}. Motif : {reason}. "
                "Contactez le support si vous n'êtes pas à l'origine de cette démarche."
            ),
            notification_type=Notification.Type.WARNING,
            transactional=True,
        )
    return log


# ── Filigrane ──────────────────────────────────────────────────────────────


def watermark_text(user, context: str) -> str:
    stamp = timezone.localtime().strftime("%d/%m/%Y %H:%M")
    return f"{context} · {actor_label(user)} · {stamp} · Usage exclusif — ne pas diffuser"


def watermark_image(data: bytes, text: str) -> bytes | None:
    """Filigrane diagonal répété sur une image. None si le format n'est pas une image."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    try:
        base = Image.open(BytesIO(data))
        fmt = (base.format or "PNG").upper()
        base = base.convert("RGBA")
    except Exception:
        return None

    w, h = base.size
    size = max(14, int(min(w, h) / 28))
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()

    diag = int(math.hypot(w, h))
    layer = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    step_y = size * 5
    for row, y in enumerate(range(0, diag, step_y)):
        offset = (row % 2) * size * 6
        draw.text((offset - size * 6, y), ("   " + text) * 4, font=font, fill=(200, 30, 30, 70))
    layer = layer.rotate(30, resample=Image.BICUBIC)
    left, top = (diag - w) // 2, (diag - h) // 2
    layer = layer.crop((left, top, left + w, top + h))
    out = Image.alpha_composite(base, layer)

    buf = BytesIO()
    if fmt in ("JPEG", "JPG"):
        out.convert("RGB").save(buf, "JPEG", quality=88)
    else:
        out.save(buf, "PNG")
    return buf.getvalue()


# ── Purge (durée de conservation) ──────────────────────────────────────────


def plaintext_sha256(field_file) -> str:
    from core.field_crypto import decrypt_bytes

    if not field_file or not field_file.name:
        return ""
    try:
        with field_file.open("rb") as fh:
            raw = fh.read()
        return hashlib.sha256(decrypt_bytes(raw)).hexdigest()
    except Exception:
        logger.warning("Empreinte impossible pour %s", field_file.name, exc_info=True)
        return ""


def _still_referenced(name: str) -> bool:
    from orders.models import Order, Prescription
    from payments.models import CoverageRequest

    return (
        Prescription.objects.filter(file=name, purged_at__isnull=True).exists()
        or Order.objects.filter(prescription=name).exists()
        or CoverageRequest.objects.filter(prescription_file=name).exists()
    )


def _delete_if_unreferenced(storage, name: str):
    if name and not _still_referenced(name):
        try:
            storage.delete(name)
        except Exception:
            logger.exception("Suppression fichier ordonnance échouée : %s", name)


def purge_prescription(rx) -> None:
    from orders.models import PrescriptionAccessLog

    if not rx.file:
        return
    name, storage = rx.file.name, rx.file.storage
    rx.file_sha256 = rx.file_sha256 or plaintext_sha256(rx.file)
    rx.file = ""
    rx.purged_at = timezone.now()
    rx.save(update_fields=["file", "file_sha256", "purged_at"])
    PrescriptionAccessLog.objects.create(
        prescription=rx,
        actor_label="Système",
        action=PrescriptionAccessLog.Action.PURGE,
        reason="Durée de conservation dépassée",
    )
    _delete_if_unreferenced(storage, name)


def purge_order_copy(order) -> None:
    if not order.prescription:
        return
    name, storage = order.prescription.name, order.prescription.storage
    order.prescription = None
    order.save(update_fields=["prescription", "updated_at"])
    _delete_if_unreferenced(storage, name)


def purge_coverage_prescription(req) -> None:
    from payments.models import CoverageAccessLog

    if not req.prescription_file:
        return
    name, storage = req.prescription_file.name, req.prescription_file.storage
    req.prescription_sha256 = req.prescription_sha256 or plaintext_sha256(req.prescription_file)
    req.prescription_file = None
    req.prescription_purged_at = timezone.now()
    req.save(update_fields=["prescription_file", "prescription_sha256", "prescription_purged_at", "updated_at"])
    CoverageAccessLog.objects.create(
        coverage_request=req,
        action=CoverageAccessLog.Action.PURGE,
        details="Durée de conservation dépassée — empreinte conservée",
    )
    _delete_if_unreferenced(storage, name)


def prescriptions_due_for_purge(now=None):
    """Ordonnances du dossier patient arrivées en fin de conservation."""
    from orders.models import Prescription

    now = now or timezone.now()
    used_cutoff = now - timedelta(days=settings.PRESCRIPTION_RETENTION_DAYS)
    unused_cutoff = now - timedelta(days=settings.PRESCRIPTION_UNUSED_RETENTION_DAYS)
    due = []
    qs = Prescription.objects.filter(purged_at__isnull=True).exclude(file="")
    for rx in qs.iterator():
        orders = list(rx.orders.values_list("status", "updated_at"))
        if orders:
            if all(st in FINAL_ORDER_STATUSES for st, _ in orders) and max(
                u for _, u in orders
            ) < used_cutoff:
                due.append(rx)
        elif rx.status == Prescription.Status.REJECTED:
            if (rx.reviewed_at or rx.created_at) < used_cutoff:
                due.append(rx)
        elif rx.created_at < unused_cutoff:
            due.append(rx)
    return due


def run_purge(now=None, *, dry_run=False) -> dict:
    from orders.models import Order
    from payments.models import CoverageRequest

    now = now or timezone.now()
    used_cutoff = now - timedelta(days=settings.PRESCRIPTION_RETENTION_DAYS)
    coverage_cutoff = now - timedelta(days=settings.COVERAGE_PRESCRIPTION_RETENTION_DAYS)

    rxs = prescriptions_due_for_purge(now)
    orders = list(
        Order.objects.filter(status__in=FINAL_ORDER_STATUSES, updated_at__lt=used_cutoff)
        .exclude(prescription="")
        .exclude(prescription__isnull=True)
    )
    coverage = list(
        CoverageRequest.objects.filter(
            status__in=FINAL_COVERAGE_STATUSES,
            updated_at__lt=coverage_cutoff,
            prescription_purged_at__isnull=True,
        )
        .exclude(prescription_file="")
        .exclude(prescription_file__isnull=True)
    )
    if not dry_run:
        for order in orders:
            purge_order_copy(order)
        for rx in rxs:
            purge_prescription(rx)
        for req in coverage:
            purge_coverage_prescription(req)
    return {"prescriptions": len(rxs), "order_copies": len(orders), "coverage": len(coverage)}
