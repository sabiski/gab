"""Envoi e-mail via le backend Django."""
import logging

from core.email_utils import deliver_email

logger = logging.getLogger(__name__)


def send_email_notification(*, user, title, message, data=None):
    email = (user.email or "").strip()
    if not email:
        return False, "Adresse e-mail manquante"
    result = deliver_email(
        subject=f"Gab'Pharma — {title}",
        body=message,
        recipient_list=[email],
    )
    if result.ok:
        return True, ""
    return False, result.error
