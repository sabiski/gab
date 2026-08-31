from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_medicine_enrichment_reviews"),
    ]

    operations = [
        migrations.AddField(
            model_name="medicine",
            name="contenance",
            field=models.CharField(
                blank=True,
                help_text="Contenance / format (ex. 300 ml, 8 comprimés)",
                max_length=80,
            ),
        ),
        migrations.AddField(
            model_name="medicine",
            name="tagline",
            field=models.CharField(
                blank=True,
                help_text="Accroche courte (ex. Apaise et ré-équilibre — peaux sensibles)",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="pharmacystock",
            name="delivery_promise",
            field=models.CharField(
                blank=True,
                help_text="Ex. Livré demain pour toute commande passée avant 18h",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="pharmacystock",
            name="pharmacist_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="pharmacystock",
            name="pharmacist_rpps",
            field=models.CharField(
                blank=True, help_text="N° RPPS / ordre", max_length=30
            ),
        ),
        migrations.AddField(
            model_name="pharmacystock",
            name="pharmacist_title",
            field=models.CharField(
                blank=True, default="Docteur en pharmacie", max_length=120
            ),
        ),
        migrations.AlterField(
            model_name="medicine",
            name="form",
            field=models.CharField(
                choices=[
                    ("tablet", "Comprimé"),
                    ("capsule", "Gélule"),
                    ("syrup", "Sirop"),
                    ("injection", "Injectable"),
                    ("cream", "Crème"),
                    ("spray", "Spray"),
                    ("gel", "Gel"),
                    ("drops", "Gouttes"),
                    ("sachet", "Sachet"),
                    ("other", "Autre"),
                ],
                default="tablet",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="medicine",
            name="dosage",
            field=models.CharField(
                blank=True,
                help_text="Dosage médicament (ex. 500 mg)",
                max_length=100,
            ),
        ),
    ]
