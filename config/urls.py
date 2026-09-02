import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import FileResponse, JsonResponse
from django.urls import include, path, re_path
from django.views.static import serve as serve_media
from pathlib import Path


def health_check(_request):
    return JsonResponse({"status": "ok"})


def favicon(_request):
  icon = Path(settings.BASE_DIR) / "static" / "icons" / "favicon.svg"
  return FileResponse(icon.open("rb"), content_type="image/svg+xml")


urlpatterns = [
    path("favicon.ico", favicon, name="favicon"),
    path("health/", health_check, name="health"),
    path("django-admin/", admin.site.urls),  # technique uniquement
    path("api/v1/", include("api.urls")),
    path("auth/", include("accounts.urls")),
    path("espace/", include("backoffice.urls")),
    path("", include("core.urls")),
]

# Fichiers uploadés (logos, ordonnances, avatars…)
# static() ne crée aucune route si DEBUG=False — serve explicite pour Dokploy/Gunicorn.
_serve_media = settings.DEBUG or os.environ.get("DJANGO_SERVE_MEDIA", "1") == "1"
if _serve_media:
    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            serve_media,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
elif settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "Gab'Pharma (technique)"
admin.site.site_title = "Gab'Pharma"
admin.site.index_title = "Accès technique — préférer /espace/"

handler404 = "core.error_views.page_not_found"
handler500 = "core.error_views.server_error"
