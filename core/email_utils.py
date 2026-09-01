"""Envoi d'e-mails — console en dev, SMTP dès que configuré."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail

logger = logging.getLogger("gabpharma.mail")

_USER_MAIL_ERRORS = (
    "impossible d'envoyer",
    "réessayez plus tard",
    "service e-mail temporairement indisponible",
    "contactez le support",
)


def mail_error_for_user(technical_error: str = "") -> str:
    """Message générique affiché à l'utilisateur (jamais le détail SMTP)."""
    raw = (technical_error or "").lower()
    if any(marker in raw for marker in _USER_MAIL_ERRORS):
        return technical_error.strip()
    if (
        "535" in raw
        or "authentication failed" in raw
        or "authentication" in raw
        or "invalid credentials" in raw
        or "username and password not accepted" in raw
    ):
        return (
            "L'envoi du message a échoué. Réessayez plus tard ou contactez le support."
        )
    if "connection" in raw or "timeout" in raw or "unreachable" in raw:
        return "Service e-mail temporairement indisponible. Réessayez dans quelques minutes."
    return "Impossible d'envoyer l'e-mail pour le moment. Réessayez plus tard."


@dataclass
class MailDeliveryResult:
    ok: bool
    mode: str = "skipped"  # smtp | console | skipped | failed
    error: str = ""

    @property
    def user_message(self) -> str:
        if self.ok:
            return ""
        if self.error in {"Aucun destinataire."}:
            return self.error
        return mail_error_for_user(self.error)

    def notice(self, *, plain_password: str = "") -> str:
        """Message utilisateur court (flash Django)."""
        if self.ok and self.mode == "smtp":
            return "Identifiants envoyés par e-mail."
        if self.ok and self.mode == "console":
            return (
                "Identifiants enregistrés — consultez la console du serveur "
                "(mode développement). Aperçu HTML : /espace/apercu-email-identifiants/"
            )
        if plain_password:
            return f"Mot de passe temporaire : {plain_password}"
        if self.error:
            return f"E-mail non envoyé : {self.user_message}"
        return ""


def smtp_configured() -> bool:
    return bool(
        os.environ.get("EMAIL_HOST_USER", getattr(settings, "EMAIL_HOST_USER", "")).strip()
        and os.environ.get(
            "EMAIL_HOST_PASSWORD", getattr(settings, "EMAIL_HOST_PASSWORD", "")
        ).strip()
    )


def uses_console_email() -> bool:
    backend = getattr(settings, "EMAIL_BACKEND", "")
    return "console" in backend or not smtp_configured()


def log_account_credentials(
    *,
    email: str,
    username: str,
    plain_password: str,
    role_label: str,
    login_url: str,
    preview_url: str = "",
) -> None:
    """Affiche les identifiants dans la console du serveur (dev / secours)."""
    block = (
        "\n"
        + "=" * 60
        + f"\n[Gab'Pharma] Identifiants compte {role_label}\n"
        f"  Destinataire : {email}\n"
        f"  Identifiant  : {username}\n"
        f"  Mot de passe : {plain_password}\n"
        f"  Connexion    : {login_url}\n"
    )
    if preview_url:
        block += f"  Aperçu e-mail : {preview_url}\n"
    block += "=" * 60 + "\n"
    print(block, flush=True)
    logger.info(
        "Identifiants compte %s → %s (identifiant %s)",
        role_label,
        email,
        username,
    )


def deliver_email(
    *,
    subject: str,
    body: str,
    recipient_list: list[str],
    html_body: str = "",
    fail_silently: bool | None = None,
) -> MailDeliveryResult:
    """Envoie un e-mail via SMTP ou affiche dans la console si non configuré."""
    recipients = [r.strip() for r in recipient_list if (r or "").strip()]
    if not recipients:
        return MailDeliveryResult(ok=False, mode="skipped", error="Aucun destinataire.")

    if fail_silently is None:
        fail_silently = uses_console_email()

    try:
        if html_body:
            message = EmailMultiAlternatives(
                subject,
                body,
                settings.DEFAULT_FROM_EMAIL,
                recipients,
            )
            message.attach_alternative(html_body, "text/html")
            message.send(fail_silently=fail_silently)
        else:
            send_mail(
                subject,
                body,
                settings.DEFAULT_FROM_EMAIL,
                recipients,
                fail_silently=fail_silently,
            )
    except Exception as exc:
        logger.exception("Échec envoi e-mail vers %s", ", ".join(recipients))
        return MailDeliveryResult(ok=False, mode="failed", error=str(exc))

    mode = "console" if uses_console_email() else "smtp"
    return MailDeliveryResult(ok=True, mode=mode)
