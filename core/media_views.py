"""Sert /media/ depuis le stockage durable (disque, sinon MySQL)."""
from __future__ import annotations

import mimetypes
import posixpath

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.http import require_GET


def _uncached_404():
    resp = HttpResponse("Not found", status=404, content_type="text/plain")
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@require_GET
def serve_durable_media(request, path: str):
    path = posixpath.normpath(path).lstrip("/")
    if not path or path.startswith("..") or "/../" in f"/{path}/":
        raise Http404("Chemin invalide.")
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
