"""
Paramètres production — VPS (Gunicorn + Nginx).

Utilisation :
  export DJANGO_SETTINGS_MODULE=config.settings_prod
  gunicorn config.wsgi:application
"""
from .settings import *  # noqa: F403

DEBUG = False

if SECRET_KEY.startswith("django-insecure"):  # noqa: F405
    raise ValueError(
        "Définissez DJANGO_SECRET_KEY dans .env avant le déploiement production."
    )

if ALLOWED_HOSTS == ["*"]:  # noqa: F405
    raise ValueError(
        "Définissez DJANGO_ALLOWED_HOSTS (ex. gabpharma.ga,www.gabpharma.ga) dans .env."
    )
