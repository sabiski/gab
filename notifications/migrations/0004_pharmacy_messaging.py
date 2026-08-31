# Generated manually

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pharmacies", "0003_pharmacy_delivery_promise"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("notifications", "0003_emergency_alert"),
    ]

    operations = [
        migrations.CreateModel(
            name="PharmacyConversation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pharmacy_conversations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "pharmacy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conversations",
                        to="pharmacies.pharmacy",
                    ),
                ),
            ],
            options={
                "verbose_name": "Conversation pharmacie",
                "verbose_name_plural": "Conversations pharmacie",
                "ordering": ["-updated_at"],
                "unique_together": {("client", "pharmacy")},
            },
        ),
        migrations.CreateModel(
            name="PharmacyMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField()),
                ("is_read", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="notifications.pharmacyconversation",
                    ),
                ),
                (
                    "sender",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pharmacy_messages_sent",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Message pharmacie",
                "verbose_name_plural": "Messages pharmacie",
                "ordering": ["created_at"],
            },
        ),
    ]
