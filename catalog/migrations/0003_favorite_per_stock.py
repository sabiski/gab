import django.db.models.deletion
from django.db import migrations, models


def forwards_favorite_to_stock(apps, schema_editor):
    Favorite = apps.get_model("catalog", "Favorite")
    PharmacyStock = apps.get_model("catalog", "PharmacyStock")
    for fav in Favorite.objects.all():
        stock = (
            PharmacyStock.objects.filter(medicine_id=fav.medicine_id)
            .order_by("id")
            .first()
        )
        if stock is None:
            fav.delete()
            continue
        fav.stock_id = stock.id
        fav.save(update_fields=["stock_id"])


def backwards_favorite_to_medicine(apps, schema_editor):
    Favorite = apps.get_model("catalog", "Favorite")
    for fav in Favorite.objects.all():
        if fav.stock_id:
            fav.medicine_id = fav.stock.medicine_id
            fav.save(update_fields=["medicine_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_medicine_pharmacy_stock_movement"),
    ]

    operations = [
        migrations.AddField(
            model_name="favorite",
            name="stock",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="favorited_by",
                to="catalog.pharmacystock",
            ),
        ),
        migrations.RunPython(forwards_favorite_to_stock, backwards_favorite_to_medicine),
        migrations.AlterUniqueTogether(
            name="favorite",
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name="favorite",
            name="medicine",
        ),
        migrations.AlterField(
            model_name="favorite",
            name="stock",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="favorited_by",
                to="catalog.pharmacystock",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="favorite",
            unique_together={("user", "stock")},
        ),
    ]
