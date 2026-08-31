from django.core.management.base import BaseCommand

from core.loyalty import process_point_expirations


class Command(BaseCommand):
    help = "Expire les points fidélité échus et notifie les clients avant expiration (CDC §4.15)."

    def handle(self, *args, **options):
        result = process_point_expirations()
        self.stdout.write(
            self.style.SUCCESS(
                f"Fidélité : {result['expired_batches']} lot(s) expiré(s), "
                f"{result['warnings_sent']} alerte(s) envoyée(s)."
            )
        )
