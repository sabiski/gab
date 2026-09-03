"""Stockage média qui survit aux redéploiements Dokploy.

Le disque du conteneur est éphémère : un avatar visible tout de suite disparaît
après un restart (le navigateur le cache 5–15 min, puis 404).
La base MySQL Hostinger, elle, est persistante — on y copie chaque upload.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage

logger = logging.getLogger("gabpharma.media")

# Au-delà, on ne met pas le blob en MySQL (max_allowed_packet).
_MAX_DB_BYTES = 8 * 1024 * 1024


class DurableMediaStorage(FileSystemStorage):
    """Écrit sur disque (lecture rapide) + copie en base (survie au redéploiement)."""

    def _save(self, name, content):
        raw = content.read()
        if hasattr(content, "seek"):
            try:
                content.seek(0)
            except Exception:
                pass
        saved = super()._save(name, ContentFile(raw, name=getattr(content, "name", name)))
        self._persist_db(saved, raw)
        return saved

    def _open(self, name, mode="rb"):
        try:
            return super()._open(name, mode)
        except (FileNotFoundError, OSError):
            if self._restore_from_db(name):
                return super()._open(name, mode)
            raise

    def exists(self, name):
        if super().exists(name):
            return True
        return self._db_exists(name)

    def delete(self, name):
        super().delete(name)
        try:
            from core.models import StoredMedia

            StoredMedia.objects.filter(name=name).delete()
        except Exception:
            logger.exception("Suppression média en base échouée : %s", name)

    def _persist_db(self, name: str, raw: bytes) -> None:
        if len(raw) > _MAX_DB_BYTES:
            logger.warning(
                "Fichier %s trop volumineux (%s octets) pour MySQL — disque uniquement.",
                name,
                len(raw),
            )
            return
        try:
            from core.models import StoredMedia

            ctype = mimetypes.guess_type(name)[0] or ""
            StoredMedia.objects.update_or_create(
                name=name,
                defaults={"data": raw, "content_type": ctype, "size": len(raw)},
            )
        except Exception:
            logger.exception("Copie MySQL du média échouée : %s", name)

    def _restore_from_db(self, name: str) -> bool:
        try:
            from core.models import StoredMedia

            obj = StoredMedia.objects.filter(name=name).only("data").first()
        except Exception:
            logger.exception("Lecture média MySQL échouée : %s", name)
            return False
        if not obj:
            return False
        dest = Path(self.path(name))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(bytes(obj.data))
        logger.info("Média restauré depuis MySQL : %s", name)
        return True

    def _db_exists(self, name: str) -> bool:
        try:
            from core.models import StoredMedia

            return StoredMedia.objects.filter(name=name).exists()
        except Exception:
            return False


def restore_all_media_to_disk() -> int:
    """Réécrit sur disque tous les blobs MySQL (appelé au démarrage du conteneur)."""
    from core.models import StoredMedia

    root = Path(settings.MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    restored = 0
    for obj in StoredMedia.objects.iterator():
        dest = root / obj.name
        if dest.is_file() and dest.stat().st_size == obj.size:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(bytes(obj.data))
        restored += 1
    return restored


def ingest_disk_media_to_db() -> int:
    """Copie vers MySQL les fichiers déjà présents sur disque (volume existant)."""
    from core.models import StoredMedia

    root = Path(settings.MEDIA_ROOT)
    if not root.is_dir():
        return 0
    ingested = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if size > _MAX_DB_BYTES:
            continue
        existing = StoredMedia.objects.filter(name=rel).only("size").first()
        if existing and existing.size == size:
            continue
        DurableMediaStorage()._persist_db(rel, path.read_bytes())
        ingested += 1
    return ingested
