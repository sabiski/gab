#!/bin/sh
set -e

echo "==> Migrations"
python manage.py migrate --noinput

echo "==> Fichiers statiques"
python manage.py collectstatic --noinput

echo "==> Démarrage Gunicorn sur ${GUNICORN_BIND:-0.0.0.0:8000}"
exec gunicorn config.wsgi:application \
    --config /app/deploy/gunicorn.conf.py \
    --bind "${GUNICORN_BIND:-0.0.0.0:8000}"
