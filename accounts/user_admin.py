"""Gestion admin des utilisateurs (CDC §3.1 — Gestion des utilisateurs)."""
from __future__ import annotations

import csv
import io
from typing import Iterable

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone

from accounts.mail import generate_temp_password, send_account_credentials
from accounts.models import ClientProfile, CourierProfile, User


def user_verification_state(user: User) -> str:
    """Libellé de vérification email/téléphone."""
    email_ok = user.is_email_verified
    phone_ok = user.is_phone_verified or not user.phone
    if email_ok and phone_ok:
        return "verified"
    if email_ok or phone_ok:
        return "partial"
    return "unverified"


def user_verification_label(user: User) -> str:
    labels = {
        "verified": "Vérifié",
        "partial": "Partiel",
        "unverified": "Non vérifié",
    }
    return labels[user_verification_state(user)]


def apply_user_status_change(user: User, new_status: str, *, previous_status: str | None = None):
    """
    Applique les effets de bord d'un changement de statut.
    Suspension pharmacie / livreur → cascade sur espaces dédiés (CDC).
    """
    from pharmacies.models import Pharmacy

    prev = previous_status or user.status
    if new_status == prev:
        return

    if new_status == User.Status.SUSPENDED:
        if user.role == User.Role.COURIER:
            profile, _ = CourierProfile.objects.get_or_create(user=user)
            profile.courier_status = CourierProfile.CourierStatus.SUSPENDED
            profile.save(update_fields=["courier_status"])
        elif user.role == User.Role.PHARMACIST:
            Pharmacy.objects.filter(owner=user).exclude(status=Pharmacy.Status.SUSPENDED).update(
                status=Pharmacy.Status.SUSPENDED
            )
    elif prev == User.Status.SUSPENDED and new_status == User.Status.ACTIVE:
        if user.role == User.Role.COURIER:
            profile, _ = CourierProfile.objects.get_or_create(user=user)
            if profile.courier_status == CourierProfile.CourierStatus.SUSPENDED:
                profile.courier_status = CourierProfile.CourierStatus.OFFLINE
                profile.save(update_fields=["courier_status"])
        elif user.role == User.Role.PHARMACIST:
            Pharmacy.objects.filter(owner=user, status=Pharmacy.Status.SUSPENDED).update(
                status=Pharmacy.Status.ACTIVE
            )


def export_users_csv(users: Iterable[User]) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="gabpharma-utilisateurs-{timezone.localdate():%Y%m%d}.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(
        [
            "Identifiant",
            "Prénom",
            "Nom",
            "E-mail",
            "Téléphone",
            "Rôle",
            "Statut",
            "E-mail vérifié",
            "Téléphone vérifié",
            "Ville",
            "Inscription",
        ]
    )
    for u in users:
        writer.writerow(
            [
                u.username,
                u.first_name,
                u.last_name,
                u.email,
                u.phone,
                u.get_role_display(),
                u.get_status_display(),
                "Oui" if u.is_email_verified else "Non",
                "Oui" if u.is_phone_verified else "Non",
                u.city,
                timezone.localtime(u.date_joined).strftime("%d/%m/%Y %H:%M"),
            ]
        )
    return response


def import_users_from_csv(file_obj, *, request=None) -> tuple[int, list[str]]:
    """
    Importe des utilisateurs depuis un CSV (séparateur ; ou ,).
    Colonnes : username, email, role, first_name, last_name, phone, city
    """
    raw = file_obj.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8-sig", errors="replace")
    delimiter = ";" if ";" in text.splitlines()[0] else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    created = 0
    errors: list[str] = []
    allowed = {
        User.Role.CLIENT,
        User.Role.PHARMACIST,
        User.Role.COURIER,
        User.Role.AUTHORITY,
        User.Role.SUPPORT,
        User.Role.REGIONAL_SUPERVISOR,
        User.Role.PARTNER,
    }
    with transaction.atomic():
        for i, row in enumerate(reader, start=2):
            username = (row.get("username") or row.get("identifiant") or "").strip()
            email = (row.get("email") or row.get("e-mail") or "").strip()
            role = (row.get("role") or User.Role.CLIENT).strip().lower()
            if not username or not email:
                errors.append(f"Ligne {i} : identifiant et e-mail obligatoires.")
                continue
            if User.objects.filter(username=username).exists():
                errors.append(f"Ligne {i} : identifiant « {username} » déjà utilisé.")
                continue
            if role not in allowed:
                errors.append(f"Ligne {i} : rôle « {role} » invalide.")
                continue
            plain = generate_temp_password()
            user = User.objects.create_user(
                username=username,
                email=email,
                password=plain,
                first_name=(row.get("first_name") or row.get("prenom") or "").strip(),
                last_name=(row.get("last_name") or row.get("nom") or "").strip(),
                phone=(row.get("phone") or row.get("telephone") or "").strip(),
                role=role,
                status=User.Status.PENDING if role == User.Role.CLIENT else User.Status.ACTIVE,
                city=(row.get("city") or row.get("ville") or "Libreville").strip(),
            )
            if role == User.Role.COURIER:
                CourierProfile.objects.get_or_create(user=user)
            elif role == User.Role.CLIENT:
                ClientProfile.objects.get_or_create(user=user)
            try:
                delivery = send_account_credentials(user, plain, request=request)
                if not delivery.ok:
                    errors.append(
                        f"Ligne {i} : compte créé mais e-mail non envoyé ({delivery.error})."
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Ligne {i} : compte créé mais e-mail non envoyé ({exc}).")
            created += 1
    return created, errors


def send_user_invitation(user: User, *, request=None) -> tuple[bool, str | None]:
    """Invitation par e-mail (compte en attente + mot de passe temporaire)."""
    if not user.email:
        return False, "Aucune adresse e-mail."
    plain = generate_temp_password()
    user.set_password(plain)
    if user.status not in {User.Status.ACTIVE, User.Status.PENDING}:
        user.status = User.Status.PENDING
    user.save(update_fields=["password", "status"])
    try:
        delivery = send_account_credentials(user, plain, request=request)
        if delivery.ok:
            return True, None
        return False, delivery.error
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
