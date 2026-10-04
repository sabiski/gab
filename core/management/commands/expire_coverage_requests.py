"""Expire automatiquement les prises en charge PC non utilisées."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.coverage_requests import expire_stale_coverage_requests


class Command(BaseCommand):
    help = "Passe en Expirée les demandes PC acceptées dont le délai est dépassé."

    def handle(self, *args, **options):
        n = expire_stale_coverage_requests()
        self.stdout.write(
            self.style.SUCCESS(
                f"{n} dossier(s) PC passé(s) en Expirée — {timezone.now().isoformat()}"
            )
        )
