from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_authority_access_level"),
    ]

    operations = [
        migrations.AddField(
            model_name="partnerprofile",
            name="acronym",
            field=models.CharField(blank=True, help_text="Sigle (ex. CNAMGS)", max_length=32),
        ),
        migrations.AddField(
            model_name="partnerprofile",
            name="country",
            field=models.CharField(default="Gabon", max_length=80),
        ),
        migrations.AddField(
            model_name="partnerprofile",
            name="headquarters_address",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="partnerprofile",
            name="registration_number",
            field=models.CharField(blank=True, help_text="RCCM", max_length=64),
        ),
        migrations.AddField(
            model_name="partnerprofile",
            name="rep_job_title",
            field=models.CharField(blank=True, help_text="Fonction du responsable", max_length=150),
        ),
        migrations.AddField(
            model_name="partnerprofile",
            name="tax_id",
            field=models.CharField(blank=True, help_text="NIF", max_length=64),
        ),
        migrations.AddField(
            model_name="partnerprofile",
            name="validated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="partnerprofile",
            name="validated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="validated_partners",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
