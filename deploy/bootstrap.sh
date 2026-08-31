#!/usr/bin/env bash
# Installation initiale sur VPS Ubuntu/Debian (KVM2)
# Usage (en root ou sudo) :
#   curl -sSL .../bootstrap.sh | bash
#   ou : sudo bash deploy/bootstrap.sh
set -euo pipefail

APP_USER="gabpharma"
APP_DIR="/var/www/gabpharma"
REPO_URL="${GABPHARMA_REPO_URL:-https://github.com/sabiski/gabpharma.git}"
DOMAIN="${GABPHARMA_DOMAIN:-gabpharma.ga}"
DB_NAME="${GABPHARMA_DB_NAME:-gabpharma}"
DB_USER="${GABPHARMA_DB_USER:-gabpharma}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Lancez ce script avec sudo." >&2
  exit 1
fi

echo "==> Paquets système"
apt-get update
apt-get install -y \
  python3 python3-venv python3-dev \
  build-essential libpq-dev \
  nginx postgresql postgresql-contrib \
  git curl certbot python3-certbot-nginx \
  ufw

echo "==> Utilisateur applicatif"
if ! id "$APP_USER" &>/dev/null; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$APP_DIR" /var/www/certbot "$APP_DIR/logs"
chown -R "$APP_USER:www-data" "$APP_DIR"

echo "==> PostgreSQL"
DB_PASS="${GABPHARMA_DB_PASSWORD:-$(openssl rand -base64 24)}"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 \
  || sudo -u postgres createuser --createdb "$DB_USER"
sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
  || sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"

echo "==> Code source"
if [[ ! -d "$APP_DIR/.git" ]]; then
  sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
else
  echo "Dépôt déjà présent dans $APP_DIR — git pull manuel si besoin."
fi

echo "==> Environnement Python"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
  SECRET=$(openssl rand -base64 48 | tr -d '/+=' | head -c 50)
  cat >"$APP_DIR/.env" <<EOF
DJANGO_SETTINGS_MODULE=config.settings_prod
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=$SECRET
DJANGO_ALLOWED_HOSTS=$DOMAIN,www.$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN,https://www.$DOMAIN
SITE_URL=https://$DOMAIN

DB_ENGINE=django.db.backends.postgresql
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_HOST=127.0.0.1
DB_PORT=5432

GUNICORN_BIND=127.0.0.1:8001
DJANGO_LOG_FILE=$APP_DIR/logs/django.log
EOF
  chown "$APP_USER:www-data" "$APP_DIR/.env"
  chmod 640 "$APP_DIR/.env"
  echo "Fichier .env créé. Conservez le mot de passe DB : $DB_PASS"
fi

echo "==> Django (migrate + static)"
sudo -u "$APP_USER" bash -lc "
  cd $APP_DIR
  source .venv/bin/activate
  export \$(grep -v '^#' .env | xargs)
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
"

echo "==> Systemd + Nginx"
cp "$APP_DIR/deploy/systemd/gabpharma.service" /etc/systemd/system/gabpharma.service
sed -i "s/gabpharma\\.ga/$DOMAIN/g" "$APP_DIR/deploy/nginx/gabpharma-http.conf"
sed -i "s/gabpharma\\.ga/$DOMAIN/g" "$APP_DIR/deploy/nginx/gabpharma.conf"
cp "$APP_DIR/deploy/nginx/gabpharma-http.conf" /etc/nginx/sites-available/gabpharma
ln -sf /etc/nginx/sites-available/gabpharma /etc/nginx/sites-enabled/gabpharma
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable gabpharma
systemctl restart gabpharma

# Nginx : version HTTP seule avant certificat (commentez le bloc HTTPS si besoin)
nginx -t && systemctl reload nginx

echo "==> Pare-feu (SSH + HTTP/HTTPS)"
ufw allow OpenSSH || true
ufw allow 'Nginx Full' || true
ufw --force enable || true

cat <<MSG

============================================================
Installation terminée.

1. Pointez le DNS de $DOMAIN vers l'IP du VPS.
2. Certificat SSL (remplace la config HTTP par HTTPS) :
     certbot --nginx -d $DOMAIN -d www.$DOMAIN
     # ou manuellement :
     # cp $APP_DIR/deploy/nginx/gabpharma.conf /etc/nginx/sites-available/gabpharma && nginx -t && systemctl reload nginx
3. Créez un super-admin :
     sudo -u $APP_USER bash -lc 'cd $APP_DIR && source .venv/bin/activate && python manage.py createsuperuser'
4. Déploiements suivants :
     sudo bash $APP_DIR/deploy/deploy.sh

Service : systemctl status gabpharma
Logs    : journalctl -u gabpharma -f
============================================================
MSG
