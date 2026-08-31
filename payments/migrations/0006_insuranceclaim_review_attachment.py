from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0005_platform_settlement_finance"),
    ]

    operations = [
        migrations.AddField(
            model_name="insuranceclaim",
            name="review_attachment",
            field=models.FileField(
                blank=True,
                help_text="Document joint par l'assureur (décision)",
                null=True,
                upload_to="insurance/claims/reviews/",
            ),
        ),
    ]
