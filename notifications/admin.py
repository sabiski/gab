from django.contrib import admin

from notifications.models import (
    AuditLog,
    Notification,
    NotificationCampaign,
    NotificationDispatchLog,
    NotificationPreference,
    PlatformNotificationSettings,
    PushSubscription,
)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "notification_type", "channel", "is_read", "created_at")
    list_filter = ("notification_type", "channel", "is_read")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "push_enabled", "sms_enabled", "email_enabled", "marketing_enabled")


@admin.register(PlatformNotificationSettings)
class PlatformNotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ("max_daily_per_user", "quiet_hours_start", "quiet_hours_end", "updated_at")


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "is_active", "created_at")
    list_filter = ("is_active",)


@admin.register(NotificationCampaign)
class NotificationCampaignAdmin(admin.ModelAdmin):
    list_display = ("title", "audience", "status", "recipients_count", "created_at")
    list_filter = ("status", "audience")


@admin.register(NotificationDispatchLog)
class NotificationDispatchLogAdmin(admin.ModelAdmin):
    list_display = ("user", "channel", "status", "title", "created_at")
    list_filter = ("channel", "status")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "module", "user", "status", "is_sensitive", "created_at")
    list_filter = ("module", "is_sensitive", "status")
    readonly_fields = ("created_at",)
