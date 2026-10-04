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
    delivery_fee_waived = models.PositiveIntegerField(
        default=0,
        help_text="Frais de livraison offerts (Premium) — pris en charge par la plateforme",
    )
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
    file_sha256 = models.CharField(
        max_length=64,
        blank=True,
        help_text="Empreinte du fichier, conservée après purge comme preuve de vérification",
    )
    purged_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fichier supprimé (durée de conservation dépassée)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Ordonnance {self.id} — {self.client}"

    @property
    def is_purged(self):
        return bool(self.purged_at) or not self.file

    @property
    def is_image(self):
        name = (self.file.name or "").lower()
        return name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))

    @property
    def is_pdf(self):
        return (self.file.name or "").lower().endswith(".pdf")


class PrescriptionAccessLog(models.Model):
    """Qui a consulté quelle ordonnance (visible par le patient)."""

    class Action(models.TextChoices):
        VIEW = "view", "Consultation"
        BREAK_GLASS = "break_glass", "Accès exceptionnel"
        PURGE = "purge", "Suppression automatique"

    prescription = models.ForeignKey(
        Prescription,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="access_logs",
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="prescription_access_logs",
    )
    coverage_request = models.ForeignKey(
        "payments.CoverageRequest",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="prescription_access_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    actor_label = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=20, choices=Action.choices, default=Action.VIEW)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Accès ordonnance"
        verbose_name_plural = "Accès ordonnances"
        indexes = [models.Index(fields=["user", "action", "created_at"])]

    def __str__(self):
        return f"{self.get_action_display()} — {self.actor_label or self.user_id}"


def generate_counter_sale_code():
    return f"VC-{secrets.token_hex(3).upper()}"


class CounterSale(models.Model):
    """Vente enregistrée par la pharmacie hors application (comptoir, téléphone, WhatsApp…).

    Aucune commission Gab'Pharma ; sert au suivi du stock et du chiffre d'affaires.
    Aucune ordonnance n'est stockée : le pharmacien atteste seulement avoir vu l'original.
    """

    class Channel(models.TextChoices):
        COUNTER = "counter", "Comptoir"
        PHONE = "phone", "Téléphone"
        WHATSAPP = "whatsapp", "WhatsApp"
        OTHER = "other", "Autre"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Espèces"
        MOOV = "moov", "Moov Money"
        AIRTEL = "airtel", "Airtel Money"
        CARD = "card", "Carte bancaire"
        INSURANCE = "insurance", "Tiers payant assurance"
        CREDIT = "credit", "Crédit client"

    class Status(models.TextChoices):
        COMPLETED = "completed", "Validée"
        CANCELLED = "cancelled", "Annulée"

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name="counter_sales")
    code = models.CharField(max_length=12, unique=True, default=generate_counter_sale_code)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.COUNTER)
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    insurance_provider = models.ForeignKey(
        "payments.InsuranceProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="counter_sales",
    )
    subtotal = models.PositiveIntegerField(default=0)
    discount = models.PositiveIntegerField(default=0)
    insurance_amount = models.PositiveIntegerField(
        default=0, help_text="Part prise en charge par l'assurance (FCFA)"
    )
    insured_number = models.CharField(
        max_length=50, blank=True, help_text="N° d'assuré (tiers payant)"
    )
    total = models.PositiveIntegerField(default=0, help_text="Montant encaissé auprès du client")
    prescription_checked = models.BooleanField(
        default=False, help_text="Ordonnance originale vérifiée au comptoir (non stockée)"
    )
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    cancellation_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="counter_sales",
    )
    # Caisse hors connexion : idempotence et date réelle de la vente sur l'appareil
    client_uuid = models.UUIDField(null=True, blank=True, unique=True)
    client_created_at = models.DateTimeField(null=True, blank=True)
    recorded_offline = models.BooleanField(default=False)
    sync_warning = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Vente hors app"
        verbose_name_plural = "Ventes hors app"
        indexes = [models.Index(fields=["pharmacy", "created_at"])]

    def __str__(self):
        return f"{self.code} — {self.total} F"

    @property
    def gross_total(self):
        return self.total + self.insurance_amount

    @property
    def sold_at(self):
        """Date réelle de la vente (appareil hors connexion) sinon date d'enregistrement."""
        return self.client_created_at or self.created_at


class CounterSaleItem(models.Model):
    sale = models.ForeignKey(CounterSale, on_delete=models.CASCADE, related_name="items")
    stock = models.ForeignKey(
        "catalog.PharmacyStock",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="counter_sale_items",
    )
    medicine = models.ForeignKey(Medicine, on_delete=models.SET_NULL, null=True, blank=True)
    medicine_name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.PositiveIntegerField()
    requires_prescription = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]

    @property
    def line_total(self):
        return self.quantity * self.unit_price
