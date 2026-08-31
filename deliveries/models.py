from django.conf import settings
from django.db import models

from orders.models import Order


class Delivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        ASSIGNED = "assigned", "Assignée"
        PICKING_UP = "picking_up", "En route retrait"
        PICKED_UP = "picked_up", "Retirée"
        IN_TRANSIT = "in_transit", "En livraison"
        DELIVERED = "delivered", "Livrée"
        FAILED = "failed", "Échouée"
        TRANSFERRED = "transferred", "Transférée"

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="delivery")
    courier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deliveries",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    transfer_code = models.CharField(max_length=6, blank=True)
    handoff_courier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handoff_deliveries",
        help_text="Livreur remplaçant (en attente validation du code)",
    )
    transfer_requested_at = models.DateTimeField(null=True, blank=True)
    transfer_completed_at = models.DateTimeField(null=True, blank=True)
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    estimated_minutes = models.PositiveIntegerField(null=True, blank=True)
    courier_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    courier_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Livraison"
        verbose_name_plural = "Livraisons"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Livraison {self.order.code}"


class DeliveryIncident(models.Model):
    class Type(models.TextChoices):
        VEHICLE_BREAKDOWN = "vehicle_breakdown", "Panne de véhicule"
        ACCIDENT = "accident", "Accident"
        FLAT_TIRE = "flat_tire", "Crevaison"
        CLIENT_ABSENT = "client_absent", "Client absent"
        CLIENT_REFUSES_PAY = "client_refuses_pay", "Client refuse de payer"
        PHONE_UNREACHABLE = "phone_unreachable", "Téléphone injoignable"
        ADDRESS_NOT_FOUND = "address_not_found", "Adresse introuvable"
        OTHER = "other", "Autre"

    class Priority(models.TextChoices):
        LOW = "low", "Basse"
        MEDIUM = "medium", "Moyenne"
        HIGH = "high", "Haute"
        URGENT = "urgent", "Urgente"

    class Status(models.TextChoices):
        OPEN = "open", "Ouvert"
        IN_PROGRESS = "in_progress", "En cours"
        RESOLVED = "resolved", "Résolu"
        ESCALATED = "escalated", "Escaladé"

    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="incidents")
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    incident_type = models.CharField(max_length=30, choices=Type.choices)
    description = models.TextField(blank=True)
    photo = models.ImageField(upload_to="incidents/", blank=True, null=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Incident {self.get_incident_type_display()} — {self.delivery}"


class DeliveryStep(models.Model):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="steps")
    label = models.CharField(max_length=100)
    status = models.CharField(max_length=20)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.label} @ {self.timestamp}"
