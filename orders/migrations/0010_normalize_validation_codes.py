from django.db import migrations


def pad_validation_codes(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    for order in Order.objects.exclude(validation_code="").iterator():
        raw = (order.validation_code or "").strip()
        if raw.isdigit() and len(raw) < 6:
            order.validation_code = raw.zfill(6)
            order.save(update_fields=["validation_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0009_pharmacy_handoff_traceability"),
    ]

    operations = [
        migrations.RunPython(pad_validation_codes, migrations.RunPython.noop),
    ]
