# Generated manually for patient search plans

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("payments", "0002_cdc_loyalty_support_insurance"),
    ]

    operations = [
        migrations.CreateModel(
            name="PatientAccessPurchase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "plan",
                    models.CharField(
                        choices=[
                            ("pass_1h", "Pass 1 heure"),
                            ("standard_day", "Standard journée"),
                            ("premium", "Premium"),
                        ],
                        max_length=20,
                    ),
                ),
                ("amount", models.PositiveIntegerField()),
                (
                    "payment_method",
                    models.CharField(
                        choices=[
                            ("moov", "Moov Money"),
                            ("airtel", "Airtel Money"),
                            ("card", "Carte bancaire"),
                            ("cod", "Paiement à la livraison"),
                            ("pharmacy", "Paiement en pharmacie"),
                            ("insurance", "Assurance maladie"),
                            ("transfer", "Virement"),
                        ],
                        max_length=20,
                    ),
                ),
                ("reference", models.CharField(blank=True, max_length=100)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Actif"),
                            ("expired", "Expiré"),
                            ("cancelled", "Annulé"),
                        ],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("purchased_at", models.DateTimeField(auto_now_add=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("first_search_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access_purchases",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Forfait patient",
                "verbose_name_plural": "Forfaits patient",
                "ordering": ["-purchased_at"],
            },
        ),
    ]
