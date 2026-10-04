"""Chiffrement au repos des fichiers sensibles (ordonnances PC)."""
from __future__ import annotations

import base64
import hashlib
import logging
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

MAGIC = b"GPENC1:"


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "Le paquet 'cryptography' est requis pour chiffrer les ordonnances."
        ) from exc

    raw = (
        getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    ).strip() or settings.SECRET_KEY
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_bytes(data: bytes) -> bytes:
    if not data:
        return data
    if data.startswith(MAGIC):
        return data
    token = _fernet().encrypt(data)
    return MAGIC + token


def decrypt_bytes(data: bytes) -> bytes:
    if not data:
        return data
    if not data.startswith(MAGIC):
        return data  # fichier legacy non chiffré
    token = data[len(MAGIC) :]
    return _fernet().decrypt(token)


def encrypt_uploaded_file(uploaded, *, original_name: str | None = None) -> ContentFile:
    """Lit un UploadedFile et renvoie un ContentFile chiffré avec nom randomisé."""
    import uuid

    name = original_name or getattr(uploaded, "name", "ordonnance.bin") or "ordonnance.bin"
    raw = uploaded.read()
    if hasattr(uploaded, "seek"):
        try:
            uploaded.seek(0)
        except Exception:
            pass
    encrypted = encrypt_bytes(raw)
    # Extension d'origine pour le Content-Type à la lecture ; contenu = binaire chiffré
    ext = ""
    if "." in name:
        ext = "." + name.rsplit(".", 1)[-1].lower()[:8]
        if ext not in {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            ext = ".bin"
    out = ContentFile(encrypted)
    out.name = f"rx_{uuid.uuid4().hex}{ext}.enc"
    return out


def open_decrypted_filefield(file_field) -> BytesIO:
    """Ouvre un FileField et déchiffre si nécessaire."""
    if not file_field:
        raise FileNotFoundError("Fichier manquant")
    with file_field.open("rb") as fh:
        raw = fh.read()
    try:
        plain = decrypt_bytes(raw)
    except Exception:
        logger.exception("Échec déchiffrement fichier %s", file_field.name)
        raise
    buf = BytesIO(plain)
    buf.seek(0)
    return buf
