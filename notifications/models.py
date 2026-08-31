from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    class Type(models.TextChoices):
        INFO = "info", "Information"
        SUCCESS = "success", "Succès"
        WARNING = "warning", "Attention"
        ERROR = "error", "Erreur"
        ORDER = "order", "Commande"
        DELIVERY = "delivery", "Livraison"
        PROMO = "promo", "Promotion"
        HEALTH = "health", "Alerte sanitaire"

    class Channel(models.TextChoices):
        IN_APP = "in_app", "In-app"
        PUSH = "push", "Push"
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
        help_text="Null = broadcast ciblé via audience",
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=Type.choices, default=Type.INFO)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.IN_APP)
    is_read = models.BooleanField(default=False)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=100)
    module = models.CharField(max_length=50)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(max_length=20, default="success")
    is_sensitive = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Journal d'activité"
        verbose_name_plural = "Journaux d'activité"

    def __str__(self):
        return f"{self.action} — {self.module}"


class SupportTicket(models.Model):
    """Réclamations / support client (CDC §3.4 Support / Réclamations)."""

    class Category(models.TextChoices):
        MISSING_ITEM = "missing_item", "Article manquant"
        DAMAGED = "damaged", "Produit endommagé"
        DELAY = "delay", "Retard de livraison"
        PAYMENT = "payment", "Problème de paiement"
        ACCOUNT = "account", "Compte / profil"
        OTHER = "other", "Autre"

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        IN_PROGRESS = "in_progress", "En cours"
        RESOLVED = "resolved", "Résolue"
        CLOSED = "closed", "Fermée"

    code = models.CharField(max_length=30, unique=True, blank=True)
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_tickets",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.OTHER)
    subject = models.CharField(max_length=200)
    description = models.TextField()
    attachment = models.FileField(upload_to="support/", blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )
    staff_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Réclamation"
        verbose_name_plural = "Réclamations"

    def save(self, *args, **kwargs):
        if not self.code:
            year = timezone.now().year
            import secrets

            self.code = f"GP-{year}-{secrets.randbelow(1_000_000):06d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code or f"Ticket #{self.pk}"


class EmergencyAlert(models.Model):
    """Alerte SOS patient Premium (Écran 13 — Bouton d'urgence)."""

    class Category(models.TextChoices):
        MEDICAL = "medical", "Problème médical"
        ACCIDENT = "accident", "Accident"
        CHILD = "child", "Enfant malade"
        OTHER = "other", "Autre urgence"

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        IN_PROGRESS = "in_progress", "Prise en charge"
        RESOLVED = "resolved", "Résolue"

    code = models.CharField(max_length=30, unique=True, blank=True)
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="emergency_alerts",
    )
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.MEDICAL)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    address = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    assigned_pharmacy = models.ForeignKey(
        "pharmacies.Pharmacy",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emergency_alerts",
    )
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    escalated_to_support = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Alerte urgence"
        verbose_name_plural = "Alertes urgence"

    def save(self, *args, **kwargs):
        if not self.code:
            import secrets

            self.code = f"SOS-{timezone.now().year}-{secrets.randbelow(1_000_000):06d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code or f"Alerte #{self.pk}"


class PharmacyConversation(models.Model):
    """Messagerie patient ↔ pharmacie (urgence / conseil)."""

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pharmacy_conversations",
    )
    pharmacy = models.ForeignKey(
        "pharmacies.Pharmacy",
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    emergency_alert = models.ForeignKey(
        "EmergencyAlert",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("client", "pharmacy")
        ordering = ["-updated_at"]
        verbose_name = "Conversation pharmacie"
        verbose_name_plural = "Conversations pharmacie"

    def __str__(self):
        return f"{self.client_id} ↔ {self.pharmacy.name}"


class PharmacyMessage(models.Model):
    conversation = models.ForeignKey(
        PharmacyConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pharmacy_messages_sent",
    )
    body = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Message pharmacie"
        verbose_name_plural = "Messages pharmacie"

    def __str__(self):
        return f"{self.sender_id}: {self.body[:40]}"


class NotificationPreference(models.Model):
    """Préférences utilisateur par canal (CDC §4.11)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    push_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    whatsapp_enabled = models.BooleanField(
        default=False,
        help_text="Architecture prête — activation lors de l'intégration WhatsApp Business",
    )
    marketing_enabled = models.BooleanField(
        default=True,
        help_text="Promotions et informations non critiques",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Préférences notif — {self.user}"


class PlatformNotificationSettings(models.Model):
    """Paramètres globaux (singleton pk=1) — fréquence et plage horaire."""

    max_daily_per_user = models.PositiveSmallIntegerField(
        default=8,
        help_text="Notifications marketing max / utilisateur / jour",
    )
    quiet_hours_start = models.TimeField(default="21:00")
    quiet_hours_end = models.TimeField(default="07:00")
    channel_sms_enabled = models.BooleanField(default=True)
    channel_email_enabled = models.BooleanField(default=True)
    channel_push_enabled = models.BooleanField(default=True)
    channel_whatsapp_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Paramètres notifications plateforme"
        verbose_name_plural = "Paramètres notifications plateforme"

    def __str__(self):
        return "Paramètres notifications"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PushSubscription(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Push — {self.user_id}"


class NotificationDispatchLog(models.Model):
    class Status(models.TextChoices):
        SENT = "sent", "Envoyé"
        SKIPPED = "skipped", "Ignoré"
        THROTTLED = "throttled", "Limité (fréquence)"
        QUIET = "quiet", "Hors plage horaire"
        FAILED = "failed", "Échec"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_dispatches",
        null=True,
        blank=True,
    )
    notification = models.ForeignKey(
        Notification,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispatches",
    )
    campaign = models.ForeignKey(
        "NotificationCampaign",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispatches",
    )
    channel = models.CharField(max_length=20, choices=Notification.Channel.choices)
    status = models.CharField(max_length=20, choices=Status.choices)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.channel} — {self.status}"


class NotificationCampaign(models.Model):
    class Audience(models.TextChoices):
        ALL = "all", "Tous les utilisateurs"
        CLIENTS = "clients", "Clients"
        PHARMACIES = "pharmacists", "Pharmaciens"
        COURIERS = "couriers", "Livreurs"

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        SCHEDULED = "scheduled", "Planifiée"
        SENDING = "sending", "En cours"
        SENT = "sent", "Envoyée"
        FAILED = "failed", "Échouée"

    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=20,
        choices=Notification.Type.choices,
        default=Notification.Type.INFO,
    )
    audience = models.CharField(max_length=20, choices=Audience.choices, default=Audience.ALL)
    channels = models.JSONField(
        default=list,
        help_text="Liste : in_app, push, sms, email, whatsapp",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    recipients_count = models.PositiveIntegerField(default=0)
    read_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_campaigns",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
