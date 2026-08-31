from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Compte unique multi-rôles (CDC §6.5 users)."""

    class Role(models.TextChoices):
        CLIENT = "client", "Client / Patient"
        PHARMACIST = "pharmacist", "Pharmacien"
        COURIER = "courier", "Livreur"
        ADMIN = "admin", "Administrateur"
        SUPERADMIN = "superadmin", "Super Administrateur"
        AUTHORITY = "authority", "Autorité Sanitaire"
        SUPPORT = "support", "Support"
        REGIONAL_SUPERVISOR = "regional_supervisor", "Superviseur régional"
        PARTNER = "partner", "Institution partenaire"

    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        PENDING = "pending", "En attente"
        SUSPENDED = "suspended", "Suspendu"
        INACTIVE = "inactive", "Inactif"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CLIENT)
    phone = models.CharField(max_length=20, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    is_phone_verified = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    city = models.CharField(max_length=100, blank=True, default="Libreville")
    district = models.CharField(max_length=100, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    loyalty_points = models.PositiveIntegerField(default=0)
    assigned_region = models.CharField(
        max_length=100,
        blank=True,
        help_text="Région de rattachement (superviseur régional)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"
        ordering = ["-date_joined"]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    @property
    def is_client(self):
        return self.role == self.Role.CLIENT

    @property
    def is_staff_role(self):
        return self.role in {
            self.Role.ADMIN,
            self.Role.SUPERADMIN,
            self.Role.PHARMACIST,
            self.Role.COURIER,
            self.Role.AUTHORITY,
            self.Role.SUPPORT,
            self.Role.REGIONAL_SUPERVISOR,
            self.Role.PARTNER,
        }

    def backoffice_home(self):
        mapping = {
            self.Role.SUPERADMIN: "bo_admin_dashboard",
            self.Role.ADMIN: "bo_admin_dashboard",
            self.Role.PHARMACIST: "bo_pharmacy_dashboard",
            self.Role.COURIER: "bo_courier_dashboard",
            self.Role.AUTHORITY: "bo_authority_dashboard",
            self.Role.SUPPORT: "bo_support_dashboard",
            self.Role.REGIONAL_SUPERVISOR: "bo_regional_dashboard",
            self.Role.PARTNER: "bo_insurer_dashboard",
            self.Role.CLIENT: "bo_client_dashboard",
        }
        return mapping.get(self.role, "profile")

    @property
    def display_location(self):
        parts = [p for p in (self.city, self.district) if p]
        return ", ".join(parts) if parts else "Libreville"


class ClientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="client_profile")
    date_of_birth = models.DateField(null=True, blank=True)
    insurance_number = models.CharField(max_length=50, blank=True)
    insurance_provider = models.ForeignKey(
        "payments.InsuranceProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_profiles",
    )
    emergency_contact = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    def __str__(self):
        return f"Profil client — {self.user}"


class CourierProfile(models.Model):
    class CourierStatus(models.TextChoices):
        ONLINE = "online", "En ligne"
        OFFLINE = "offline", "Hors ligne"
        BUSY = "busy", "En mission"
        SUSPENDED = "suspended", "Suspendu"

    class Level(models.TextChoices):
        BRONZE = "bronze", "Bronze"
        SILVER = "silver", "Argent"
        GOLD = "gold", "Or"
        TOP = "top", "Top livreur"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="courier_profile")
    vehicle_type = models.CharField(max_length=50, blank=True)
    vehicle_plate = models.CharField(max_length=30, blank=True)
    vehicle_label = models.CharField(
        max_length=120, blank=True, help_text="Marque et modèle affichés"
    )
    vehicle_year = models.PositiveSmallIntegerField(null=True, blank=True)
    vehicle_fuel = models.CharField(max_length=30, blank=True)
    vehicle_cc = models.PositiveSmallIntegerField(null=True, blank=True)
    vehicle_color = models.CharField(max_length=30, blank=True)
    vehicle_chassis = models.CharField(max_length=80, blank=True)
    courier_status = models.CharField(
        max_length=20, choices=CourierStatus.choices, default=CourierStatus.OFFLINE
    )
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.BRONZE)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=5.0)
    total_deliveries = models.PositiveIntegerField(default=0)
    zone = models.CharField(max_length=100, blank=True)
    pharmacy = models.ForeignKey(
        "pharmacies.Pharmacy",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="couriers",
        help_text="Pharmacie de rattachement (une seule à la fois)",
    )
    id_document = models.FileField(upload_to="couriers/docs/", blank=True, null=True)
    license_document = models.FileField(upload_to="couriers/docs/", blank=True, null=True)
    insurance_document = models.FileField(upload_to="couriers/docs/", blank=True, null=True)
    registration_document = models.FileField(upload_to="couriers/docs/", blank=True, null=True)
    technical_control_document = models.FileField(
        upload_to="couriers/docs/", blank=True, null=True
    )
    training_document = models.FileField(upload_to="couriers/docs/", blank=True, null=True)
    professional_card_document = models.FileField(
        upload_to="couriers/docs/", blank=True, null=True
    )
    eligibility_approved = models.BooleanField(
        default=False,
        help_text="Validation admin — requis pour accepter des missions",
    )
    eligibility_approved_at = models.DateTimeField(null=True, blank=True)
    eligibility_approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courier_eligibility_approvals",
    )

    def __str__(self):
        return f"Livreur — {self.user}"


class LoyaltyTransaction(models.Model):
    """Historique points fidélité (CDC §4.15)."""

    class Kind(models.TextChoices):
        EARN = "earn", "Gain"
        SPEND = "spend", "Dépense"
        EXPIRE = "expire", "Expiration"
        ADJUST = "adjust", "Ajustement"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="loyalty_transactions"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.EARN)
    points = models.IntegerField(help_text="Positif = crédit, négatif = débit")
    balance_after = models.PositiveIntegerField(default=0)
    reason = models.CharField(max_length=255, blank=True)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_transactions",
    )
    reward = models.ForeignKey(
        "LoyaltyReward",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    expiry_notified = models.BooleanField(default=False)
    expired_processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Mouvement fidélité"
        verbose_name_plural = "Mouvements fidélité"

    def __str__(self):
        return f"{self.user_id} {self.points:+d} pts"


class LoyaltyProgramSettings(models.Model):
    """Paramètres globaux du programme fidélité (CDC §4.15) — singleton pk=1."""

    points_per_1000_fcfa = models.PositiveSmallIntegerField(
        default=1,
        help_text="Points gagnés par tranche de 1000 FCFA dépensés",
    )
    points_validity_days = models.PositiveIntegerField(
        default=365,
        help_text="Durée de validité des points gagnés (jours)",
    )
    expiry_warning_days = models.PositiveSmallIntegerField(
        default=30,
        help_text="Notification avant expiration des points",
    )
    voucher_validity_days = models.PositiveSmallIntegerField(
        default=90,
        help_text="Validité d'un bon échangé (jours)",
    )
    bronze_discount_percent = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    silver_discount_percent = models.DecimalField(max_digits=4, decimal_places=2, default=2)
    gold_discount_percent = models.DecimalField(max_digits=4, decimal_places=2, default=5)

    class Meta:
        verbose_name = "Paramètres fidélité"
        verbose_name_plural = "Paramètres fidélité"

    def __str__(self):
        return "Programme fidélité Gab'Pharma"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class LoyaltyReward(models.Model):
    """Catalogue de récompenses échangeables contre des points."""

    class RewardType(models.TextChoices):
        DISCOUNT_FCFA = "discount_fcfa", "Réduction (FCFA)"
        FREE_DELIVERY = "free_delivery", "Livraison offerte"

    code = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    reward_type = models.CharField(max_length=20, choices=RewardType.choices)
    points_cost = models.PositiveIntegerField()
    value = models.PositiveIntegerField(
        default=0,
        help_text="Montant FCFA (réduction) — ignoré pour livraison offerte",
    )
    min_level = models.CharField(
        max_length=20,
        blank=True,
        help_text="Palier minimum : decouverte, bronze, argent, or",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "points_cost"]
        verbose_name = "Récompense fidélité"
        verbose_name_plural = "Récompenses fidélité"

    def __str__(self):
        return f"{self.label} ({self.points_cost} pts)"


class LoyaltyVoucher(models.Model):
    """Bon de réduction obtenu après échange de points."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        USED = "used", "Utilisé"
        EXPIRED = "expired", "Expiré"
        CANCELLED = "cancelled", "Annulé"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="loyalty_vouchers")
    reward = models.ForeignKey(LoyaltyReward, on_delete=models.PROTECT, related_name="vouchers")
    code = models.CharField(max_length=16, unique=True)
    points_spent = models.PositiveIntegerField()
    discount_amount = models.PositiveIntegerField(default=0)
    free_delivery = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_vouchers",
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Bon fidélité"
        verbose_name_plural = "Bons fidélité"

    def __str__(self):
        return f"{self.code} — {self.user_id}"


class PartnerProfile(models.Model):
    """Profil institution partenaire (CDC §4.2) — lecture seule contractuelle."""

    class PartnerType(models.TextChoices):
        INSURER = "insurer", "Assureur / Mutuelle"
        LAB = "lab", "Laboratoire"
        WHOLESALER = "wholesaler", "Grossiste"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="partner_profile")
    partner_type = models.CharField(max_length=20, choices=PartnerType.choices)
    organization_name = models.CharField(max_length=200)
    acronym = models.CharField(max_length=32, blank=True, help_text="Sigle (ex. CNAMGS)")
    registration_number = models.CharField(max_length=64, blank=True, help_text="RCCM")
    tax_id = models.CharField(max_length=64, blank=True, help_text="NIF")
    country = models.CharField(max_length=80, default="Gabon")
    headquarters_address = models.TextField(blank=True)
    rep_job_title = models.CharField(max_length=150, blank=True, help_text="Fonction du responsable")
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="validated_partners",
    )
    insurance_provider = models.ForeignKey(
        "payments.InsuranceProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="partner_users",
        help_text="Assureur lié (si type assureur)",
    )

    class Meta:
        verbose_name = "Profil partenaire"
        verbose_name_plural = "Profils partenaires"

    def __str__(self):
        return f"{self.organization_name} — {self.user}"

    @property
    def is_validated(self):
        return self.validated_at is not None

    @property
    def display_name(self):
        return self.acronym or self.organization_name


class InsurerPortalSettings(models.Model):
    """Préférences portail assureur (par compte partenaire)."""

    class Frequency(models.TextChoices):
        IMMEDIATE = "immediate", "Immédiatement"
        DAILY = "daily", "Quotidiennement"
        WEEKLY = "weekly", "Hebdomadairement"

    partner = models.OneToOneField(
        PartnerProfile,
        on_delete=models.CASCADE,
        related_name="insurer_portal_settings",
    )
    language = models.CharField(max_length=8, default="fr")
    timezone = models.CharField(max_length=64, default="Africa/Libreville")
    currency = models.CharField(max_length=8, default="XAF")
    date_format = models.CharField(max_length=16, default="d/m/Y")
    notification_frequency = models.CharField(
        max_length=16,
        choices=Frequency.choices,
        default=Frequency.IMMEDIATE,
    )
    daily_digest_time = models.TimeField(default="18:00")
    category_preferences = models.JSONField(default=dict, blank=True)
    maintenance_mode = models.BooleanField(default=False)
    session_expiry_minutes = models.PositiveSmallIntegerField(default=30)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Paramètres portail assureur"
        verbose_name_plural = "Paramètres portail assureur"

    def __str__(self):
        return f"Paramètres — {self.partner}"

    @classmethod
    def default_categories(cls) -> dict:
        return {
            "claims": True,
            "payments": True,
            "fraud": True,
            "contracts": True,
            "system": True,
            "reports": True,
        }

    @classmethod
    def for_partner(cls, partner: PartnerProfile):
        obj, created = cls.objects.get_or_create(partner=partner)
        if created or not obj.category_preferences:
            obj.category_preferences = cls.default_categories()
            obj.save(update_fields=["category_preferences"])
        return obj


class InsuranceMemberSubscription(models.Model):
    """Abonnement d'un bénéficiaire auprès d'un organisme d'assurance."""

    class Plan(models.TextChoices):
        ESSENTIEL = "essentiel", "Essentiel"
        ENTREPRISE = "entreprise", "Santé Entreprise"
        PLUS = "plus", "Santé Plus"

    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        PENDING = "pending", "En attente"
        SUSPENDED = "suspended", "Suspendu"
        EXPIRED = "expired", "Expiré"
        CANCELLED = "cancelled", "Résilié"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="insurance_subscriptions",
        null=True,
        blank=True,
    )
    client_profile = models.ForeignKey(
        ClientProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="insurance_subscriptions",
    )
    insurance_provider = models.ForeignKey(
        "payments.InsuranceProvider",
        on_delete=models.CASCADE,
        related_name="member_subscriptions",
    )
    subscription_plan = models.ForeignKey(
        "payments.InsuranceSubscriptionPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
    )
    reference = models.CharField(max_length=32, unique=True)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.ENTREPRISE)
    premium_amount = models.PositiveIntegerField(
        default=35_000,
        help_text="Cotisation FCFA",
    )
    coverage_rate = models.PositiveSmallIntegerField(default=80)
    employer_name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    starts_at = models.DateField()
    ends_at = models.DateField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Abonnement assuré"
        verbose_name_plural = "Abonnements assurés"

    def __str__(self):
        return self.reference


class PartnerSubscription(models.Model):
    """Abonnement plateforme Gab'Pharma pour un partenaire (assureur, labo…)."""

    class Plan(models.TextChoices):
        STANDARD = "standard", "Standard"
        PROFESSIONAL = "professional", "Professionnel"
        ENTERPRISE = "enterprise", "Entreprise"

    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        PENDING = "pending", "En attente de paiement"
        EXPIRED = "expired", "Expiré"
        CANCELLED = "cancelled", "Résilié"

    partner = models.ForeignKey(
        PartnerProfile,
        on_delete=models.CASCADE,
        related_name="platform_subscriptions",
    )
    plan = models.CharField(max_length=20, choices=Plan.choices)
    amount = models.PositiveIntegerField(help_text="Montant FCFA pour la période")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    starts_at = models.DateField()
    ends_at = models.DateField()
    payment_reference = models.CharField(max_length=100, blank=True)
    payment_method = models.CharField(max_length=20, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Abonnement partenaire plateforme"
        verbose_name_plural = "Abonnements partenaires plateforme"

    def __str__(self):
        return f"{self.partner} — {self.get_plan_display()}"


class AccessConfiguration(models.Model):
    """Matrice rôles/permissions plateforme (singleton — CDC §4.2)."""

    pharmacy_role_permissions = models.JSONField(
        default=dict,
        blank=True,
        help_text="Rôle métier pharmacie → liste de permissions ERP",
    )
    platform_role_modules = models.JSONField(
        default=dict,
        blank=True,
        help_text="Rôle plateforme → modules back-office admin autorisés",
    )
    portal_role_modules = models.JSONField(
        default=dict,
        blank=True,
        help_text="Rôle portail (autorité, partenaire, livreur…) → modules autorisés",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuration des accès"
        verbose_name_plural = "Configuration des accès"

    def __str__(self):
        return "Configuration des accès"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PlatformSettings(models.Model):
    """Paramètres généraux plateforme (singleton — maquette admin)."""

    class Currency(models.TextChoices):
        XAF = "XAF", "Franc CFA (XAF)"

    class Language(models.TextChoices):
        FR = "fr", "Français"
        EN = "en", "English"

    class DateFormat(models.TextChoices):
        DMY = "d/m/Y", "JJ/MM/AAAA"
        MDY = "m/d/Y", "MM/JJ/AAAA"
        YMD = "Y-m-d", "AAAA-MM-JJ"

    class TimeFormat(models.TextChoices):
        H24 = "24", "24 heures"
        H12 = "12", "12 heures"

    class BackupFrequency(models.TextChoices):
        DAILY = "daily", "Quotidienne"
        WEEKLY = "weekly", "Hebdomadaire"
        MONTHLY = "monthly", "Mensuelle"

    platform_name = models.CharField(max_length=120, default="Gab'Pharma")
    slogan = models.CharField(max_length=200, default="Votre santé, notre priorité")
    currency = models.CharField(max_length=8, choices=Currency.choices, default=Currency.XAF)
    country = models.CharField(max_length=80, default="Gabon")
    default_language = models.CharField(
        max_length=8, choices=Language.choices, default=Language.FR
    )
    date_format = models.CharField(
        max_length=16, choices=DateFormat.choices, default=DateFormat.DMY
    )
    timezone = models.CharField(max_length=64, default="Africa/Libreville")
    time_format = models.CharField(
        max_length=4, choices=TimeFormat.choices, default=TimeFormat.H24
    )
    two_factor_required = models.BooleanField(default=True)
    session_expiry_minutes = models.PositiveSmallIntegerField(default=30)
    password_complexity = models.BooleanField(default=True)
    login_attempt_limit = models.PositiveSmallIntegerField(default=5)
    activity_logging = models.BooleanField(default=True)
    auto_backup = models.BooleanField(default=True)
    backup_frequency = models.CharField(
        max_length=16,
        choices=BackupFrequency.choices,
        default=BackupFrequency.DAILY,
    )
    backup_retention_days = models.PositiveSmallIntegerField(default=30)
    scheduled_maintenance = models.BooleanField(default=False)
    integrations = models.JSONField(
        default=dict,
        blank=True,
        help_text="État des intégrations externes (paiement, assurance, livraison…)",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Paramètres plateforme"
        verbose_name_plural = "Paramètres plateforme"

    def __str__(self):
        return self.platform_name

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        if not obj.integrations:
            obj.integrations = {
                "airtel_money": {"connected": True, "label": "Airtel Money"},
                "moov_money": {"connected": True, "label": "Moov Money"},
                "cnamgs": {"connected": True, "label": "CNAMGS"},
                "ascoma": {"connected": True, "label": "ASCOMA"},
                "delivery_service": {"connected": True, "label": "Service de livraison"},
            }
            obj.save(update_fields=["integrations"])
        return obj


class AuthorityProfile(models.Model):
    """Profil institutionnel — Portail Autorités Sanitaires (CDC §3.2)."""

    class TwoFactorMethod(models.TextChoices):
        SMS = "sms", "SMS"
        EMAIL = "email", "E-mail"

    class AccessLevel(models.TextChoices):
        NATIONAL_ADMIN = "national_admin", "Administrateur National"
        REGIONAL_ADMIN = "regional_admin", "Administrateur Régional"
        NATIONAL_READER = "national_reader", "Lecteur National"
        REGIONAL_READER = "regional_reader", "Lecteur Régional"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="authority_profile")
    institution = models.CharField(max_length=200, help_text="Ministère, Direction, agence…")
    department = models.CharField(max_length=150, blank=True)
    job_title = models.CharField(max_length=150, blank=True)
    region = models.CharField(max_length=100, blank=True, help_text="Zone de compétence")
    access_level = models.CharField(
        max_length=20,
        choices=AccessLevel.choices,
        default=AccessLevel.NATIONAL_ADMIN,
    )
    authority_code = models.CharField(max_length=16, blank=True, unique=True, null=True)
    two_factor_method = models.CharField(
        max_length=10,
        choices=TwoFactorMethod.choices,
        default=TwoFactorMethod.EMAIL,
    )
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="validated_authorities",
    )

    class Meta:
        verbose_name = "Profil autorité sanitaire"
        verbose_name_plural = "Profils autorités sanitaires"

    def __str__(self):
        return f"{self.institution} — {self.user}"

    def save(self, *args, **kwargs):
        if not self.authority_code:
            self.authority_code = self._generate_authority_code()
        super().save(*args, **kwargs)

    def _generate_authority_code(self) -> str:
        last = (
            AuthorityProfile.objects.exclude(pk=self.pk)
            .exclude(authority_code="")
            .order_by("-id")
            .values_list("authority_code", flat=True)
            .first()
        )
        seq = 1
        if last and last.startswith("AUTH-"):
            try:
                seq = int(last.split("-", 1)[1]) + 1
            except ValueError:
                seq = AuthorityProfile.objects.count() + 1
        else:
            seq = AuthorityProfile.objects.count() + 1
        return f"AUTH-{seq:04d}"

    @property
    def display_code(self) -> str:
        return self.authority_code or f"AUTH-{self.pk:04d}"

    @property
    def is_validated(self):
        return self.validated_at is not None

    @property
    def access_level_badge_class(self) -> str:
        mapping = {
            self.AccessLevel.NATIONAL_ADMIN: "auth-admin-badge--national",
            self.AccessLevel.REGIONAL_ADMIN: "auth-admin-badge--regional",
            self.AccessLevel.NATIONAL_READER: "auth-admin-badge--reader",
            self.AccessLevel.REGIONAL_READER: "auth-admin-badge--reader-regional",
        }
        return mapping.get(self.access_level, "auth-admin-badge--national")

    @property
    def region_display(self) -> str:
        from core.gabon_regions import normalize_region_name

        region = normalize_region_name(self.region)
        if not region:
            if self.access_level in {
                self.AccessLevel.NATIONAL_ADMIN,
                self.AccessLevel.NATIONAL_READER,
            }:
                return "National"
            return "—"
        return region
