from django.conf import settings
from django.db import models

from orders.models import Order
from pharmacies.models import Pharmacy


class Payment(models.Model):
    class Method(models.TextChoices):
        MOOV = "moov", "Moov Money"
        AIRTEL = "airtel", "Airtel Money"
        CARD = "card", "Carte bancaire"
        COD = "cod", "Paiement à la livraison"
        PHARMACY = "pharmacy", "Paiement en pharmacie"
        INSURANCE = "insurance", "Assurance maladie"
        TRANSFER = "transfer", "Virement"

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        PROCESSING = "processing", "En cours"
        SUCCESS = "success", "Réussi"
        FAILED = "failed", "Échoué"
        REFUNDED = "refunded", "Remboursé"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=20, choices=Method.choices)
    amount = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reference = models.CharField(max_length=100, blank=True)
    is_deposit = models.BooleanField(default=False, help_text="Acompte COD")
    provider_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Paiement {self.reference or self.id} — {self.amount} FCFA"


class Subscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        PENDING = "pending", "En attente de paiement"
        EXPIRED = "expired", "Expiré"
        CANCELLED = "cancelled", "Résilié"

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.CharField(max_length=20, choices=Pharmacy.SubscriptionPlan.choices)
    amount = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    starts_at = models.DateField()
    ends_at = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.pharmacy} — {self.plan}"


class InsuranceProvider(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    logo = models.ImageField(upload_to="insurance/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    coverage_rate = models.PositiveIntegerField(default=80, help_text="% de prise en charge")
    annual_cap = models.PositiveIntegerField(
        default=500_000,
        help_text="Plafond annuel de prise en charge par bénéficiaire (FCFA)",
    )

    def __str__(self):
        return self.name


class InsuranceSubscriptionPlan(models.Model):
    """Formule d'abonnement proposée par un organisme d'assurance."""

    class BillingPeriod(models.TextChoices):
        MONTHLY = "monthly", "Mensuel"
        ANNUAL = "annual", "Annuel"

    provider = models.ForeignKey(
        InsuranceProvider,
        on_delete=models.CASCADE,
        related_name="subscription_plans",
    )
    code = models.CharField(max_length=32, help_text="essentiel, entreprise, plus…")
    name = models.CharField(max_length=120)
    premium_amount = models.PositiveIntegerField(help_text="Cotisation FCFA par période")
    coverage_rate = models.PositiveSmallIntegerField(default=80)
    billing_period = models.CharField(
        max_length=12,
        choices=BillingPeriod.choices,
        default=BillingPeriod.MONTHLY,
    )
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Formule d'abonnement assurance"
        verbose_name_plural = "Formules d'abonnement assurance"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "code"],
                name="uniq_insurance_plan_per_provider",
            ),
        ]

    def __str__(self):
        return f"{self.provider.code} — {self.name}"


class InsuranceClaim(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        APPROVED = "approved", "Approuvée"
        REJECTED = "rejected", "Rejetée"
        PAID = "paid", "Payée"

    client = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    provider = models.ForeignKey(InsuranceProvider, on_delete=models.PROTECT)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="insurance_claims")
    amount = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    review_notes = models.TextField(blank=True)
    review_attachment = models.FileField(
        upload_to="insurance/claims/reviews/",
        blank=True,
        null=True,
        help_text="Document joint par l'assureur (décision)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Remboursement {self.provider} — {self.amount} FCFA"


class InsurerFraudAlert(models.Model):
    """Alerte fraude persistée — portail compagnie d'assurance."""

    class AlertType(models.TextChoices):
        SUSPICIOUS = "suspicious", "Demande suspecte"
        OVERBILLING = "overbilling", "Sur-facturation"
        DOCUMENT = "document", "Document suspect"
        ABUSE = "abuse", "Utilisation anormale"

    class Level(models.TextChoices):
        CRITICAL = "critical", "Critique"
        HIGH = "high", "Élevé"
        MEDIUM = "medium", "Moyen"
        LOW = "low", "Faible"

    class Status(models.TextChoices):
        OPEN = "open", "En cours"
        ANALYSIS = "analysis", "En analyse"
        RESOLVED = "resolved", "Résolue"

    insurance_provider = models.ForeignKey(
        InsuranceProvider,
        on_delete=models.CASCADE,
        related_name="fraud_alerts",
    )
    claim = models.ForeignKey(
        InsuranceClaim,
        on_delete=models.CASCADE,
        related_name="fraud_alerts",
        null=True,
        blank=True,
    )
    reference = models.CharField(max_length=32, unique=True)
    alert_type = models.CharField(max_length=20, choices=AlertType.choices)
    level = models.CharField(max_length=10, choices=Level.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    holder_name = models.CharField(max_length=200)
    holder_sub = models.CharField(max_length=120, blank=True)
    detail = models.TextField(blank=True)
    detected_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-detected_at"]
        verbose_name = "Alerte fraude assureur"
        verbose_name_plural = "Alertes fraude assureur"

    def __str__(self):
        return self.reference


class PatientAccessPurchase(models.Model):
    """Forfaits patient — recherche payante (CDC §4 / maquettes Souscription & Forfaits)."""

    class Plan(models.TextChoices):
        PASS_1H = "pass_1h", "Pass 1 heure"
        STANDARD_DAY = "standard_day", "Standard journée"
        PREMIUM = "premium", "Premium"

    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        EXPIRED = "expired", "Expiré"
        CANCELLED = "cancelled", "Annulé"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access_purchases",
    )
    plan = models.CharField(max_length=20, choices=Plan.choices)
    amount = models.PositiveIntegerField()
    payment_method = models.CharField(max_length=20, choices=Payment.Method.choices)
    reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    purchased_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    first_search_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-purchased_at"]
        verbose_name = "Forfait patient"
        verbose_name_plural = "Forfaits patient"

    def __str__(self):
        return f"{self.user_id} — {self.get_plan_display()} ({self.get_status_display()})"


class PlatformPaymentSettings(models.Model):
    """Paramètres financiers globaux (CDC §4.8) — singleton pk=1."""

    platform_commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=5,
        help_text="Commission plateforme sur le panier (%)",
    )
    payout_delay_days = models.PositiveSmallIntegerField(
        default=2,
        help_text="Délai de versement pharmacie (jours)",
    )
    daily_transaction_cap = models.PositiveIntegerField(
        default=500_000,
        help_text="Plafond paiement client / jour (FCFA)",
    )
    cod_deposit_rate = models.PositiveSmallIntegerField(
        default=20,
        help_text="Acompte COD (% du reste à charge)",
    )
    cod_deposit_min = models.PositiveIntegerField(
        default=500,
        help_text="Acompte COD minimum (FCFA)",
    )
    courier_base_fee = models.PositiveIntegerField(
        default=1_500,
        help_text="Rémunération de base par course (FCFA)",
    )
    courier_per_km_fee = models.PositiveIntegerField(
        default=200,
        help_text="Bonus distance par km (FCFA)",
    )
    courier_express_bonus = models.PositiveIntegerField(
        default=500,
        help_text="Bonus livraison express (FCFA)",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Paramètres paiement plateforme"
        verbose_name_plural = "Paramètres paiement plateforme"

    def __str__(self):
        return "Paramètres paiement"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class OrderSettlement(models.Model):
    """Ventilation financière d'une commande (commission, pharmacie, livreur)."""

    class PharmacyPayoutStatus(models.TextChoices):
        PENDING = "pending", "En attente"
        SCHEDULED = "scheduled", "Programmé"
        PAID = "paid", "Versé"

    class CourierPayoutStatus(models.TextChoices):
        PENDING = "pending", "En attente"
        PAID = "paid", "Versé"
        NA = "na", "Sans livraison"

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="settlement")
    product_base = models.PositiveIntegerField(default=0, help_text="Sous-total − remise")
    platform_commission = models.PositiveIntegerField(default=0)
    pharmacy_net = models.PositiveIntegerField(default=0)
    delivery_fee = models.PositiveIntegerField(default=0)
    courier_earning = models.PositiveIntegerField(default=0)
    platform_delivery_margin = models.PositiveIntegerField(default=0)
    pharmacy_payout_status = models.CharField(
        max_length=20,
        choices=PharmacyPayoutStatus.choices,
        default=PharmacyPayoutStatus.PENDING,
    )
    courier_payout_status = models.CharField(
        max_length=20,
        choices=CourierPayoutStatus.choices,
        default=CourierPayoutStatus.NA,
    )
    payout_due_at = models.DateTimeField(null=True, blank=True)
    pharmacy_paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Règlement {self.order.code}"


class CourierEarning(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        PAID = "paid", "Versé"

    delivery = models.OneToOneField(
        "deliveries.Delivery",
        on_delete=models.CASCADE,
        related_name="earning",
    )
    courier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="courier_earnings",
    )
    base_fee = models.PositiveIntegerField(default=0)
    distance_bonus = models.PositiveIntegerField(default=0)
    express_bonus = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Gain livreur {self.total} F — {self.delivery_id}"
