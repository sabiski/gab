import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0003_favorite_per_stock"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="medicine",
            name="composition",
            field=models.TextField(blank=True, help_text="Composition / ingrédients"),
        ),
        migrations.AddField(
            model_name="medicine",
            name="pharmacist_advice",
            field=models.TextField(blank=True, help_text="Avis / conseil du pharmacien"),
        ),
        migrations.AddField(
            model_name="medicine",
            name="presentation",
            field=models.TextField(blank=True, help_text="Présentation détaillée du produit"),
        ),
        migrations.AddField(
            model_name="medicine",
            name="recommended_by",
            field=models.CharField(
                blank=True, help_text="Ex. Dermatologues, pédiatres…", max_length=150
            ),
        ),
        migrations.AddField(
            model_name="medicine",
            name="usage_advice",
            field=models.TextField(blank=True, help_text="Conseils d'utilisation"),
        ),
        migrations.AddField(
            model_name="pharmacystock",
            name="pharmacist_advice",
            field=models.TextField(
                blank=True, help_text="Conseil du pharmacien pour cette offre"
            ),
        ),
        migrations.CreateModel(
            name="MedicineQuestion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("question", models.CharField(max_length=300)),
                ("answer", models.TextField()),
                ("order", models.PositiveIntegerField(default=0)),
                ("is_published", models.BooleanField(default=True)),
                (
                    "medicine",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="questions",
                        to="catalog.medicine",
                    ),
                ),
            ],
            options={
                "ordering": ["order", "id"],
            },
        ),
        migrations.CreateModel(
            name="MedicineReview",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("rating", models.PositiveSmallIntegerField(default=5)),
                ("comment", models.TextField()),
                ("is_published", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "medicine",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reviews",
                        to="catalog.medicine",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="medicine_reviews",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "unique_together": {("medicine", "user")},
            },
        ),
    ]
