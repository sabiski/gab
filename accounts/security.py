"""Protection des comptes : anti force brute, mots de passe de démo, complexité.

- Un compte est bloqué 15 min après N échecs (réglage « Tentatives de connexion »
  de la plateforme, 5 par défaut) ; le titulaire est prévenu.
- Une adresse IP est bloquée 15 min après 40 échecs, tous comptes confondus.
- En production, les mots de passe de démonstration sont refusés.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

logger = logging.getLogger("gabpharma.security")

LOCK_WINDOW = timedelta(minutes=15)
IP_FAILURE_LIMIT = 40  # large : beaucoup d'abonnés mobiles partagent une IP (CGNAT)

# Mots de passe des comptes de démonstration (seed_demo) — interdits en production.
DEMO_PASSWORDS = {
    "admin123", "patient123", "pharma123", "livreur123", "autorite123",
    "support123", "cnamgs123", "password", "motdepasse", "12345678",
}


def client_ip(request) -> str | None:
    """IP du client derrière le reverse proxy (Traefik / Nginx).

    On prend l'entrée ajoutée par le dernier proxy de confiance dans X-Forwarded-For
    (les entrées de gauche peuvent être forgées par le client).
    """
    if request is None:
        return None
    proxies = int(getattr(settings, "SECURITY_TRUSTED_PROXIES", 1))
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if proxies > 0 and forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-proxies] if len(hops) >= proxies else hops[0]
    return request.META.get("REMOTE_ADDR")


def _limit() -> int:
    from accounts.models import PlatformSettings

    try:
        return max(3, PlatformSettings.load().login_attempt_limit or 5)
    except Exception:
        return 5


@dataclass
class LockStatus:
    locked: bool
    minutes_left: int = 0
    scope: str = ""  # "account" | "ip"

    @property
    def message(self) -> str:
        if self.scope == "ip":
            return (
                "Trop de tentatives depuis votre connexion. "
                f"Réessayez dans {self.minutes_left} minute(s)."
            )
        return (
            "Compte temporairement bloqué après plusieurs mots de passe incorrects. "
            f"Réessayez dans {self.minutes_left} minute(s) ou réinitialisez votre mot de passe."
        )


def _failures_since_success(qs):
    last_ok = qs.filter(success=True).order_by("-created_at").values_list("created_at", flat=True).first()
    failures = qs.filter(success=False)
    if last_ok:
        failures = failures.filter(created_at__gt=last_ok)
    return failures


def lock_status(username: str, ip: str | None) -> LockStatus:
    from accounts.models import LoginAttempt

    now = timezone.now()
    since = now - LOCK_WINDOW
    recent = LoginAttempt.objects.filter(created_at__gte=since)

    if ip:
        ip_failures = recent.filter(ip_address=ip, success=False)
        if ip_failures.count() >= IP_FAILURE_LIMIT:
            oldest = ip_failures.order_by("-created_at")[IP_FAILURE_LIMIT - 1].created_at
            left = max(1, int((oldest + LOCK_WINDOW - now).total_seconds() // 60) + 1)
            return LockStatus(True, left, "ip")

    key = (username or "").strip().lower()
    if key:
        failures = _failures_since_success(recent.filter(username=key))
        limit = _limit()
        if failures.count() >= limit:
            last = failures.order_by("-created_at").first().created_at
            left = max(1, int((last + LOCK_WINDOW - now).total_seconds() // 60) + 1)
            return LockStatus(True, left, "account")
    return LockStatus(False)


def record_attempt(username: str, request, *, success: bool, channel: str = "web") -> None:
    from accounts.models import LoginAttempt

    key = (username or "").strip().lower()[:150]
    ip = client_ip(request)
    LoginAttempt.objects.create(
        username=key,
        ip_address=ip,
        success=success,
        channel=channel,
        user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else "")[:255],
    )
    if success:
        return
    status = lock_status(key, ip)
    if status.locked and status.scope == "account":
        _on_account_locked(key, ip)
    elif status.locked and status.scope == "ip":
        logger.warning("IP bloquée pour force brute : %s", ip)


def _on_account_locked(username: str, ip: str | None) -> None:
    """Journalise et prévient le titulaire (une fois par blocage)."""
    from accounts.models import LoginAttempt, User
    from notifications.models import AuditLog, Notification
    from notifications.services import notify_user

    since = timezone.now() - LOCK_WINDOW
    if AuditLog.objects.filter(
        action="Compte bloqué (force brute)", details__startswith=f"{username} ", created_at__gte=since
    ).exists():
        return
    user = User.objects.filter(username__iexact=username).first() or User.objects.filter(
        email__iexact=username
    ).first()
    AuditLog.objects.create(
        user=user,
        action="Compte bloqué (force brute)",
        module="security",
        details=f"{username} — {LoginAttempt.objects.filter(username=username, success=False, created_at__gte=since).count()} échecs, IP {ip}",
        ip_address=ip,
        status="warning",
        is_sensitive=True,
    )
    logger.warning("Compte bloqué pour force brute : %s (IP %s)", username, ip)
    if user:
        notify_user(
            user,
            "Tentatives de connexion suspectes",
            (
                "Plusieurs mots de passe incorrects ont été saisis sur votre compte Gab'Pharma. "
                "Il est bloqué 15 minutes par sécurité. Si ce n'était pas vous, changez votre "
                "mot de passe dès que possible."
            ),
            notification_type=Notification.Type.WARNING,
            transactional=True,
            critical=True,
        )


def is_demo_password_blocked(password: str) -> bool:
    """En production, refuse les mots de passe de démonstration connus."""
    if settings.DEBUG:
        return False
    return (password or "").strip().lower() in DEMO_PASSWORDS


def purge_old_attempts(days: int = 90) -> int:
    from accounts.models import LoginAttempt

    deleted, _ = LoginAttempt.objects.filter(created_at__lt=timezone.now() - timedelta(days=days)).delete()
    return deleted


class ComplexityValidator:
    """Majuscule, minuscule et chiffre — si « Complexité du mot de passe » est activée."""

    def validate(self, password, user=None):
        from accounts.models import PlatformSettings

        try:
            required = PlatformSettings.load().password_complexity
        except Exception:
            required = True
        if not required:
            return
        if not (re.search(r"[a-z]", password) and re.search(r"[A-Z]", password) and re.search(r"\d", password)):
            raise ValidationError(
                "Le mot de passe doit contenir au moins une majuscule, une minuscule et un chiffre.",
                code="password_too_simple",
            )

    def get_help_text(self):
        return "Au moins une majuscule, une minuscule et un chiffre."


class DemoPasswordValidator:
    """Interdit les mots de passe de démonstration publiés dans le README."""

    def validate(self, password, user=None):
        if (password or "").strip().lower() in DEMO_PASSWORDS:
            raise ValidationError("Ce mot de passe est public (compte de démonstration).", code="demo_password")

    def get_help_text(self):
        return "Les mots de passe de démonstration sont interdits."
