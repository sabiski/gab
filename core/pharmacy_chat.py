"""Messagerie patient ↔ pharmacie."""
from django.utils import timezone

from notifications.models import PharmacyConversation, PharmacyMessage


def get_or_create_conversation(client, pharmacy, emergency_alert=None):
    conv, created = PharmacyConversation.objects.get_or_create(
        client=client,
        pharmacy=pharmacy,
        defaults={"emergency_alert": emergency_alert},
    )
    if emergency_alert and not conv.emergency_alert_id:
        conv.emergency_alert = emergency_alert
        conv.save(update_fields=["emergency_alert"])
    return conv


def open_emergency_chat(*, user, pharmacy, alert, auto_message):
    """Ouvre la conversation urgence et envoie le message initial au pharmacien."""
    conv = get_or_create_conversation(user, pharmacy, emergency_alert=alert)
    if not conv.messages.exists():
        send_pharmacy_message(
            conversation=conv,
            sender=user,
            body=auto_message,
            notify_user=pharmacy.owner,
            notify_title=f"Urgence SOS {alert.code}",
            notify_message=auto_message[:200],
        )
    return conv


def send_pharmacy_message(*, conversation, sender, body, notify_user=None, notify_title="", notify_message=""):
    body = (body or "").strip()
    if not body:
        return None
    msg = PharmacyMessage.objects.create(
        conversation=conversation,
        sender=sender,
        body=body,
    )
    conversation.updated_at = timezone.now()
    conversation.save(update_fields=["updated_at"])
    if notify_user:
        from core.pharmacy_notifications import notify_pharmacy_message

        notify_pharmacy_message(
            conversation,
            notify_title or "Nouveau message",
            notify_message or body[:200],
        )
    return msg
