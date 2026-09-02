# Déploiement Gab'Pharma — VPS KVM2 (Ubuntu/Debian)

Stack : **Git + Python 3 + Gunicorn + Nginx + PostgreSQL**

> Ne jamais utiliser `python manage.py runserver` en production.

## Prérequis

- VPS Ubuntu 22.04+ ou Debian 12+
- Nom de domaine pointant vers l'IP du VPS (`gabpharma.ga`)
- Dépôt Git accessible depuis le serveur (GitHub, GitLab…)

## 1. Pousser le code sur Git

Sur votre machine locale :

```bash
git add .
git commit -m "Préparation déploiement VPS"
git push origin main
```

## 2. Installation initiale (une fois sur le VPS)

Connectez-vous en SSH au VPS, puis :

```bash
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/VOTRE_COMPTE/gabpharma.git /var/www/gabpharma
cd /var/www/gabpharma
sudo bash deploy/bootstrap.sh
```

Variables optionnelles avant `bootstrap.sh` :

```bash
export GABPHARMA_REPO_URL=https://github.com/sabiski/gabpharma.git
export GABPHARMA_DOMAIN=gabpharma.ga
export GABPHARMA_DB_PASSWORD=votre_mot_de_passe
```

## 3. Certificat HTTPS (Let's Encrypt)

Après propagation DNS :

```bash
sudo certbot --nginx -d gabpharma.ga -d www.gabpharma.ga
```

## 4. Compte administrateur

```bash
sudo -u gabpharma bash -lc 'cd /var/www/gabpharma && source .venv/bin/activate && python manage.py createsuperuser'
```

Données de démo (optionnel) :

```bash
sudo -u gabpharma bash -lc 'cd /var/www/gabpharma && source .venv/bin/activate && python manage.py seed_demo'
```

## 5. Mises à jour (après chaque `git push`)

Sur le VPS :

```bash
sudo bash /var/www/gabpharma/deploy/deploy.sh
```

## Fichiers fournis

| Fichier | Rôle |
|---------|------|
| `config/settings_prod.py` | Paramètres Django production |
| `.env.production.example` | Modèle de variables d'environnement |
| `deploy/gunicorn.conf.py` | Workers Gunicorn |
| `deploy/nginx/gabpharma.conf` | Reverse proxy + static/media |
| `deploy/systemd/gabpharma.service` | Service systemd |
| `deploy/bootstrap.sh` | Installation complète VPS |
| `deploy/deploy.sh` | Mise à jour rapide |

## Commandes utiles

```bash
sudo systemctl status gabpharma    # état du service
sudo journalctl -u gabpharma -f   # logs Gunicorn
sudo nginx -t && sudo systemctl reload nginx
tail -f /var/www/gabpharma/logs/django.log
```

## Checklist sécurité Django

- [ ] `DJANGO_DEBUG=0`
- [ ] `DJANGO_SECRET_KEY` unique et long
- [ ] `DJANGO_ALLOWED_HOSTS` = votre domaine uniquement
- [ ] PostgreSQL (pas SQLite en prod)
- [ ] HTTPS actif (certbot)
- [ ] Fichier `.env` en `chmod 640`, propriétaire `gabpharma`
- [ ] Pare-feu : ports 22, 80, 443 seulement

## Dépannage

**502 Bad Gateway** — Gunicorn ne tourne pas :

```bash
sudo systemctl restart gabpharma
sudo journalctl -u gabpharma -n 50
```

**Fichiers statiques manquants** :

```bash
sudo -u gabpharma bash -lc 'cd /var/www/gabpharma && source .venv/bin/activate && python manage.py collectstatic --noinput'
```

**Erreur CSRF au login** — vérifiez `DJANGO_CSRF_TRUSTED_ORIGINS` avec `https://votre-domaine`.

## Documentation métier

- [Mise en service d'une pharmacie partenaire](PHARMACIE_ONBOARDING.md) — rattachement titulaire, abonnement, connexion portail
