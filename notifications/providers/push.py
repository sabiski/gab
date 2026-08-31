"""Notifications push Web (PWA) via pywebpush si configuré."""
import json
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)


def send_push_notification(*, user, title, message, data=None):
    from notifications.models import PushSubscription

    subs = PushSubscription.objects.filter(user=user, is_active=True)
    if not subs.exists():
        return False, "Aucun abonnement push"

    vapid_private = getattr(settings, "VAPID_PRIVATE_KEY", "") or os.environ.get("VAPID_PRIVATE_KEY", "")
    vapid_claims = getattr(settings, "VAPID_CLAIMS", {"sub": "mailto:noreply@gabpharma.ga"})

    payload = json.dumps(
        {
            "title": title,
            "body": message,
            "data": data or {},
            "icon": "/static/icons/icon-192.png",
        }
    )

    if not vapid_private:
        logger.info("[Push simulé → user %s] %s", user.pk, title)
        return True, "simulated"

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush non installé — push simulé")
        return True, "simulated"

    sent = 0
    errors = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=vapid_private,
                vapid_claims=vapid_claims,
            )
            sent += 1
        except WebPushException as exc:
            errors.append(str(exc))
            if getattr(exc, "response", None) and exc.response.status_code in {404, 410}:
                sub.is_active = False
                sub.save(update_fields=["is_active", "updated_at"])

    if sent:
        return True, ""
    return False, "; ".join(errors) or "Échec push"
