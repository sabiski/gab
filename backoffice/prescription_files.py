"""Accès sécurisé aux fichiers d'ordonnance (aperçu iframe / consultation).

Les fichiers sensibles ne sont JAMAIS servis via /media/ en clair.
Lecture uniquement ici, après contrôle d'autorisation + déchiffrement Fernet.

Qui voit quoi :
- le patient : ses propres ordonnances ;
- le pharmacien : les ordonnances adressées à sa pharmacie (filigrane, pas de téléchargement) ;
- l'assureur : les ordonnances liées à une prise en charge chez lui (filigrane) ;
- l'équipe Gab'Pharma (admin / support) : uniquement via un accès exceptionnel motivé.
"""
from __future__ import annotations

import mimetypes
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin

from accounts.models import User
from backoffice.decorators import pharmacy_roles
from core.field_crypto import decrypt_bytes
from core.pharmacy_access import pharmacy_ids_for_user
from core.prescription_privacy import (
    BREAK_GLASS_MIN_REASON,
    break_glass_active,
    grant_break_glass,
    is_platform_staff,
    log_prescription_view,
    watermark_image,
    watermark_text,
)
from orders.models import Order, Prescription


SENSITIVE_CACHE = "no-store, no-cache, must-revalidate, private"


def _insurer_provider_id(user):
    from core.insurer_profile import resolve_insurer_profile

    try:
        profile = resolve_insurer_profile(user)
    except Exception:
        return None
    return getattr(profile, "insurance_provider_id", None)


def _insurer_can_view_order(user, order: Order) -> bool:
    provider_id = _insurer_provider_id(user)
    if not provider_id:
        return False
    from payments.models import InsuranceClaim

    return InsuranceClaim.objects.filter(order_id=order.pk, provider_id=provider_id).exists()


def _user_can_view_prescription(user, rx: Prescription) -> bool:
    if not user.is_authenticated:
        return False
    if rx.client_id == user.id:
        return True
    if is_platform_staff(user):
        return break_glass_active(user, prescription=rx)
    role = getattr(user, "role", None)
    if role in pharmacy_roles:
        pharmacy_ids = pharmacy_ids_for_user(user)
        if rx.pharmacy_id and rx.pharmacy_id in pharmacy_ids:
            return True
        if Order.objects.filter(linked_prescription=rx, pharmacy_id__in=pharmacy_ids).exists():
            return True
    if role == User.Role.PARTNER:
        provider_id = _insurer_provider_id(user)
        if provider_id and Order.objects.filter(
            linked_prescription=rx,
            insurance_claims__provider_id=provider_id,
        ).exists():
            return True
    return False


def _user_can_view_order(user, order: Order) -> bool:
    if not user.is_authenticated:
        return False
    if order.client_id == user.id:
        return True
    if is_platform_staff(user):
        return break_glass_active(user, order=order)
    role = getattr(user, "role", None)
    if role in pharmacy_roles and order.pharmacy_id in pharmacy_ids_for_user(user):
        return True
    if role == User.Role.PARTNER and _insurer_can_view_order(user, order):
        return True
    return False


def secure_prescription_response(request, field_file, *, filename=None, context="", owner_id=None):
    """Déchiffre et sert une ordonnance inline ; filigrane pour toute personne autre que le patient."""
    if not field_file or not field_file.name:
        raise Http404("Ordonnance supprimée ou introuvable.")
    try:
        with field_file.open("rb") as fh:
            raw = fh.read()
    except FileNotFoundError as exc:
        raise Http404("Fichier introuvable sur le serveur.") from exc

    try:
        plain = decrypt_bytes(raw)
    except Exception as exc:
        raise Http404("Fichier illisible ou clé de chiffrement incorrecte.") from exc

    # Nom d'affichage : retirer suffixe .enc éventuel
    name = filename or field_file.name.split("/")[-1]
    if name.endswith(".enc"):
        name = name[:-4]
    content_type, _ = mimetypes.guess_type(name)

    if owner_id is not None and request.user.pk != owner_id:
        marked = watermark_image(plain, watermark_text(request.user, context))
        if marked is not None:
            plain = marked

    buf = BytesIO(plain)
    buf.seek(0)
    response = FileResponse(buf, content_type=content_type or "application/octet-stream")
    response["Content-Disposition"] = f'inline; filename="{name}"'
    response["Cache-Control"] = SENSITIVE_CACHE
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


def _break_glass_redirect(request, kind, pk):
    url = reverse("bo_prescription_break_glass", args=[kind, pk])
    return redirect(f"{url}?next={request.get_full_path()}")


@login_required(login_url="login")
@xframe_options_sameorigin
def prescription_file_view(request, pk):
    rx = get_object_or_404(Prescription, pk=pk)
    if not _user_can_view_prescription(request.user, rx):
        if is_platform_staff(request.user):
            return _break_glass_redirect(request, "rx", rx.pk)
        raise Http404()
    log_prescription_view(request.user, prescription=rx, request=request)
    return secure_prescription_response(
        request,
        rx.file,
        filename=rx.file.name.split("/")[-1] if rx.file else None,
        context=f"Ordonnance #{rx.pk}",
        owner_id=rx.client_id,
    )


@login_required(login_url="login")
@xframe_options_sameorigin
def order_prescription_file_view(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    if not _user_can_view_order(request.user, order):
        if is_platform_staff(request.user):
            return _break_glass_redirect(request, "order", order.pk)
        raise Http404()
    if order.linked_prescription_id and order.linked_prescription.file:
        field_file, filename = order.linked_prescription.file, f"ordonnance-{order.code}.pdf"
        log_prescription_view(request.user, prescription=order.linked_prescription, request=request)
    elif order.prescription:
        field_file, filename = order.prescription, f"ordonnance-commande-{order.code}.pdf"
        log_prescription_view(request.user, order=order, request=request)
    else:
        raise Http404()
    return secure_prescription_response(
        request,
        field_file,
        filename=filename,
        context=f"Commande {order.code}",
        owner_id=order.client_id,
    )


BREAK_GLASS_TARGETS = {
    "rx": ("Ordonnance", "bo_prescription_file"),
    "order": ("Commande", "bo_order_prescription_file"),
    "coverage": ("Dossier de prise en charge", "bo_insurer_coverage_prescription"),
}


@login_required(login_url="login")
def prescription_break_glass(request, kind, pk):
    """Accès exceptionnel admin / support : motif obligatoire, durée limitée, patient notifié."""
    from django.conf import settings

    from payments.models import CoverageRequest

    if not is_platform_staff(request.user) or kind not in BREAK_GLASS_TARGETS:
        raise Http404()
    label, default_url_name = BREAK_GLASS_TARGETS[kind]
    target = {}
    if kind == "rx":
        obj = get_object_or_404(Prescription, pk=pk)
        target["prescription"] = obj
        title = f"{label} #{obj.pk}"
    elif kind == "order":
        obj = get_object_or_404(Order, pk=pk)
        target["order"] = obj
        title = f"{label} {obj.code}"
    else:
        obj = get_object_or_404(CoverageRequest, pk=pk)
        target["coverage_request"] = obj
        title = f"{label} {obj.reference}"

    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse(default_url_name, args=[pk])

    if request.method == "POST":
        try:
            grant_break_glass(request.user, request.POST.get("reason", ""), request=request, **target)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            return redirect(next_url)

    from backoffice.views import _ctx

    return render(
        request,
        "backoffice/admin/prescription_break_glass.html",
        _ctx(
            request,
            "prescription_break_glass",
            title=title,
            patient=obj.client,
            next_url=next_url,
            min_reason=BREAK_GLASS_MIN_REASON,
            duration=settings.PRESCRIPTION_BREAK_GLASS_MINUTES,
        ),
    )
