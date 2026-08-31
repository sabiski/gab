#!/usr/bin/env bash
# Mise à jour après git push — à lancer sur le VPS
# Usage : sudo bash /var/www/gabpharma/deploy/deploy.sh
set -euo pipefail

APP_USER="gabpharma"
APP_DIR="/var/www/gabpharma"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Lancez ce script avec sudo." >&2
  exit 1
fi

echo "==> Git pull"
sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only

echo "==> Dépendances Python"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Migrations + fichiers statiques"
sudo -u "$APP_USER" bash -lc "
  cd $APP_DIR
  set -a
  source .env
  set +a
  .venv/bin/python manage.py migrate --noinput
  .venv/bin/python manage.py collectstatic --noinput
"

echo "==> Redémarrage Gunicorn"
systemctl restart gabpharma
systemctl status gabpharma --no-pager

echo "Déploiement terminé."
