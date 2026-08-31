# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pharmacies", "0003_pharmacy_delivery_promise"),
        ("notifications", "0004_pharmacy_messaging"),
    ]

    operations = [
        migrations.AddField(
            model_name="emergencyalert",
            name="assigned_pharmacy",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="emergency_alerts",
                to="pharmacies.pharmacy",
            ),
        ),
        migrations.AddField(
            model_name="emergencyalert",
            name="distance_km",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
        ),
        migrations.AddField(
            model_name="pharmacyconversation",
            name="emergency_alert",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="conversations",
                to="notifications.emergencyalert",
            ),
        ),
    ]
