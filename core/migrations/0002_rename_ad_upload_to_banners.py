# Generated manually — rename ads/ → banners/ (évite les bloqueurs de pubs)
import shutil
from pathlib import Path

from django.conf import settings
from django.db import migrations, models


def forwards_move_banners(apps, schema_editor):
    Advertisement = apps.get_model("core", "Advertisement")
    media_root = Path(settings.MEDIA_ROOT)
    src_dir = media_root / "ads"
    dst_dir = media_root / "banners"
    dst_dir.mkdir(parents=True, exist_ok=True)

    for ad in Advertisement.objects.all():
        name = getattr(ad, "image", None)
        if not name:
            continue
        name = str(name)
        filename = Path(name).name
        old_path = media_root / name
        # aussi chercher dans ads/ si le chemin DB est juste le nom
        candidates = [old_path, src_dir / filename, media_root / "ads" / filename]
        new_rel = f"banners/{filename}"
        new_path = media_root / new_rel
        copied = False
        for cand in candidates:
            if cand.exists() and cand.is_file():
                if not new_path.exists():
                    shutil.copy2(cand, new_path)
                copied = True
                break
        if copied or new_path.exists():
            ad.image = new_rel
            ad.save(update_fields=["image"])


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_site_cms"),
    ]

    operations = [
        migrations.AlterField(
            model_name="advertisement",
            name="image",
            field=models.ImageField(upload_to="banners/"),
        ),
        migrations.RunPython(forwards_move_banners, backwards_noop),
    ]
