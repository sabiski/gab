"""Middlewares de sécurité : en-têtes navigateur, expiration de session, taille des envois."""
from __future__ import annotations

import time

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

# Domaines tiers réellement utilisés par les templates (CDN, polices, cartes).
_CDN = "https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net"
_FONTS_CSS = "https://fonts.googleapis.com"
_FONTS = "https://fonts.gstatic.com"
_OSM = "https://tile.openstreetmap.org https://nominatim.openstreetmap.org"

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    f"script-src 'self' 'unsafe-inline' {_CDN}",
    f"style-src 'self' 'unsafe-inline' {_FONTS_CSS} https://unpkg.com https://cdn.jsdelivr.net",
    f"font-src 'self' data: {_FONTS}",
    "img-src 'self' data: blob: https:",
    # Les service workers (caisse, PWA) re-téléchargent CDN et polices via fetch() : connect-src s'applique.
    f"connect-src 'self' {_OSM} {_CDN} {_FONTS_CSS} {_FONTS}",
    "frame-src 'self'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "media-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'self'",
])

PERMISSIONS_POLICY = (
    "camera=(self), geolocation=(self), microphone=(), payment=(), usb=(), "
    "magnetometer=(), gyroscope=(), accelerometer=(), interest-cohort=()"
)


class SecurityHeadersMiddleware:
    """CSP, Permissions-Policy et isolation de fenêtre sur toutes les réponses HTML."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.report_only = getattr(settings, "CSP_REPORT_ONLY", False)

    def __call__(self, request):
        response = self.get_response(request)
        header = "Content-Security-Policy-Report-Only" if self.report_only else "Content-Security-Policy"
        if not request.path.startswith(f"/{settings.ADMIN_URL}"):
            response.headers.setdefault(header, CONTENT_SECURITY_POLICY)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.path.startswith(("/espace/", "/auth/", "/api/")):
            # Pages privées : jamais en cache partagé (proxy, ordinateur public)
            response.headers.setdefault("Cache-Control", "no-store, private")
        return response


# Rôles dont la session expire après inactivité (réglage admin « Expiration de session »).
SENSITIVE_ROLES = {"admin", "superadmin", "support", "authority", "regional_supervisor", "partner"}
# Pharmacies : la caisse reste ouverte toute la journée au comptoir.
PHARMACY_IDLE_MINUTES = 8 * 60
SESSION_ACTIVITY_KEY = "_gp_last_activity"


class IdleSessionTimeoutMiddleware:
    """Déconnecte après inactivité : comptes sensibles (réglage admin), pharmacies (8 h)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def _limit_minutes(self, user) -> int | None:
        role = getattr(user, "role", "")
        if role in SENSITIVE_ROLES:
            from accounts.models import PlatformSettings

            try:
                return max(5, PlatformSettings.load().session_expiry_minutes or 30)
            except Exception:
                return 30
        if role == "pharmacist":
            return PHARMACY_IDLE_MINUTES
        return None

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not request.path.startswith("/api/"):
            limit = self._limit_minutes(user)
            now = int(time.time())
            last = request.session.get(SESSION_ACTIVITY_KEY)
            if limit and last and now - int(last) > limit * 60:
                logout(request)
                if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type == "application/json":
                    return HttpResponse('{"ok": false, "error": "Session expirée."}', status=401,
                                        content_type="application/json")
                return redirect(f"{reverse('login')}?next={request.path}&expire=1")
            # Écriture au plus une fois par minute pour limiter les écritures de session
            if not last or now - int(last) >= 60:
                request.session[SESSION_ACTIVITY_KEY] = now
        return self.get_response(request)


class MaxUploadSizeMiddleware:
    """Refuse les requêtes trop volumineuses avant de les lire (déni de service, disque)."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.limit = int(getattr(settings, "MAX_UPLOAD_BYTES", 15 * 1024 * 1024))

    def __call__(self, request):
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > self.limit:
            return HttpResponse(
                f"Fichier trop volumineux (maximum {self.limit // (1024 * 1024)} Mo).",
                status=413, content_type="text/plain; charset=utf-8",
            )
        return self.get_response(request)
