"""Audit de sécurité de l'installation (à lancer après chaque déploiement)."""
from django.conf import settings
from django.core.management.base import BaseCommand

from accounts.models import PlatformSettings, User
from accounts.security import DEMO_PASSWORDS, purge_old_attempts

# Comptes créés par seed_demo (README) : à neutraliser en production.
DEMO_USERNAMES = {"admin", "patient", "pharmacien", "livreur", "autorite", "support", "cnamgs"}
STAFF_ROLES = {
    User.Role.ADMIN, User.Role.SUPERADMIN, User.Role.SUPPORT, User.Role.AUTHORITY,
    User.Role.REGIONAL_SUPERVISOR, User.Role.PARTNER, User.Role.PHARMACIST,
}


class Command(BaseCommand):
    help = "Vérifie la configuration de sécurité et les comptes à mot de passe de démonstration."

    def add_arguments(self, parser):
        parser.add_argument("--disable-demo", action="store_true",
                            help="Désactive les comptes utilisant un mot de passe de démonstration.")
        parser.add_argument("--purge-only", action="store_true",
                            help="Supprime seulement l'historique de connexions de plus de 90 jours.")

    def handle(self, *args, **opts):
        if opts["purge_only"]:
            self.stdout.write(f"{purge_old_attempts()} tentative(s) de connexion anciennes supprimée(s).")
            return

        problems, warnings = [], []
        if settings.DEBUG:
            problems.append("DEBUG est activé (DJANGO_DEBUG=0 en production).")
        if settings.SECRET_KEY.startswith("django-insecure") or len(settings.SECRET_KEY) < 40:
            problems.append("DJANGO_SECRET_KEY absente, par défaut ou trop courte.")
        if "*" in settings.ALLOWED_HOSTS:
            problems.append("DJANGO_ALLOWED_HOSTS='*' : risque d'empoisonnement des liens de réinitialisation.")
        if not settings.DEBUG and not getattr(settings, "SESSION_COOKIE_SECURE", False):
            problems.append("SESSION_COOKIE_SECURE désactivé.")
        if any("trycloudflare" in o or "ngrok" in o for o in settings.CSRF_TRUSTED_ORIGINS) and not settings.DEBUG:
            problems.append("Des domaines de tunnel publics sont dans CSRF_TRUSTED_ORIGINS.")
        if not getattr(settings, "FIELD_ENCRYPTION_KEY", ""):
            warnings.append("FIELD_ENCRYPTION_KEY vide : les ordonnances sont chiffrées avec une clé dérivée "
                            "de SECRET_KEY (changer SECRET_KEY les rendrait illisibles).")
        if settings.ADMIN_URL == "django-admin/":
            warnings.append("L'admin technique est à l'URL par défaut /django-admin/ (définir DJANGO_ADMIN_URL).")
        ps = PlatformSettings.load()
        if not ps.two_factor_required:
            warnings.append("La double authentification (2FA) est désactivée dans les paramètres plateforme.")
        if ps.session_expiry_minutes > 60:
            warnings.append(f"Expiration de session longue pour les comptes sensibles ({ps.session_expiry_minutes} min).")

        superusers = User.objects.filter(is_superuser=True, is_active=True).count()
        admins = User.objects.filter(role__in=[User.Role.ADMIN, User.Role.SUPERADMIN], is_active=True).count()
        if superusers > 3:
            warnings.append(f"{superusers} super-utilisateurs Django actifs : limitez au strict nécessaire.")

        # Comptes à mot de passe de démonstration (vérification limitée aux comptes sensibles et de démo)
        weak = []
        candidates = User.objects.filter(is_active=True).filter(
            role__in=STAFF_ROLES
        ) | User.objects.filter(is_active=True, username__in=DEMO_USERNAMES)
        for user in candidates.distinct():
            if any(user.check_password(p) for p in DEMO_PASSWORDS):
                weak.append(user)
        if weak:
            problems.append(
                "Comptes avec mot de passe de démonstration : " + ", ".join(u.username for u in weak)
            )
            if opts["disable_demo"]:
                for user in weak:
                    user.set_unusable_password()
                    user.is_active = False
                    user.status = User.Status.INACTIVE
                    user.save(update_fields=["password", "is_active", "status"])
                self.stdout.write(self.style.WARNING(f"{len(weak)} compte(s) désactivé(s)."))

        self.stdout.write(f"Administrateurs actifs : {admins} · super-utilisateurs : {superusers}")
        for p in problems:
            self.stdout.write(self.style.ERROR(f"[CRITIQUE] {p}"))
        for w in warnings:
            self.stdout.write(self.style.WARNING(f"[ATTENTION] {w}"))
        if not problems and not warnings:
            self.stdout.write(self.style.SUCCESS("Aucun problème détecté."))
        elif not problems:
            self.stdout.write(self.style.SUCCESS("Aucun problème critique."))
