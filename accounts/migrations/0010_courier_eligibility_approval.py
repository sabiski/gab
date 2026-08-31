from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_courier_vehicle_documents"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="courierprofile",
            name="eligibility_approved",
            field=models.BooleanField(
                default=False,
                help_text="Validation admin — requis pour accepter des missions",
            ),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="eligibility_approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="eligibility_approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="courier_eligibility_approvals",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
