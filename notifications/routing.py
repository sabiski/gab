"""Résolution des liens de destination pour les notifications in-app."""
from django.urls import reverse

from notifications.models import Notification


def pharmacy_order_url(order_id):
    """URL portail pharmacie vers le détail d'une commande."""
    from orders.models import Order

    order = (
        Order.objects.filter(pk=order_id)
        .only("id", "code", "status", "linked_prescription_id")
        .first()
    )
    if not order:
        return reverse("bo_pharmacy_orders")
    if order.linked_prescription_id or order.status == Order.Status.AWAITING_RX:
        return reverse("bo_pharmacy_prescription_detail", args=[order.pk])
    base = reverse("bo_pharmacy_orders")
    return f"{base}?order={order.pk}#order-{order.pk}"


def emergency_messages_url(data):
    """Lien messagerie pour une alerte SOS (nouvelles et anciennes notifications)."""
    from notifications.models import EmergencyAlert, PharmacyConversation

    conv_id = data.get("conversation_id")
    if conv_id:
        return f"{reverse('bo_pharmacy_messages')}?conv={conv_id}"

    alert_id = data.get("alert_id")
    pharmacy_id = data.get("pharmacy_id")
    if not alert_id:
        return reverse("bo_pharmacy_messages")

    alert = (
        EmergencyAlert.objects.filter(pk=alert_id)
        .select_related("client", "assigned_pharmacy")
        .first()
    )
    if not alert:
        return reverse("bo_pharmacy_messages")

    pharmacy_id = pharmacy_id or alert.assigned_pharmacy_id
    conv = PharmacyConversation.objects.filter(emergency_alert=alert).first()
    if not conv and pharmacy_id:
        conv = PharmacyConversation.objects.filter(
            pharmacy_id=pharmacy_id,
            client=alert.client,
        ).first()
    if conv:
        return f"{reverse('bo_pharmacy_messages')}?conv={conv.id}"
    return reverse("bo_pharmacy_messages")


def resolve_notification_url(notif):
    """URL cible d'une notification, ou None si la liste suffit."""
    data = notif.data or {}
    event = data.get("event")
    title_l = (notif.title or "").lower()
    is_sos = event == "emergency" or (
        notif.notification_type == Notification.Type.ERROR and data.get("alert_id")
    ) or ("urgence" in title_l or "sos" in title_l)

    if is_sos:
        return emergency_messages_url(data)

    order_id = data.get("order_id")

    if event == "new_order" and order_id:
        return pharmacy_order_url(order_id)

    if event == "message" and data.get("conversation_id"):
        return f"{reverse('bo_pharmacy_messages')}?conv={data['conversation_id']}"
    if event == "stock_alert":
        return f"{reverse('bo_pharmacy_stocks')}?tab=low"
    if event == "subscription":
        return reverse("bo_pharmacy_subscription")

    url = data.get("url")
    if url:
        if order_id and url.rstrip("/") == reverse("bo_pharmacy_orders").rstrip("/"):
            return pharmacy_order_url(order_id)
        return url

    if order_id:
        return reverse("bo_client_order_detail", kwargs={"pk": order_id})

    return None


def notification_display_meta(notif):
    """Libellé et style visuel pour l'affichage pharmacie."""
    data = notif.data or {}
    event = data.get("event")
    ntype = notif.notification_type

    if ntype == Notification.Type.ERROR or event == "emergency":
        return {
            "label": "Alerte urgente",
            "severity": "critical",
            "icon": "e911_emergency",
        }
    title_l = (notif.title or "").lower()
    if "urgence" in title_l or "sos" in title_l:
        return {
            "label": "Alerte urgente",
            "severity": "critical",
            "icon": "e911_emergency",
        }
    if ntype == Notification.Type.WARNING or event == "stock_alert":
        if data.get("alert_kind") == "out":
            return {
                "label": "Rupture de stock",
                "severity": "warning",
                "icon": "error",
            }
        return {
            "label": "Alerte stock",
            "severity": "warning",
            "icon": "inventory_2",
        }
    if ntype == Notification.Type.ORDER or event == "new_order":
        return {
            "label": "Nouvelle commande",
            "severity": "order",
            "icon": "shopping_bag",
        }
    if event == "message":
        return {
            "label": "Message patient",
            "severity": "info",
            "icon": "chat",
        }
    if event == "subscription" or (
        ntype == Notification.Type.SUCCESS and "abonnement" in title_l
    ):
        return {
            "label": "Abonnement",
            "severity": "info",
            "icon": "card_membership",
        }
    if ntype == Notification.Type.DELIVERY:
        return {
            "label": "Livraison",
            "severity": "info",
            "icon": "local_shipping",
        }
    return {
        "label": "Information",
        "severity": "info",
        "icon": "notifications",
    }


def annotate_pharmacy_notification(notif, *, open_url):
    """Enrichit une notification pour les templates pharmacie."""
    meta = notification_display_meta(notif)
    notif.open_url = open_url
    notif.alert_label = meta["label"]
    notif.alert_severity = meta["severity"]
    notif.alert_icon = meta["icon"]
    return notif
