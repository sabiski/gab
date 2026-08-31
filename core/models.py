from django.conf import settings
from django.db import models
from django.utils import timezone

from pharmacies.models import Pharmacy


class SiteHero(models.Model):
    """Contenu configurable du hero de la page d'accueil (singleton logique)."""

    eyebrow = models.CharField(
        max_length=120,
        default="Votre pharmacie en ligne gabonaise",
        verbose_name="Sur-titre",
    )
    title = models.CharField(
        max_length=200,
        default="Médicaments & parapharmacie,",
        verbose_name="Titre (ligne 1)",
    )
    title_accent = models.CharField(
        max_length=120,
        default="à portée de clic",
        verbose_name="Titre accentué (ligne 2)",
    )
    description = models.TextField(
        default=(
            "Recherchez un médicament, comparez les pharmacies partenaires "
            "et faites-vous livrer à Libreville — en toute sécurité."
        ),
    )
    cta1_label = models.CharField(max_length=80, default="Voir tous les médicaments")
    cta1_url = models.CharField(max_length=255, default="/recherche/")
    cta2_label = models.CharField(max_length=80, default="Pharmacies à proximité")
    cta2_url = models.CharField(max_length=255, default="/pharmacies/")
    hero_image = models.ImageField(
        upload_to="site/hero/",
        blank=True,
        null=True,
        help_text="Image / logo affiché à droite du hero. Si vide, logo plateforme par défaut.",
    )
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Hero accueil"
        verbose_name_plural = "Hero accueil"

    def __str__(self):
        return "Hero page d'accueil"

    @classmethod
    def get_solo(cls):
        obj = cls.objects.filter(is_active=True).order_by("-updated_at").first()
        if obj:
            return obj
        return cls.objects.create()


class PharmacistTip(models.Model):
    """Conseils rédigés par les pharmacies, affichés sur le site public."""

    pharmacy = models.ForeignKey(
        Pharmacy, on_delete=models.CASCADE, related_name="tips", null=True, blank=True
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pharmacist_tips",
    )
    category = models.CharField(max_length=80, blank=True, default="Santé")
    title = models.CharField(max_length=200)
    excerpt = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    cover_image = models.ImageField(upload_to="tips/", blank=True, null=True)
    icon = models.CharField(
        max_length=50,
        blank=True,
        default="health_and_safety",
        help_text="Nom d'icône Material Symbols si pas d'image",
    )
    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conseil pharmacien"
        verbose_name_plural = "Conseils pharmaciens"
        ordering = ["-published_at", "-created_at"]

    def __str__(self):
        return self.title


class Advertisement(models.Model):
    """Bannières publicitaires gérées par l'admin."""

    class Placement(models.TextChoices):
        HOME_TOP = "home_top", "Accueil — sous le hero"
        HOME_MID = "home_mid", "Accueil — milieu de page"
        HOME_BOTTOM = "home_bottom", "Accueil — bas de page"
        CATALOG = "catalog", "Catalogue"
        SIDEBAR = "sidebar", "Latéral"

    title = models.CharField(max_length=150)
    image = models.ImageField(upload_to="banners/")
    link_url = models.CharField(max_length=500, blank=True)
    placement = models.CharField(
        max_length=30, choices=Placement.choices, default=Placement.HOME_MID
    )
    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=0, help_text="Plus élevé = affiché en premier")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Publicité"
        verbose_name_plural = "Publicités"
        ordering = ["-priority", "-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_currently_active(self):
        if not self.is_active:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


class HealthCampaign(models.Model):
    """Campagne nationale de santé publique (pilotage MSNS / OMS — contexte Gabon)."""

    class Theme(models.TextChoices):
        VACCINATION = "vaccination", "Vaccination (PEV / fièvre jaune, rougeole…)"
        MALARIA = "malaria", "Lutte antipaludisme (MILDA, TPI)"
        MATERNAL = "maternal", "Santé maternelle et néonatale"
        CHOLERA = "cholera", "Prévention choléra & eau potable"
        HIV = "hiv", "VIH / dépistage & prévention"
        NCD = "ncd", "Maladies non transmissibles (diabète, HTA)"
        NUTRITION = "nutrition", "Nutrition infantile"
        TOBACCO = "tobacco", "Lutte anti-tabac"
        OTHER = "other", "Autre campagne"

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        PLANNED = "planned", "Planifiée"
        ACTIVE = "active", "En cours"
        COMPLETED = "completed", "Terminée"
        PAUSED = "paused", "Suspendue"

    code = models.CharField(max_length=30, unique=True, blank=True)
    title = models.CharField(max_length=200)
    theme = models.CharField(max_length=30, choices=Theme.choices, default=Theme.VACCINATION)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    description = models.TextField(blank=True)
    partner = models.CharField(
        max_length=120,
        blank=True,
        help_text="Partenaire : MSNS, OMS, UNICEF, CNAMGS…",
    )
    region_slugs = models.JSONField(
        default=list,
        blank=True,
        help_text="Provinces ciblées (slugs gabon-provinces). Vide = national.",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    target_population = models.PositiveIntegerField(default=0)
    people_reached = models.PositiveIntegerField(default=0)
    doses_target = models.PositiveIntegerField(default=0, blank=True)
    doses_administered = models.PositiveIntegerField(default=0, blank=True)
    pharmacies_involved = models.PositiveIntegerField(
        default=0,
        help_text="Points de distribution déclarés (pharmacies partenaires).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="health_campaigns_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Campagne de santé"
        verbose_name_plural = "Campagnes de santé"
        ordering = ["-start_date", "-created_at"]

    def save(self, *args, **kwargs):
        if not self.code:
            import secrets

            year = timezone.now().year
            self.code = f"CAMP-{year}-{secrets.randbelow(1_000_000):06d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} — {self.title}"

    @property
    def coverage_pct(self) -> float:
        if not self.target_population:
            return 0.0
        return round((self.people_reached / self.target_population) * 100, 1)

    @property
    def is_active_now(self) -> bool:
        if self.status != self.Status.ACTIVE:
            return False
        today = timezone.now().date()
        return self.start_date <= today <= self.end_date
