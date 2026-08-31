from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health_check(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health_check, name="health"),
    path("django-admin/", admin.site.urls),  # technique uniquement
    path("api/v1/", include("api.urls")),
    path("auth/", include("accounts.urls")),
    path("espace/", include("backoffice.urls")),
    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # En prod, préférer un reverse-proxy ; en secours on sert aussi /media/
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "Gab'Pharma (technique)"
admin.site.site_title = "Gab'Pharma"
admin.site.index_title = "Accès technique — préférer /espace/"
