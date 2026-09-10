"""Réinitialisation de mot de passe — lien sécurisé par e-mail."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from accounts.models import User
from core.email_utils import MailDeliveryResult, deliver_email, uses_console_email

logger = logging.getLogger("gabpharma.mail")

GENERIC_SENT_MESSAGE = (
    "Si un compte correspond à cet identifiant, un e-mail de réinitialisation "
    "vient d'être envoyé. Vérifiez aussi vos courriers indésirables."
)

# Anti-abus : max 5 demandes / heure / e-mail (ou IP)
_RATE_LIMIT = 5
_RATE_WINDOW = 3600


@dataclass
class ResetLookup:
    user: User | None = None
    throttled: bool = False


def find_user_for_reset(login_id: str) -> User | None:
    """Retrouve un compte actif par e-mail ou identifiant."""
    login_id = (login_id or "").strip()
    if not login_id:
        return None
    qs = User.objects.filter(is_active=True).exclude(status=User.Status.SUSPENDED)
    if "@" in login_id:
        user = qs.filter(email__iexact=login_id).first()
    else:
        user = qs.filter(username__iexact=login_id).first()
    if user and not (user.email or "").strip():
        return None
    return user


def _rate_key(identifier: str) -> str:
    return f"pwd_reset:{identifier.lower().strip()}"


def is_rate_limited(identifier: str) -> bool:
    key = _rate_key(identifier)
    return int(cache.get(key) or 0) >= _RATE_LIMIT


def bump_rate_limit(identifier: str) -> None:
    key = _rate_key(identifier)
    n = int(cache.get(key) or 0) + 1
    cache.set(key, n, timeout=_RATE_WINDOW)


def make_reset_uid(user: User) -> str:
    return urlsafe_base64_encode(force_bytes(user.pk))


def make_reset_token(user: User) -> str:
    return default_token_generator.make_token(user)


def user_from_uid(uidb64: str) -> User | None:
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        return User.objects.filter(pk=uid, is_active=True).first()
    except (TypeError, ValueError, OverflowError):
        return None


def token_is_valid(user: User, token: str) -> bool:
    return bool(user and token and default_token_generator.check_token(user, token))


def build_reset_url(user: User, *, request=None) -> str:
    path = reverse(
        "password_reset_confirm",
        kwargs={"uidb64": make_reset_uid(user), "token": make_reset_token(user)},
    )
    if request is not None:
        return request.build_absolute_uri(path)
    site = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{site}{path}"


def send_password_reset_email(user: User, *, request=None) -> MailDeliveryResult:
    brand = settings.GABPHARMA.get("NAME", "Gab'Pharma")
    name = user.get_full_name() or user.username
    reset_url = build_reset_url(user, request=request)
    context = {
        "brand": brand,
        "name": name,
        "username": user.username,
        "reset_url": reset_url,
        "expires_hours": 24,
    }
    subject = f"{brand} — réinitialisation de votre mot de passe"
    text_body = render_to_string("emails/password_reset.txt", context)
    html_body = render_to_string("emails/password_reset.html", context)

    if uses_console_email():
        logger.info(
            "\n%s\n[Gab'Pharma] Lien réinitialisation MDP\n  Destinataire : %s\n  Lien : %s\n%s",
            "=" * 60,
            user.email,
            reset_url,
            "=" * 60,
        )

    return deliver_email(
        subject=subject,
        body=text_body,
        html_body=html_body,
        recipient_list=[user.email],
    )


def request_password_reset(login_id: str, *, request=None, client_ip: str = "") -> tuple[bool, str]:
    """
    Lance l'envoi. Retourne toujours un message générique (sauf throttle / erreur SMTP claire).
    """
    login_id = (login_id or "").strip()
    if not login_id:
        return False, "Indiquez votre e-mail ou identifiant."

    throttle_id = login_id if "@" in login_id else (client_ip or login_id)
    if is_rate_limited(throttle_id) or is_rate_limited(login_id):
        return False, (
            "Trop de demandes. Attendez quelques minutes avant de réessayer, "
            "ou contactez support@gabpharma.online."
        )

    user = find_user_for_reset(login_id)
    bump_rate_limit(throttle_id)
    bump_rate_limit(login_id)

    if not user:
        # Message identique : pas de fuite d'existence de compte
        return True, GENERIC_SENT_MESSAGE

    result = send_password_reset_email(user, request=request)
    if not result.ok:
        logger.warning(
            "Échec envoi reset MDP user=%s : %s",
            user.username,
            result.error or result.mode,
        )
        return False, result.user_message or (
            "Impossible d'envoyer l'e-mail pour le moment. Réessayez plus tard."
        )
    return True, GENERIC_SENT_MESSAGE


def validate_new_password(password1: str, password2: str) -> str | None:
    """Retourne un message d'erreur ou None si OK."""
    password1 = password1 or ""
    password2 = password2 or ""
    if len(password1) < 8:
        return "Le mot de passe doit contenir au moins 8 caractères."
    if password1 != password2:
        return "La confirmation ne correspond pas."
    if password1.isdigit() or password1.isalpha():
        return "Utilisez un mélange de lettres et de chiffres."
    return None
