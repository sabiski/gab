"""Désactive la 2FA plateforme (secours production / dépannage connexion)."""
from django.core.management.base import BaseCommand

from accounts.models import PlatformSettings


class Command(BaseCommand):
    help = "Active ou désactive la vérification 2FA à la connexion."

    def add_arguments(self, parser):
        parser.add_argument(
            "--off",
            action="store_true",
            help="Désactiver la 2FA (connexion directe après mot de passe).",
        )
        parser.add_argument(
            "--on",
            action="store_true",
            help="Réactiver la 2FA.",
        )

    def handle(self, *args, **options):
        settings = PlatformSettings.load()
        if options["off"]:
            settings.two_factor_required = False
            settings.save(update_fields=["two_factor_required", "updated_at"])
            self.stdout.write(self.style.SUCCESS("2FA désactivée."))
        elif options["on"]:
            settings.two_factor_required = True
            settings.save(update_fields=["two_factor_required", "updated_at"])
            self.stdout.write(self.style.SUCCESS("2FA activée."))
        else:
            state = "activée" if settings.two_factor_required else "désactivée"
            self.stdout.write(f"2FA actuellement : {state}")
            self.stdout.write("Utilisez --off ou --on pour changer.")
