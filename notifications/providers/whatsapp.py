"""WhatsApp Business — architecture prête, envoi simulé jusqu'à intégration API."""
import logging
import os

logger = logging.getLogger(__name__)


def send_whatsapp_notification(*, user, title, message, data=None):
    phone = (user.phone or "").strip()
    if not phone:
        return False, "Numéro WhatsApp manquant"

    api_token = os.environ.get("WHATSAPP_API_TOKEN", "")
    if not api_token:
        logger.info("[WhatsApp simulé → %s] %s: %s", phone, title, message[:120])
        return True, "simulated"

    # Point d'extension Meta Cloud API / partenaire local
    logger.info("[WhatsApp API → %s] %s", phone, title)
    return True, ""
