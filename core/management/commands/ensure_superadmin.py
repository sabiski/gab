"""Crée ou met à jour le compte super administrateur plateforme."""
from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Crée ou met à jour le super administrateur Gab'Pharma "
        "(accès /espace/ — toutes les configurations)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", default=os.environ.get("SUPERADMIN_USERNAME", "admin"))
        parser.add_argument("--email", default=os.environ.get("SUPERADMIN_EMAIL", "admin@gabpharma.ga"))
        parser.add_argument("--password", default=os.environ.get("SUPERADMIN_PASSWORD", ""))
        parser.add_argument("--first-name", default=os.environ.get("SUPERADMIN_FIRST_NAME", "Super"))
        parser.add_argument("--last-name", default=os.environ.get("SUPERADMIN_LAST_NAME", "Admin"))
        parser.add_argument(
            "--force-password",
            action="store_true",
            help="Met à jour le mot de passe même si le compte existe déjà.",
        )

    def handle(self, *args, **options):
        username = (options["username"] or "admin").strip()
        email = (options["email"] or "").strip()
        password = options["password"] or ""
        first_name = (options["first_name"] or "Super").strip()
        last_name = (options["last_name"] or "Admin").strip()
        force_password = options["force_password"]

        if not username:
            raise CommandError("Nom d'utilisateur requis (--username ou SUPERADMIN_USERNAME).")

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email or f"{username}@gabpharma.ga",
                "first_name": first_name,
                "last_name": last_name,
                "role": User.Role.SUPERADMIN,
                "status": User.Status.ACTIVE,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
                "city": "Libreville",
            },
        )

        changed = False
        if not created:
            updates = {
                "role": User.Role.SUPERADMIN,
                "status": User.Status.ACTIVE,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            }
            if email:
                updates["email"] = email
            if first_name:
                updates["first_name"] = first_name
            if last_name:
                updates["last_name"] = last_name
            for field, value in updates.items():
                if getattr(user, field) != value:
                    setattr(user, field, value)
                    changed = True

        if password:
            user.set_password(password)
            changed = True
        elif created:
            raise CommandError(
                "Mot de passe requis pour créer le superadmin "
                "(--password ou variable SUPERADMIN_PASSWORD)."
            )
        elif force_password and not password:
            raise CommandError("--force-password nécessite aussi un mot de passe.")

        if created or changed:
            user.save()

        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Superadmin créé : {username} — connexion sur /auth/connexion/ "
                    f"puis /espace/"
                )
            )
        elif changed:
            self.stdout.write(
                self.style.SUCCESS(f"Superadmin mis à jour : {username}")
            )
        else:
            self.stdout.write(f"Superadmin déjà à jour : {username}")

        self.stdout.write(
            "Rôle : Super Administrateur (tous les modules back-office + paramètres plateforme)."
        )
