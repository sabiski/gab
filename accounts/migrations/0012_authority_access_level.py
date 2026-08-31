from django.db import migrations, models


def assign_authority_codes(apps, schema_editor):
    AuthorityProfile = apps.get_model("accounts", "AuthorityProfile")
    for idx, profile in enumerate(AuthorityProfile.objects.order_by("id"), start=1):
        if not profile.authority_code:
            profile.authority_code = f"AUTH-{idx:04d}"
            profile.save(update_fields=["authority_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_platform_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="authorityprofile",
            name="access_level",
            field=models.CharField(
                choices=[
                    ("national_admin", "Administrateur National"),
                    ("regional_admin", "Administrateur Régional"),
                    ("national_reader", "Lecteur National"),
                    ("regional_reader", "Lecteur Régional"),
                ],
                default="national_admin",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="authorityprofile",
            name="authority_code",
            field=models.CharField(blank=True, max_length=16, null=True, unique=True),
        ),
        migrations.RunPython(assign_authority_codes, migrations.RunPython.noop),
    ]
