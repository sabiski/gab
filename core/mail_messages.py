"""Messages d'échec e-mail affichés à l'utilisateur (jamais de détail technique en prod)."""
from __future__ import annotations

from django.conf import settings

# Connexion / code 2FA
MAIL_FAILURE_LOGIN = (
    "Nous n'avons pas pu finaliser cette étape. "
    "Veuillez réessayer dans quelques instants."
)

# Admin / envoi d'identifiants
MAIL_FAILURE_ADMIN = (
    "Le compte a été enregistré, mais la notification n'a pas pu être envoyée. "
    "Contactez le support si besoin."
)

# Générique
MAIL_FAILURE_GENERIC = (
    "Une erreur est survenue. Veuillez réessayer ou contacter le support."
)


def mail_error_for_user(technical_error: str = "", *, context: str = "generic") -> str:
    """
    Message neutre pour l'interface — le détail technique reste dans les logs serveur.
    En DEBUG, le message technique peut être affiché pour le développement.
    """
    if settings.DEBUG and technical_error:
        return technical_error

    if context == "login":
        return MAIL_FAILURE_LOGIN
    if context == "admin":
        return MAIL_FAILURE_ADMIN
    return MAIL_FAILURE_GENERIC
