"""Service central de notifications multi-canaux (CDC §4.11)."""
from __future__ import annotations

import logging
from datetime import datetime, time

from django.utils import timezone

from accounts.models import User
from notifications.models import (
    Notification,
    NotificationCampaign,
    NotificationDispatchLog,
    NotificationPreference,
    PlatformNotificationSettings,
)
from notifications.providers import (
    send_email_notification,
    send_push_notification,
    send_sms_notification,
    send_whatsapp_notification,
)

logger = logging.getLogger(__name__)

TRANSACTIONAL_TYPES = {
    Notification.Type.ORDER,
    Notification.Type.DELIVERY,
    Notification.Type.SUCCESS,
}
CRITICAL_TYPES = {
    Notification.Type.HEALTH,
    Notification.Type.ERROR,
}

CHANNEL_HANDLERS = {
    Notification.Channel.SMS: send_sms_notification,
    Notification.Channel.EMAIL: send_email_notification,
    Notification.Channel.PUSH: send_push_notification,
    Notification.Channel.WHATSAPP: send_whatsapp_notification,
}


def get_user_preferences(user):
    prefs, _ = NotificationPreference.objects.get_or_create(user=user)
    return prefs


def _is_critical(notification_type, *, critical=False):
    return critical or notification_type in CRITICAL_TYPES


def _is_transactional(notification_type, *, transactional=False):
    return transactional or notification_type in TRANSACTIONAL_TYPES


def _in_quiet_hours(now, settings_obj):
    start = settings_obj.quiet_hours_start
    end = settings_obj.quiet_hours_end
    current = now.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def _daily_marketing_count(user, since):
    return NotificationDispatchLog.objects.filter(
        user=user,
        created_at__gte=since,
        status=NotificationDispatchLog.Status.SENT,
        channel__in={
            Notification.Channel.SMS,
            Notification.Channel.PUSH,
            Notification.Channel.EMAIL,
            Notification.Channel.WHATSAPP,
        },
        notification__notification_type__in={
            Notification.Type.PROMO,
            Notification.Type.INFO,
        },
    ).count()


def _channels_for_user(user, notification_type, prefs, platform, *, channels=None):
    if channels:
        requested = set(channels)
    else:
        requested = {
            Notification.Channel.PUSH,
            Notification.Channel.SMS,
            Notification.Channel.EMAIL,
        }
        if notification_type == Notification.Type.PROMO:
            requested.add(Notification.Channel.WHATSAPP)

    allowed = []
    mapping = [
        (Notification.Channel.PUSH, prefs.push_enabled and platform.channel_push_enabled),
        (Notification.Channel.SMS, prefs.sms_enabled and platform.channel_sms_enabled),
        (Notification.Channel.EMAIL, prefs.email_enabled and platform.channel_email_enabled),
        (
            Notification.Channel.WHATSAPP,
            prefs.whatsapp_enabled and platform.channel_whatsapp_enabled,
        ),
    ]
    for ch, enabled in mapping:
        if ch in requested and enabled:
            if notification_type == Notification.Type.PROMO and not prefs.marketing_enabled:
                continue
            allowed.append(ch)
    return allowed


def _log_dispatch(*, user, notification, campaign, channel, status, title, message, error=""):
    NotificationDispatchLog.objects.create(
        user=user,
        notification=notification,
        campaign=campaign,
        channel=channel,
        status=status,
        title=title[:200],
        message=(message or "")[:500],
        error_message=(error or "")[:500],
    )


def notify_user(
    user,
    title,
    message,
    *,
    notification_type=Notification.Type.INFO,
    data=None,
    channels=None,
    critical=False,
    transactional=False,
    campaign=None,
):
    """
    Crée une notification in-app et diffuse sur les canaux externes autorisés.
    Les notifications transactionnelles et critiques ignorent plafond et plage horaire.
    """
    if not user:
        return None

    data = data or {}
    critical = _is_critical(notification_type, critical=critical)
    transactional = _is_transactional(notification_type, transactional=transactional)

    notif = Notification.objects.create(
        user=user,
        title=title,
        message=message,
        notification_type=notification_type,
        channel=Notification.Channel.IN_APP,
        data=data,
    )

    prefs = get_user_preferences(user)
    platform = PlatformNotificationSettings.load()
    now = timezone.localtime()
    day_start = timezone.make_aware(
        datetime.combine(now.date(), time.min),
        timezone.get_current_timezone(),
    )

    external_channels = _channels_for_user(
        user, notification_type, prefs, platform, channels=channels
    )

    throttled = False
    quiet = False
    if not critical and not transactional:
        if _in_quiet_hours(now, platform):
            quiet = True
        elif _daily_marketing_count(user, day_start) >= platform.max_daily_per_user:
            throttled = True

    for channel in external_channels:
        if quiet:
            _log_dispatch(
                user=user,
                notification=notif,
                campaign=campaign,
                channel=channel,
                status=NotificationDispatchLog.Status.QUIET,
                title=title,
                message=message,
            )
            continue
        if throttled:
            _log_dispatch(
                user=user,
                notification=notif,
                campaign=campaign,
                channel=channel,
                status=NotificationDispatchLog.Status.THROTTLED,
                title=title,
                message=message,
            )
            continue

        handler = CHANNEL_HANDLERS.get(channel)
        if not handler:
            continue
        try:
            ok, err = handler(user=user, title=title, message=message, data=data)
            _log_dispatch(
                user=user,
                notification=notif,
                campaign=campaign,
                channel=channel,
                status=NotificationDispatchLog.Status.SENT
                if ok
                else NotificationDispatchLog.Status.FAILED,
                title=title,
                message=message,
                error=err,
            )
        except Exception as exc:
            logger.exception("Dispatch %s failed for user %s", channel, user.pk)
            _log_dispatch(
                user=user,
                notification=notif,
                campaign=campaign,
                channel=channel,
                status=NotificationDispatchLog.Status.FAILED,
                title=title,
                message=message,
                error=str(exc),
            )

    return notif


def audience_queryset(audience):
    qs = User.objects.filter(status=User.Status.ACTIVE)
    if audience == NotificationCampaign.Audience.CLIENTS:
        return qs.filter(role=User.Role.CLIENT)
    if audience == NotificationCampaign.Audience.PHARMACIES:
        return qs.filter(role=User.Role.PHARMACIST)
    if audience == NotificationCampaign.Audience.COURIERS:
        return qs.filter(role=User.Role.COURIER)
    return qs


def send_campaign(campaign: NotificationCampaign):
    """Diffuse une campagne immédiate ou planifiée."""
    if campaign.status not in {
        NotificationCampaign.Status.DRAFT,
        NotificationCampaign.Status.SCHEDULED,
    }:
        return 0

    campaign.status = NotificationCampaign.Status.SENDING
    campaign.save(update_fields=["status", "updated_at"])

    channels = campaign.channels or [Notification.Channel.IN_APP, Notification.Channel.PUSH]
    users = audience_queryset(campaign.audience)
    count = 0
    is_promo = campaign.notification_type == Notification.Type.PROMO

    for user in users.iterator(chunk_size=200):
        notify_user(
            user,
            campaign.title,
            campaign.message,
            notification_type=campaign.notification_type,
            channels=channels,
            transactional=not is_promo,
            critical=campaign.notification_type in CRITICAL_TYPES,
            campaign=campaign,
        )
        count += 1

    campaign.status = NotificationCampaign.Status.SENT
    campaign.sent_at = timezone.now()
    campaign.recipients_count = count
    campaign.save(update_fields=["status", "sent_at", "recipients_count", "updated_at"])
    return count


def process_due_campaigns():
    """Envoie les campagnes planifiées dont l'heure est passée."""
    now = timezone.now()
    due = NotificationCampaign.objects.filter(
        status=NotificationCampaign.Status.SCHEDULED,
        scheduled_at__lte=now,
    )
    total = 0
    for campaign in due:
        total += send_campaign(campaign)
    return total


def save_push_subscription(user, *, endpoint, p256dh, auth, user_agent=""):
    from notifications.models import PushSubscription

    sub, _ = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": user,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": user_agent[:255],
            "is_active": True,
        },
    )
    return sub


def unsubscribe_push(endpoint):
    from notifications.models import PushSubscription

    PushSubscription.objects.filter(endpoint=endpoint).update(is_active=False)
