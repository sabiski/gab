from django.conf import settings
from django.db import models

from pharmacies.models import Pharmacy


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Nom d'icône Lucide/Heroicons")
    color = models.CharField(max_length=30, default="purple")
    bg_color = models.CharField(max_length=30, default="violet")
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children"
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Catégorie"
        verbose_name_plural = "Catégories"
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class Medicine(models.Model):
    """Référentiel national des médicaments (CDC medicines)."""

    class Form(models.TextChoices):
        TABLET = "tablet", "Comprimé"
        CAPSULE = "capsule", "Gélule"
        SYRUP = "syrup", "Sirop"
        INJECTION = "injection", "Injectable"
        CREAM = "cream", "Crème"
        SPRAY = "spray", "Spray"
        GEL = "gel", "Gel"
        DROPS = "drops", "Gouttes"
        SACHET = "sachet", "Sachet"
        OTHER = "other", "Autre"

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    dci = models.CharField(max_length=200, blank=True, verbose_name="Principe actif (DCI)")
    laboratory = models.CharField(max_length=150, blank=True)
    form = models.CharField(max_length=20, choices=Form.choices, default=Form.TABLET)
    dosage = models.CharField(max_length=100, blank=True, help_text="Dosage médicament (ex. 500 mg)")
    contenance = models.CharField(
        max_length=80, blank=True, help_text="Contenance / format (ex. 300 ml, 8 comprimés)"
    )
    tagline = models.CharField(
        max_length=255,
        blank=True,
        help_text="Accroche courte (ex. Apaise et ré-équilibre — peaux sensibles)",
    )
    description = models.TextField(blank=True)
    presentation = models.TextField(blank=True, help_text="Présentation détaillée du produit")
    composition = models.TextField(blank=True, help_text="Composition / ingrédients")
    usage_advice = models.TextField(blank=True, help_text="Conseils d'utilisation")
    pharmacist_advice = models.TextField(blank=True, help_text="Avis / conseil du pharmacien")
    recommended_by = models.CharField(
        max_length=150, blank=True, help_text="Ex. Dermatologues, pédiatres…"
    )
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="medicines"
    )
    image = models.ImageField(upload_to="medicines/", blank=True, null=True)
    requires_prescription = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    is_top = models.BooleanField(default=False)
    barcode = models.CharField(max_length=50, blank=True)
    # Si renseigné : médicament proposé par une pharmacie (complément catalogue)
    created_by_pharmacy = models.ForeignKey(
        Pharmacy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contributed_medicines",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Médicament"
        verbose_name_plural = "Médicaments"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} {self.dosage}".strip()

    @property
    def form_label(self):
        return self.get_form_display()

    @property
    def format_label(self):
        """Contenance affichée (priorité contenance, sinon dosage)."""
        return self.contenance or self.dosage


class PharmacyStock(models.Model):
    """Stock par pharmacie (CDC pharmacy_medicine_stock)."""

    class Availability(models.TextChoices):
        AVAILABLE = "available", "Disponible"
        LOW = "low", "Stock faible"
        OUT = "out", "Rupture"

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name="stocks")
    medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE, related_name="stocks")
    quantity = models.PositiveIntegerField(default=0)
    price = models.PositiveIntegerField(help_text="Prix en FCFA")
    purchase_price = models.PositiveIntegerField(
        default=0,
        help_text="Prix d'achat FCFA (marge stats) — 0 = estimation auto",
    )
    promotional_price = models.PositiveIntegerField(null=True, blank=True)
    lot_number = models.CharField(max_length=50, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    low_stock_threshold = models.PositiveIntegerField(default=10)
    is_visible = models.BooleanField(default=True)
    pharmacist_advice = models.TextField(
        blank=True, help_text="Conseil du pharmacien pour cette offre"
    )
    pharmacist_name = models.CharField(max_length=120, blank=True)
    pharmacist_title = models.CharField(
        max_length=120, blank=True, default="Docteur en pharmacie"
    )
    pharmacist_rpps = models.CharField(max_length=30, blank=True, help_text="N° RPPS / ordre")
    delivery_promise = models.CharField(
        max_length=255,
        blank=True,
        help_text="Ex. Livré demain pour toute commande passée avant 18h",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Stock pharmacie"
        verbose_name_plural = "Stocks pharmacies"
        unique_together = ("pharmacy", "medicine")
        ordering = ["medicine__name"]

    def __str__(self):
        return f"{self.medicine} @ {self.pharmacy} ({self.quantity})"

    @property
    def availability(self):
        if self.quantity <= 0:
            return self.Availability.OUT
        if self.quantity <= self.low_stock_threshold:
            return self.Availability.LOW
        return self.Availability.AVAILABLE

    @property
    def availability_label(self):
        return self.Availability(self.availability).label

    @property
    def display_price(self):
        return self.promotional_price or self.price

    @property
    def formatted_price(self):
        return f"{self.display_price:,}".replace(",", " ") + " FCFA"

    @property
    def delivery_promise_text(self):
        if self.delivery_promise:
            return self.delivery_promise
        if hasattr(self.pharmacy, "delivery_promise") and self.pharmacy.delivery_promise:
            return self.pharmacy.delivery_promise
        return "Livraison express disponible"

    @property
    def pharmacist_advice_text(self):
        return self.pharmacist_advice or self.medicine.pharmacist_advice


class StockMovement(models.Model):
    """Mouvement de stock type ERP (entrée / sortie / ajustement / inventaire)."""

    class MovementType(models.TextChoices):
        IN = "in", "Entrée"
        OUT = "out", "Sortie"
        ADJUST = "adjust", "Ajustement"
        INVENTORY = "inventory", "Inventaire"

    stock = models.ForeignKey(PharmacyStock, on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    quantity_delta = models.IntegerField(help_text="Variation (+/-)")
    quantity_after = models.PositiveIntegerField()
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mouvement de stock"
        verbose_name_plural = "Mouvements de stock"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.quantity_delta} — {self.stock}"


class StockInventorySession(models.Model):
    """Session d'inventaire physique (CDC §4.5)."""

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "En cours"
        COMPLETED = "completed", "Validé"
        CANCELLED = "cancelled", "Annulé"

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name="inventory_sessions")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="inventory_sessions_started",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_sessions_completed",
    )
    note = models.CharField(max_length=255, blank=True)
    lines_total = models.PositiveIntegerField(default=0)
    lines_counted = models.PositiveIntegerField(default=0)
    variance_lines = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Session d'inventaire"
        verbose_name_plural = "Sessions d'inventaire"

    def __str__(self):
        return f"Inventaire {self.pharmacy} — {self.get_status_display()}"


class StockInventoryLine(models.Model):
    session = models.ForeignKey(
        StockInventorySession,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    stock = models.ForeignKey(PharmacyStock, on_delete=models.CASCADE, related_name="inventory_lines")
    expected_quantity = models.PositiveIntegerField()
    counted_quantity = models.PositiveIntegerField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("session", "stock")
        ordering = ["stock__medicine__name"]

    @property
    def variance(self):
        if self.counted_quantity is None:
            return None
        return self.counted_quantity - self.expected_quantity

    def __str__(self):
        return f"{self.stock} — attendu {self.expected_quantity}"


class Favorite(models.Model):
    """Favori lié à une offre concrète (médicament chez une pharmacie)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites"
    )
    stock = models.ForeignKey(
        PharmacyStock, on_delete=models.CASCADE, related_name="favorited_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "stock")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} ♥ {self.stock}"

    @property
    def medicine(self):
        return self.stock.medicine


class MedicineReview(models.Model):
    """Avis client sur un médicament."""

    medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="medicine_reviews"
    )
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField()
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("medicine", "user")

    def __str__(self):
        return f"{self.medicine} — {self.rating}/5"


class MedicineQuestion(models.Model):
    """Questions / réponses sur un médicament (style fiche conseil)."""

    medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE, related_name="questions")
    question = models.CharField(max_length=300)
    answer = models.TextField()
    order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.question[:60]
