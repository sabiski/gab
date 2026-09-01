"""Authentification à deux facteurs (OTP e-mail / SMS) — connexion backoffice."""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import AuthorityProfile, PlatformSettings
from core.email_utils import deliver_email
from notifications.providers.sms import send_sms_notification

logger = logging.getLogger("gabpharma.2fa")

SESSION_USER_KEY = "2fa_pending_user_id"
SESSION_CODE_HASH_KEY = "2fa_code_hash"
SESSION_EXPIRES_KEY = "2fa_expires_at"
SESSION_METHOD_KEY = "2fa_method"
SESSION_NEXT_KEY = "2fa_next_url"
SESSION_ATTEMPTS_KEY = "2fa_attempts"
SESSION_FLASH_KEY = "2fa_flash"

CODE_TTL_MINUTES = 10


def two_factor_enabled() -> bool:
    return PlatformSettings.load().two_factor_required


def resolve_method(user) -> str:
    if user.role == user.Role.AUTHORITY:
        profile = getattr(user, "authority_profile", None)
        if profile:
            return profile.two_factor_method
    if user.email:
        return AuthorityProfile.TwoFactorMethod.EMAIL
    if user.phone:
        return AuthorityProfile.TwoFactorMethod.SMS
    return AuthorityProfile.TwoFactorMethod.EMAIL


def _hash_code(user_id: int, code: str) -> str:
    raw = f"gabpharma-2fa-v1:{user_id}:{code}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = f"{local[0]}***"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def _mask_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


@dataclass
class SendResult:
    ok: bool
    method: str = ""
    destination_masked: str = ""
    error: str = ""


def clear_pending(request) -> None:
    for key in (
        SESSION_USER_KEY,
        SESSION_CODE_HASH_KEY,
        SESSION_EXPIRES_KEY,
        SESSION_METHOD_KEY,
        SESSION_NEXT_KEY,
        SESSION_ATTEMPTS_KEY,
        SESSION_FLASH_KEY,
    ):
        request.session.pop(key, None)


def get_pending_user(request):
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    User = get_user_model()
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        clear_pending(request)
        return None


def _send_code(user, code: str, method: str) -> SendResult:
    platform = PlatformSettings.load()
    subject = f"{platform.platform_name} — Code de vérification"
    body = (
        f"Votre code de connexion {platform.platform_name} est : {code}\n\n"
        f"Il expire dans {CODE_TTL_MINUTES} minutes.\n"
        "Ne partagez ce code avec personne."
    )

    if method == AuthorityProfile.TwoFactorMethod.SMS:
        ok, err = send_sms_notification(
            user=user,
            title="Connexion",
            message=f"Votre code : {code} (valide {CODE_TTL_MINUTES} min)",
        )
        if ok:
            print(
                f"\n[Gab'Pharma 2FA] Code SMS pour {user.phone} : {code}\n",
                flush=True,
            )
            return SendResult(
                ok=True,
                method="sms",
                destination_masked=_mask_phone(user.phone or ""),
            )
        if user.email:
            result = _send_email_code(user, subject, body, code)
            if result.ok:
                result.error = ""
            return result
        return SendResult(ok=False, method="sms", error=err or "Envoi SMS impossible.")

    return _send_email_code(user, subject, body, code)


def _send_email_code(user, subject: str, body: str, code: str) -> SendResult:
    if not user.email:
        return SendResult(ok=False, method="email", error="Aucune adresse e-mail configurée.")
    delivery = deliver_email(
        subject=subject,
        body=body,
        recipient_list=[user.email],
    )
    if not delivery.ok:
        return SendResult(ok=False, method="email", error=delivery.user_message)
    print(f"\n[Gab'Pharma 2FA] Code pour {user.email} : {code}\n", flush=True)
    return SendResult(
        ok=True,
        method="email",
        destination_masked=_mask_email(user.email),
    )


def start_pending_login(request, user, *, next_url: str = "") -> SendResult:
    """Après mot de passe valide : session 2FA en attente + envoi du code."""
    clear_pending(request)
    code = _generate_code()
    method = resolve_method(user)
    request.session[SESSION_USER_KEY] = user.pk
    request.session[SESSION_CODE_HASH_KEY] = _hash_code(user.pk, code)
    request.session[SESSION_EXPIRES_KEY] = (
        timezone.now() + timezone.timedelta(minutes=CODE_TTL_MINUTES)
    ).isoformat()
    request.session[SESSION_METHOD_KEY] = method
    request.session[SESSION_NEXT_KEY] = next_url or ""
    request.session[SESSION_ATTEMPTS_KEY] = 0
    request.session.modified = True

    result = _send_code(user, code, method)
    logger.info("2FA code envoyé user_id=%s via %s", user.pk, method)
    return result


def resend_code(request) -> SendResult:
    user = get_pending_user(request)
    if not user:
        return SendResult(ok=False, error="Session expirée. Reconnectez-vous.")
    code = _generate_code()
    method = request.session.get(SESSION_METHOD_KEY) or resolve_method(user)
    request.session[SESSION_CODE_HASH_KEY] = _hash_code(user.pk, code)
    request.session[SESSION_EXPIRES_KEY] = (
        timezone.now() + timezone.timedelta(minutes=CODE_TTL_MINUTES)
    ).isoformat()
    request.session[SESSION_ATTEMPTS_KEY] = 0
    request.session.modified = True
    return _send_code(user, code, method)


def pending_context(request) -> dict:
    user = get_pending_user(request)
    if not user:
        return {}
    method = request.session.get(SESSION_METHOD_KEY, "email")
    if method == AuthorityProfile.TwoFactorMethod.SMS:
        masked = _mask_phone(user.phone or "")
        channel_label = "SMS"
    else:
        masked = _mask_email(user.email or "")
        channel_label = "e-mail"
    return {
        "method": method,
        "channel_label": channel_label,
        "destination_masked": masked,
        "ttl_minutes": CODE_TTL_MINUTES,
    }


@dataclass
class VerifyResult:
    ok: bool
    user: Any = None
    next_url: str = ""
    error: str = ""
    locked: bool = False


def verify_code(request, code: str) -> VerifyResult:
    user = get_pending_user(request)
    if not user:
        return VerifyResult(ok=False, error="Session expirée. Reconnectez-vous.")

    max_attempts = PlatformSettings.load().login_attempt_limit or 5
    attempts = int(request.session.get(SESSION_ATTEMPTS_KEY, 0))

    expires_raw = request.session.get(SESSION_EXPIRES_KEY)
    if expires_raw:
        expires = timezone.datetime.fromisoformat(expires_raw)
        if timezone.is_naive(expires):
            expires = timezone.make_aware(expires)
        if timezone.now() > expires:
            clear_pending(request)
            return VerifyResult(ok=False, error="Code expiré. Reconnectez-vous.")

    entered = "".join(c for c in (code or "") if c.isdigit())
    if len(entered) != 6:
        return VerifyResult(ok=False, error="Saisissez les 6 chiffres du code.")

    expected_hash = request.session.get(SESSION_CODE_HASH_KEY, "")
    if _hash_code(user.pk, entered) != expected_hash:
        attempts += 1
        request.session[SESSION_ATTEMPTS_KEY] = attempts
        request.session.modified = True
        if attempts >= max_attempts:
            clear_pending(request)
            return VerifyResult(
                ok=False,
                error="Trop de tentatives. Reconnectez-vous.",
                locked=True,
            )
        remaining = max_attempts - attempts
        return VerifyResult(
            ok=False,
            error=f"Code incorrect. {remaining} tentative(s) restante(s).",
        )

    next_url = request.session.get(SESSION_NEXT_KEY, "")
    clear_pending(request)
    return VerifyResult(ok=True, user=user, next_url=next_url)
