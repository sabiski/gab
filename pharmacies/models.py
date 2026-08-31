from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Pharmacy(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PENDING = "pending", "En attente"
        SUSPENDED = "suspended", "Suspendue"
        REJECTED = "rejected", "Rejetée"

    class SubscriptionPlan(models.TextChoices):
        ESSENTIAL = "essential", "Essentiel"
        PROFESSIONAL = "professional", "Professionnel"
        ENTERPRISE = "enterprise", "Entreprise"
        NONE = "none", "Aucun"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_pharmacies",
    )
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100, default="Libreville")
    district = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, default="Estuaire")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    logo = models.ImageField(upload_to="pharmacies/logos/", blank=True, null=True)
    logo_color = models.CharField(max_length=20, default="green")  # green | blue | purple
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    is_24h = models.BooleanField(default=False)
    is_on_duty = models.BooleanField(default=False, verbose_name="Pharmacie de garde")
    opening_hours = models.CharField(max_length=100, blank=True, default="08:00 – 20:00")
    delivery_promise = models.CharField(
        max_length=255,
        blank=True,
        help_text="Texte livraison affiché sur les fiches produit",
    )
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    review_count = models.PositiveIntegerField(default=0)
    compliance_score = models.PositiveIntegerField(default=100)
    subscription_plan = models.CharField(
        max_length=20, choices=SubscriptionPlan.choices, default=SubscriptionPlan.NONE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pharmacie"
        verbose_name_plural = "Pharmacies"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def status_label(self):
        if self.is_24h:
            return "Ouvert • 24h/24"
        if self.is_on_duty:
            return "De garde"
        return f"Ouvert • {self.opening_hours}" if self.status == self.Status.ACTIVE else "Fermé"


class PharmacyDocument(models.Model):
    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="pharmacies/docs/")
    expires_at = models.DateField(
        null=True,
        blank=True,
        help_text="Date d'expiration du document (agrément, RCCM…). Alerte J-30.",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} — {self.pharmacy}"

    @property
    def is_expiring_soon(self):
        if not self.expires_at:
            return False
        today = timezone.localdate()
        return today <= self.expires_at <= today + timedelta(days=30)

    @property
    def is_expired(self):
        if not self.expires_at:
            return False
        return self.expires_at < timezone.localdate()


class PharmacyReview(models.Model):
    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("pharmacy", "user")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.pharmacy} — {self.rating}/5"


class PharmacyEmployee(models.Model):
    """Membre du personnel d'une officine (ERP RH)."""

    class JobRole(models.TextChoices):
        OWNER = "owner", "Titulaire / Gérant"
        MANAGER = "manager", "Adjoint / Gestionnaire"
        PHARMACIST = "pharmacist", "Pharmacien"
        PREPARER = "preparer", "Préparateur"
        CASHIER = "cashier", "Caissier"
        INTERN = "intern", "Stagiaire"

    class ContractType(models.TextChoices):
        CDI = "cdi", "CDI"
        CDD = "cdd", "CDD"
        STAGE = "stage", "Stage"
        INTERIM = "interim", "Intérim"

    pharmacy = models.ForeignKey(
        Pharmacy, on_delete=models.CASCADE, related_name="employees"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pharmacy_employments",
    )
    employee_code = models.CharField(max_length=20, help_text="Matricule interne")
    job_role = models.CharField(
        max_length=20, choices=JobRole.choices, default=JobRole.PHARMACIST
    )
    job_title = models.CharField(max_length=120, blank=True)
    contract_type = models.CharField(
        max_length=20, choices=ContractType.choices, default=ContractType.CDI
    )
    hired_at = models.DateField(default=timezone.localdate)
    ended_at = models.DateField(null=True, blank=True)
    base_salary = models.PositiveIntegerField(
        default=0, help_text="Salaire mensuel de base (FCFA)"
    )
    national_id = models.CharField(max_length=40, blank=True, verbose_name="N° CNI")
    professional_license = models.CharField(
        max_length=40, blank=True, verbose_name="N° ordre / RPPS"
    )
    emergency_contact = models.CharField(max_length=120, blank=True)
    emergency_phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Employé pharmacie"
        verbose_name_plural = "Employés pharmacie"
        ordering = ["pharmacy__name", "user__last_name", "user__first_name"]
        unique_together = ("pharmacy", "user")
        constraints = [
            models.UniqueConstraint(
                fields=["pharmacy", "employee_code"],
                name="uniq_pharmacy_employee_code",
            )
        ]

    def __str__(self):
        return f"{self.user} — {self.pharmacy.name} ({self.get_job_role_display()})"

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.username


class EmployeeShift(models.Model):
    """Créneau de planning."""

    employee = models.ForeignKey(
        PharmacyEmployee, on_delete=models.CASCADE, related_name="shifts"
    )
    shift_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shifts_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["shift_date", "start_time"]
        unique_together = ("employee", "shift_date", "start_time")

    def __str__(self):
        return f"{self.employee} — {self.shift_date}"

    @property
    def duration_hours(self):
        from datetime import datetime, timedelta

        start = datetime.combine(self.shift_date, self.start_time)
        end = datetime.combine(self.shift_date, self.end_time)
        if end <= start:
            end += timedelta(days=1)
        mins = (end - start).total_seconds() / 60 - self.break_minutes
        return round(max(mins, 0) / 60, 1)


class EmployeeAbsence(models.Model):
    """Congé, absence ou indisponibilité."""

    class AbsenceType(models.TextChoices):
        LEAVE = "leave", "Congé payé"
        SICK = "sick", "Maladie"
        TRAINING = "training", "Formation"
        UNPAID = "unpaid", "Sans solde"
        OTHER = "other", "Autre"

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        APPROVED = "approved", "Approuvée"
        REJECTED = "rejected", "Refusée"

    employee = models.ForeignKey(
        PharmacyEmployee, on_delete=models.CASCADE, related_name="absences"
    )
    absence_type = models.CharField(
        max_length=20, choices=AbsenceType.choices, default=AbsenceType.LEAVE
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="absences_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.employee} — {self.start_date}"

    @property
    def days_count(self):
        return (self.end_date - self.start_date).days + 1


class EmployeePayslip(models.Model):
    """Bulletin de paie mensuel."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        VALIDATED = "validated", "Validé"
        PAID = "paid", "Payé"

    employee = models.ForeignKey(
        PharmacyEmployee, on_delete=models.CASCADE, related_name="payslips"
    )
    period_year = models.PositiveSmallIntegerField()
    period_month = models.PositiveSmallIntegerField()
    base_salary = models.PositiveIntegerField(default=0)
    bonus = models.PositiveIntegerField(default=0)
    deductions = models.PositiveIntegerField(default=0)
    hours_worked = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    net_salary = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    notes = models.CharField(max_length=255, blank=True)
    paid_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_year", "-period_month"]
        unique_together = ("employee", "period_year", "period_month")

    def __str__(self):
        return f"{self.employee} — {self.period_month:02d}/{self.period_year}"

    def recalculate_net(self):
        self.net_salary = max(
            0, int(self.base_salary) + int(self.bonus) - int(self.deductions)
        )

    def save(self, *args, **kwargs):
        self.recalculate_net()
        super().save(*args, **kwargs)
