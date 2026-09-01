"""Teste la configuration SMTP (admin / déploiement)."""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from core.email_utils import deliver_email, smtp_configured


class Command(BaseCommand):
    help = "Envoie un e-mail de test et affiche le diagnostic SMTP (logs techniques)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            default=settings.EMAIL_HOST_USER,
            help="Destinataire (défaut : EMAIL_HOST_USER)",
        )

    def handle(self, *args, **options):
        recipient = (options["to"] or "").strip()
        self.stdout.write(f"EMAIL_HOST      = {settings.EMAIL_HOST}")
        self.stdout.write(f"EMAIL_PORT      = {settings.EMAIL_PORT}")
        self.stdout.write(f"EMAIL_USE_TLS   = {settings.EMAIL_USE_TLS}")
        self.stdout.write(f"EMAIL_USE_SSL   = {getattr(settings, 'EMAIL_USE_SSL', False)}")
        self.stdout.write(f"EMAIL_HOST_USER = {settings.EMAIL_HOST_USER}")
        self.stdout.write(f"DEFAULT_FROM    = {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"EMAIL_BACKEND   = {settings.EMAIL_BACKEND}")
        self.stdout.write(f"SMTP configuré  = {smtp_configured()}")

        if not recipient:
            self.stderr.write(self.style.ERROR("Aucun destinataire (--to ou EMAIL_HOST_USER)."))
            return

        result = deliver_email(
            subject="Gab'Pharma — test SMTP",
            body="Si vous recevez ce message, la configuration SMTP fonctionne.",
            recipient_list=[recipient],
            fail_silently=False,
        )
        if result.ok:
            self.stdout.write(self.style.SUCCESS(f"E-mail de test envoyé à {recipient} (mode {result.mode})."))
        else:
            self.stderr.write(self.style.ERROR(f"Échec technique : {result.error}"))
