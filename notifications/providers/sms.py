"""Fournisseur SMS — console en dev, API configurable en production."""
import logging
import os

logger = logging.getLogger(__name__)


def send_sms_notification(*, user, title, message, data=None):
    phone = (user.phone or "").strip()
    if not phone:
        return False, "Numéro de téléphone manquant"

    body = f"{title}: {message}"[:320]
    provider = os.environ.get("SMS_PROVIDER", "console")

    if provider == "console":
        logger.info("[SMS → %s] %s", phone, body)
        return True, ""

    # Point d'extension : AfricasTalking, Twilio, etc.
    api_url = os.environ.get("SMS_API_URL", "")
    if not api_url:
        logger.warning("SMS_PROVIDER=%s mais SMS_API_URL non configurée", provider)
        return False, "SMS non configuré"
    logger.info("[SMS API %s → %s] %s", provider, phone, body)
    return True, ""
