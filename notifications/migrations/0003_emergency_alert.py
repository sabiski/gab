# Generated manually

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("notifications", "0002_cdc_loyalty_support_insurance"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmergencyAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(blank=True, max_length=30, unique=True)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("medical", "Problème médical"),
                            ("accident", "Accident"),
                            ("child", "Enfant malade"),
                            ("other", "Autre urgence"),
                        ],
                        default="medical",
                        max_length=20,
                    ),
                ),
                ("latitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("longitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("address", models.TextField(blank=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "En attente"),
                            ("in_progress", "Prise en charge"),
                            ("resolved", "Résolue"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="emergency_alerts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Alerte urgence",
                "verbose_name_plural": "Alertes urgence",
                "ordering": ["-created_at"],
            },
        ),
    ]
