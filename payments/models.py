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
    # Tarif livraison facturé au client : base + km + supplément express.
    courier_base_fee = models.PositiveIntegerField(
        default=1_500,
        help_text="Frais de livraison de base par course (FCFA)",
    )
    courier_per_km_fee = models.PositiveIntegerField(
        default=200,
        help_text="Supplément distance par km pharmacie → client (FCFA)",
    )
    courier_express_bonus = models.PositiveIntegerField(
        default=500,
        help_text="Supplément livraison express (FCFA)",
    )
    delivery_max_km = models.PositiveSmallIntegerField(
        default=30,
        help_text="Distance maximale pharmacie → client pour une livraison (km)",
    )
    delivery_platform_rate = models.PositiveSmallIntegerField(
        default=25,
        help_text="Commission plateforme sur les frais de livraison (%) — le livreur perçoit le reste",
    )
    premium_free_delivery_min_order = models.PositiveIntegerField(
        default=5_000,
        help_text="Panier minimum pour la livraison offerte Premium (FCFA, 0 = toujours offerte)",
    )
    coverage_request_fee = models.PositiveIntegerField(
        default=500,
        help_text="Frais de traitement d'un dossier de prise en charge (FCFA)",
    )
    premium_coverage_monthly_quota = models.PositiveSmallIntegerField(
        default=3,
        help_text="Dossiers de prise en charge inclus par mois pour les Premium",
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


class CoverageRequest(models.Model):
    """Demande de prise en charge patient (dossier PC-XXXXXX)."""

    class Status(models.TextChoices):
        SENT = "sent", "Envoyée"
        UNDER_REVIEW = "under_review", "En vérification"
        INFO_NEEDED = "info_needed", "Informations complémentaires"
        ACCEPTED = "accepted", "Acceptée"
        REJECTED = "rejected", "Refusée"
        EXPIRED = "expired", "Expirée"
        READY = "ready", "Préparée"
        COMPLETED = "completed", "Terminée"

    class RequestType(models.TextChoices):
        PHARMACY = "pharmacy", "Pharmacie"
        REFILL = "refill", "Renouvellement"
        EMERGENCY = "emergency", "Urgence"
        OTHER = "other", "Autre"

    reference = models.CharField(max_length=20, unique=True)
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coverage_requests",
    )
    provider = models.ForeignKey(
        InsuranceProvider,
        on_delete=models.PROTECT,
        related_name="coverage_requests",
    )
    pharmacy = models.ForeignKey(
        Pharmacy,
        on_delete=models.PROTECT,
        related_name="coverage_requests",
    )
    subscription = models.ForeignKey(
        "accounts.InsuranceMemberSubscription",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coverage_requests",
    )
    insured_number = models.CharField(max_length=50)
    request_type = models.CharField(
        max_length=20,
        choices=RequestType.choices,
        default=RequestType.PHARMACY,
    )
    desired_date = models.DateField(null=True, blank=True)
    prescription_file = models.FileField(
        upload_to="coverage/prescriptions/",
        blank=True,
        null=True,
    )
    prescription_encrypted = models.BooleanField(
        default=False,
        help_text="Ordonnance chiffrée au repos (Fernet)",
    )
    prescription_sha256 = models.CharField(max_length=64, blank=True)
    prescription_purged_at = models.DateTimeField(null=True, blank=True)
    prescription_date = models.DateField(null=True, blank=True)
    doctor_name = models.CharField(max_length=200, blank=True)
    comment = models.TextField(blank=True)
    estimated_amount = models.PositiveIntegerField(
        default=0,
        help_text="Montant estimatif ordonnance (FCFA)",
    )
    coverage_rate = models.PositiveSmallIntegerField(
        default=80,
        help_text="Taux de couverture appliqué (%)",
    )
    insurance_amount = models.PositiveIntegerField(default=0)
    patient_amount = models.PositiveIntegerField(default=0)
    uncovered_amount = models.PositiveIntegerField(default=0)
    delivered_amount = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Montant réellement délivré par la pharmacie (FCFA)",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SENT,
    )
    rejection_reason = models.TextField(blank=True)
    info_request_message = models.TextField(blank=True)
    patient_info_reply = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    pharmacy_notified_at = models.DateTimeField(null=True, blank=True)
    prepared_at = models.DateTimeField(null=True, blank=True)
    processing_fee = models.PositiveIntegerField(
        default=0,
        help_text="Frais de traitement payés par le patient (FCFA)",
    )
    fee_included_premium = models.BooleanField(
        default=False,
        help_text="Dossier inclus dans le quota mensuel Premium",
    )
    fee_payment_method = models.CharField(
        max_length=20,
        choices=Payment.Method.choices,
        blank=True,
    )
    fee_payment_reference = models.CharField(max_length=100, blank=True)
    fee_paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Demande de prise en charge"
        verbose_name_plural = "Demandes de prise en charge"

    def __str__(self):
        return self.reference

    @property
    def total_amount(self) -> int:
        if self.delivered_amount is not None:
            return int(self.delivered_amount)
        return int(self.estimated_amount or 0)


class CoverageRequestEvent(models.Model):
    """Historique d'actions sur une demande PC."""

    coverage_request = models.ForeignKey(
        CoverageRequest,
        on_delete=models.CASCADE,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coverage_request_events",
    )
    action = models.CharField(max_length=60)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.coverage_request_id} — {self.action}"


class CoverageInvoice(models.Model):
    """Facture générée après validation d'une prise en charge (FAC-XXXXXX)."""

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "En attente"
        PARTIAL = "partial", "Partiel"
        PAID = "paid", "Payé"
        WAIVED = "waived", "Sans reste à charge"

    reference = models.CharField(max_length=20, unique=True)
    coverage_request = models.OneToOneField(
        CoverageRequest,
        on_delete=models.CASCADE,
        related_name="invoice",
    )
    total = models.PositiveIntegerField(default=0)
    insurance_share = models.PositiveIntegerField(default=0)
    patient_share = models.PositiveIntegerField(default=0)
    uncovered_share = models.PositiveIntegerField(default=0)
    commission = models.PositiveIntegerField(
        default=0,
        help_text="Commission Gab'Pharma (FCFA)",
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    payment_method = models.CharField(
        max_length=20,
        choices=Payment.Method.choices,
        blank=True,
    )
    payment_reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    pdf_file = models.FileField(
        upload_to="coverage/invoices/",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Facture prise en charge"
        verbose_name_plural = "Factures prise en charge"

    def __str__(self):
        return self.reference


class CoverageInvoiceLine(models.Model):
    invoice = models.ForeignKey(
        CoverageInvoice,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.PositiveIntegerField(default=0)
    line_total = models.PositiveIntegerField(default=0)
    is_covered = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class CoverageAccessLog(models.Model):
    """Journalisation des consultations / modifications de dossiers sensibles."""

    class Action(models.TextChoices):
        VIEW = "view", "Consultation"
        DOWNLOAD = "download", "Téléchargement"
        MODIFY = "modify", "Modification"
        PRESCRIPTION = "prescription", "Ordonnance consultée"
        PURGE = "purge", "Ordonnance supprimée (conservation)"

    coverage_request = models.ForeignKey(
        CoverageRequest,
        on_delete=models.CASCADE,
        related_name="access_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coverage_access_logs",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    details = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} — {self.coverage_request_id}"


class InsuranceStatement(models.Model):
    """Relevé mensuel de tiers payant d'une pharmacie pour un assureur.

    Regroupe les parts assurance des ventes hors app, des commandes app et des
    prises en charge (PC) de la période. Les montants sont figés à la génération.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        SENT = "sent", "Envoyé à l'assureur"
        PARTIAL = "partial", "Payé partiellement"
        PAID = "paid", "Payé"

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name="insurance_statements")
    provider = models.ForeignKey(InsuranceProvider, on_delete=models.PROTECT, related_name="statements")
    period = models.DateField(help_text="Premier jour du mois couvert")
    reference = models.CharField(max_length=30, unique=True)
    counter_total = models.PositiveIntegerField(default=0)
    order_total = models.PositiveIntegerField(default=0)
    coverage_total = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    line_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    sent_at = models.DateTimeField(null=True, blank=True)
    paid_amount = models.PositiveIntegerField(default=0)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period", "provider__name"]
        verbose_name = "Relevé de tiers payant"
        verbose_name_plural = "Relevés de tiers payant"
        constraints = [
            models.UniqueConstraint(fields=["pharmacy", "provider", "period"], name="uniq_statement_period")
        ]

    def __str__(self):
        return self.reference

    @property
    def balance(self) -> int:
        return max(0, self.total - self.paid_amount)
