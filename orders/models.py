import secrets

from django.conf import settings
from django.db import models

from catalog.models import Medicine
from pharmacies.models import Pharmacy


def generate_order_code():
    return secrets.token_hex(4).upper()


def generate_validation_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_pharmacy_handoff_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def normalize_validation_code(value: str) -> str:
    """Code client à 6 chiffres (zéros en tête acceptés à la saisie)."""
    raw = (value or "").strip().replace(" ", "")
    if raw.isdigit():
        return raw.zfill(6)
    return raw


def ensure_validation_code_normalized(order, *, save: bool = False) -> str:
    """Aligne le code stocké sur 6 chiffres (anciennes commandes à 4 chiffres)."""
    normalized = normalize_validation_code(order.validation_code)
    if normalized and normalized != order.validation_code:
        order.validation_code = normalized
        if save:
            order.save(update_fields=["validation_code", "updated_at"])
    return normalized or order.validation_code


class Order(models.Model):
    class Status(models.TextChoices):
        CART = "cart", "Panier"
        PENDING = "pending", "En attente"
        AWAITING_RX = "awaiting_rx", "Ordonnance à valider"
        CONFIRMED = "confirmed", "Confirmée"
        PREPARING = "preparing", "À préparer"
        READY = "ready", "Prête"
        DELIVERING = "delivering", "En livraison"
        DELIVERED = "delivered", "Livrée"
        CANCELLED = "cancelled", "Annulée"
        REFUNDED = "refunded", "Remboursée"

    class DeliveryMode(models.TextChoices):
        HOME = "home", "Livraison à domicile"
        PICKUP = "pickup", "Retrait en pharmacie"
        EXPRESS = "express", "Livraison express"

    code = models.CharField(max_length=20, unique=True, default=generate_order_code)
    validation_code = models.CharField(max_length=6, default=generate_validation_code)
    pharmacy_handoff_code = models.CharField(
        max_length=6,
        blank=True,
        help_text="Code pharmacie — remise du colis au livreur (traçabilité)",
    )
    pharmacy_handoff_at = models.DateTimeField(null=True, blank=True)
    pharmacy_handoff_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pharmacy_handoffs_validated",
    )
    preparing_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Début préparation — décompte affiché au client",
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders"
    )
    pharmacy = models.ForeignKey(
        Pharmacy, on_delete=models.PROTECT, related_name="orders", null=True, blank=True
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CART)
    delivery_mode = models.CharField(
        max_length=20, choices=DeliveryMode.choices, default=DeliveryMode.HOME
    )
    delivery_address = models.TextField(blank=True)
    delivery_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    subtotal = models.PositiveIntegerField(default=0)
    delivery_fee = models.PositiveIntegerField(default=0)
    discount = models.PositiveIntegerField(default=0)
    insurance_coverage = models.PositiveIntegerField(default=0)
    insurance_provider = models.ForeignKey(
        "payments.InsuranceProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    deposit_amount = models.PositiveIntegerField(default=0, help_text="Acompte obligatoire COD")
    loyalty_discount = models.PositiveIntegerField(
        default=0,
        help_text="Réduction fidélité (bon ou palier)",
    )
    loyalty_voucher = models.ForeignKey(
        "accounts.LoyaltyVoucher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applied_orders",
    )
    total = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    cancellation_reason = models.TextField(
        blank=True, help_text="Motif de refus / annulation par la pharmacie"
    )
    is_urgent = models.BooleanField(default=False)
    prescription = models.FileField(upload_to="prescriptions/", blank=True, null=True)
    linked_prescription = models.ForeignKey(
        "Prescription",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Commande {self.code}"

    @property
    def requires_prescription(self):
        return self.items.filter(medicine__requires_prescription=True).exists()

    @property
    def validation_code_display(self) -> str:
        return normalize_validation_code(self.validation_code)

    def recalculate_totals(self):
        self.subtotal = sum(item.line_total for item in self.items.all())
        loyalty = int(self.loyalty_discount or 0)
        self.total = max(
            0,
            self.subtotal + self.delivery_fee - self.discount - loyalty - self.insurance_coverage,
        )
        self.save(update_fields=["subtotal", "total", "updated_at"])


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.PositiveIntegerField()
    medicine_name = models.CharField(max_length=200)

    class Meta:
        verbose_name = "Ligne de commande"
        verbose_name_plural = "Lignes de commande"

    def __str__(self):
        return f"{self.quantity}× {self.medicine_name}"

    @property
    def line_total(self):
        return self.quantity * self.unit_price


class OrderEvaluation(models.Model):
    """Avis client post-livraison — pharmacie et livreur (CDC §4.7)."""

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="delivery_evaluation")
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="order_evaluations",
    )
    pharmacy = models.ForeignKey(
        Pharmacy,
        on_delete=models.CASCADE,
        related_name="order_evaluations",
    )
    pharmacy_rating = models.PositiveSmallIntegerField()
    pharmacy_comment = models.TextField(blank=True)
    courier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courier_evaluations_received",
    )
    courier_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    courier_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Évaluation commande"
        verbose_name_plural = "Évaluations commande"

    def __str__(self):
        return f"{self.order.code} — pharmacie {self.pharmacy_rating}/5"


class Prescription(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon (prévisualisation)"
        PENDING = "pending", "En attente de validation"
        VALIDATED = "validated", "Validée"
        REJECTED = "rejected", "Rejetée"
        USED = "used", "Utilisée"

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="prescriptions"
    )
    pharmacy = models.ForeignKey(
        Pharmacy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prescriptions_to_review",
    )
    file = models.FileField(upload_to="prescriptions/")
    doctor_name = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    review_notes = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Ordonnance {self.id} — {self.client}"

    @property
    def is_image(self):
        name = (self.file.name or "").lower()
        return name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))

    @property
    def is_pdf(self):
        return (self.file.name or "").lower().endswith(".pdf")
