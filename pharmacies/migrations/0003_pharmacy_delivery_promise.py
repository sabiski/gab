from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pharmacies", "0002_document_expires_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="pharmacy",
            name="delivery_promise",
            field=models.CharField(
                blank=True,
                help_text="Texte livraison affiché sur les fiches produit",
                max_length=255,
            ),
        ),
    ]
