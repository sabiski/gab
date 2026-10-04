"""Supprime les ordonnances arrivées en fin de durée de conservation."""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.prescription_privacy import run_purge


class Command(BaseCommand):
    help = (
        "Purge les fichiers d'ordonnance (commandes livrées/annulées, dossier patient "
        "inutilisé, prises en charge closes). L'empreinte SHA-256 est conservée."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche ce qui serait supprimé sans rien supprimer.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        result = run_purge(dry_run=dry)
        prefix = "[simulation] " if dry else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}{result['prescriptions']} ordonnance(s) dossier, "
                f"{result['order_copies']} copie(s) commande, "
                f"{result['coverage']} ordonnance(s) PC purgée(s) — "
                f"conservation {settings.PRESCRIPTION_RETENTION_DAYS} j / "
                f"{settings.PRESCRIPTION_UNUSED_RETENTION_DAYS} j / "
                f"{settings.COVERAGE_PRESCRIPTION_RETENTION_DAYS} j — "
                f"{timezone.now().isoformat()}"
            )
        )
