#!/bin/sh
set -e

echo "==> Migrations (auto-réparation déploiement)"
python manage.py migrate_deploy

if [ -n "${SUPERADMIN_PASSWORD:-}" ]; then
  echo "==> Super administrateur"
  python manage.py ensure_superadmin
fi

echo "==> Restauration des médias (MySQL → disque)"
python manage.py restore_media || true

echo "==> Audit de sécurité"
python manage.py security_audit || true

echo "==> Fichiers statiques"
python manage.py collectstatic --noinput

echo "==> Purge quotidienne des ordonnances (données de santé)"
(
  while true; do
    python manage.py purge_prescriptions || echo "purge_prescriptions a échoué"
    python manage.py security_audit --purge-only || true
    sleep 86400
  done
) &

echo "==> Démarrage Gunicorn sur ${GUNICORN_BIND:-0.0.0.0:8000}"
exec gunicorn config.wsgi:application \
    --config /app/deploy/gunicorn.conf.py \
    --bind "${GUNICORN_BIND:-0.0.0.0:8000}"
