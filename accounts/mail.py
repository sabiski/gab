"""Envoi d'e-mails liés aux comptes Gab'Pharma."""
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse

from core.email_utils import (
    MailDeliveryResult,
    deliver_email,
    log_account_credentials,
    uses_console_email,
)


def generate_temp_password(length: int = 12) -> str:
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
        ):
            return pwd


def _login_url(request=None) -> str:
    login_path = reverse("login")
    if request is not None:
        return request.build_absolute_uri(login_path)
    site = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{site}{login_path}"


def credentials_email_preview_url(request=None) -> str:
    path = reverse("bo_preview_credentials_email")
    if request is not None:
        return request.build_absolute_uri(path)
    site = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{site}{path}"


def build_credentials_email(user, plain_password: str, *, request=None):
    brand = settings.GABPHARMA.get("NAME", "Gab'Pharma")
    role = user.get_role_display()
    name = user.get_full_name() or user.username
    login_url = _login_url(request)
    context = {
        "brand": brand,
        "name": name,
        "role_label": role,
        "username": user.username,
        "password": plain_password,
        "login_url": login_url,
    }
    subject = f"{brand} — vos identifiants de connexion ({role})"
    text_body = render_to_string("emails/account_credentials.txt", context)
    html_body = render_to_string("emails/account_credentials.html", context)
    return subject, text_body, html_body, login_url


def send_account_credentials(
    user, plain_password: str, *, request=None
) -> MailDeliveryResult:
    """Envoie identifiant + mot de passe temporaire (e-mail HTML ou console)."""
    if not user.email:
        return MailDeliveryResult(
            ok=False, mode="skipped", error="Aucune adresse e-mail."
        )

    subject, text_body, html_body, login_url = build_credentials_email(
        user, plain_password, request=request
    )
    role = user.get_role_display()
    preview_url = credentials_email_preview_url(request)

    if uses_console_email():
        log_account_credentials(
            email=user.email,
            username=user.username,
            plain_password=plain_password,
            role_label=role,
            login_url=login_url,
            preview_url=preview_url,
        )

    return deliver_email(
        subject=subject,
        body=text_body,
        html_body=html_body,
        recipient_list=[user.email],
    )
