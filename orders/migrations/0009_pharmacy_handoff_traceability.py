from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orders", "0008_orderevaluation"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="pharmacy_handoff_code",
            field=models.CharField(
                blank=True,
                help_text="Code pharmacie — remise du colis au livreur (traçabilité)",
                max_length=6,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="pharmacy_handoff_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="pharmacy_handoff_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pharmacy_handoffs_validated",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
