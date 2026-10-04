"""Sert /media/ depuis le stockage durable (disque, sinon MySQL).

Les chemins d'ordonnances / factures santé sont BLOQUÉS ici :
accès uniquement via les vues authentifiées + déchiffrement.
"""
from __future__ import annotations

import mimetypes
import posixpath

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.http import require_GET

# Préfixes interdits en accès public (données de santé / factures sensibles)
BLOCKED_MEDIA_PREFIXES = (
    "prescriptions/",
    "coverage/prescriptions/",
    "coverage/invoices/",
    "support/",
)


def _uncached_404():
    resp = HttpResponse("Not found", status=404, content_type="text/plain")
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    return resp


def _is_blocked(path: str) -> bool:
    p = path.lower().lstrip("/")
    return any(p.startswith(prefix) for prefix in BLOCKED_MEDIA_PREFIXES)


@require_GET
def serve_durable_media(request, path: str):
    path = posixpath.normpath(path).lstrip("/")
    if not path or path.startswith("..") or "/../" in f"/{path}/":
        raise Http404("Chemin invalide.")
    if _is_blocked(path):
        # Pas de fuite : même 404 anonyme / connecté sans passer par la vue métier
        return _uncached_404()
    if not default_storage.exists(path):
        return _uncached_404()
    try:
        fh = default_storage.open(path, "rb")
    except FileNotFoundError:
        return _uncached_404()

    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    response = FileResponse(fh, content_type=content_type)
    response["Cache-Control"] = "public, max-age=86400"
    return response
