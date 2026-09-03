from django.core.management.base import BaseCommand

from core.media_storage import ingest_disk_media_to_db, restore_all_media_to_disk


class Command(BaseCommand):
    help = "Synchronise les fichiers média entre MySQL et le disque du conteneur."

    def handle(self, *args, **options):
        restored = restore_all_media_to_disk()
        ingested = ingest_disk_media_to_db()
        self.stdout.write(
            self.style.SUCCESS(
                f"Médias : {restored} restauré(s) sur disque, {ingested} copié(s) vers MySQL."
            )
        )
