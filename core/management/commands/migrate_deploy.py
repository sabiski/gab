"""Migrations production — répare les conflits « table already exists » (déploiements interrompus)."""
from __future__ import annotations

import re
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.utils import OperationalError


_APPLYING_RE = re.compile(r"Applying\s+(\S+)\.\.\.", re.MULTILINE)
_MAX_AUTO_FAKE = 40


class Command(BaseCommand):
    help = (
        "Applique les migrations ; si une table existe déjà (MySQL 1050), "
        "marque la migration en --fake puis continue."
    )

    def handle(self, *args, **options):
        for attempt in range(1, _MAX_AUTO_FAKE + 1):
            buffer = StringIO()
            try:
                call_command("migrate", "--noinput", stdout=buffer, stderr=buffer)
                self.stdout.write(buffer.getvalue())
                self.stdout.write(self.style.SUCCESS("Migrations terminées."))
                return
            except OperationalError as exc:
                if not self._is_table_exists_error(exc):
                    raise
                migration = self._last_applying_migration(buffer.getvalue())
                if not migration:
                    self.stderr.write(buffer.getvalue())
                    raise RuntimeError(
                        "Table déjà existante mais migration introuvable dans la sortie."
                    ) from exc
                self.stdout.write(
                    self.style.WARNING(
                        f"[{attempt}] Table existante → fake {migration}"
                    )
                )
                app_label, migration_name = migration.split(".", 1)
                call_command(
                    "migrate",
                    app_label,
                    migration_name,
                    fake=True,
                    verbosity=1,
                )

        raise RuntimeError(
            f"Trop de conflits de migration (>{_MAX_AUTO_FAKE}). "
            "Recréez la base ou corrigez django_migrations manuellement."
        )

    @staticmethod
    def _is_table_exists_error(exc: OperationalError) -> bool:
        if exc.args and exc.args[0] == 1050:
            return True
        return "already exists" in str(exc).lower()

    @staticmethod
    def _last_applying_migration(output: str) -> str | None:
        matches = _APPLYING_RE.findall(output)
        return matches[-1] if matches else None
