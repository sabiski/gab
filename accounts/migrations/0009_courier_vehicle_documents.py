from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_accessconfiguration_portal_role_modules"),
    ]

    operations = [
        migrations.AddField(
            model_name="courierprofile",
            name="vehicle_label",
            field=models.CharField(
                blank=True, help_text="Marque et modèle affichés", max_length=120
            ),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="vehicle_year",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="vehicle_fuel",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="vehicle_cc",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="vehicle_color",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="vehicle_chassis",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="insurance_document",
            field=models.FileField(blank=True, null=True, upload_to="couriers/docs/"),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="registration_document",
            field=models.FileField(blank=True, null=True, upload_to="couriers/docs/"),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="technical_control_document",
            field=models.FileField(blank=True, null=True, upload_to="couriers/docs/"),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="training_document",
            field=models.FileField(blank=True, null=True, upload_to="couriers/docs/"),
        ),
        migrations.AddField(
            model_name="courierprofile",
            name="professional_card_document",
            field=models.FileField(blank=True, null=True, upload_to="couriers/docs/"),
        ),
    ]
