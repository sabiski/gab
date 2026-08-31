from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_order_rx_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="cancellation_reason",
            field=models.TextField(
                blank=True, help_text="Motif de refus / annulation par la pharmacie"
            ),
        ),
    ]
