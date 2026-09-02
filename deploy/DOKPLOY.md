# Déploiement Dokploy — Gab'Pharma

URL : **https://gabpharma.online/**

Un **502 Bad Gateway** signifie que Traefik (Dokploy) n'atteint pas Gunicorn dans le conteneur.

## Configuration Dokploy (Application GitHub)

### Build
| Paramètre | Valeur |
|-----------|--------|
| **Build type** | Dockerfile |
| **Dockerfile** | `Dockerfile` (racine) |
| **Port conteneur** | `8000` (pas 3000 !) |
| **Domaine → Port** | `gabpharma.online` → **8000** |
| **Health check** | `/health/` |

### Variables d'environnement (obligatoires)

```env
DJANGO_SETTINGS_MODULE=config.settings_prod
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<générez une clé longue et unique>
DJANGO_ALLOWED_HOSTS=gabpharma.online,www.gabpharma.online
DJANGO_CSRF_TRUSTED_ORIGINS=https://gabpharma.online,https://www.gabpharma.online
SITE_URL=https://gabpharma.online
GUNICORN_BIND=0.0.0.0:8000

DB_ENGINE=mysql
DB_NAME=gabbdd
DB_USER=gabbdd
DB_PASSWORD=<votre mot de passe Dokploy>
DB_HOST=gabpharmavps-sabi-p7vvry
DB_PORT=3306
```

### E-Billing (paiements Mobile Money / carte)

```env
EBILLING_CLIENT_ID=<client_id Cognito (portail LAB)>
EBILLING_CLIENT_SECRET=<client_secret>
EBILLING_ENV=lab
EBILLING_FLOW=redirect
EBILLING_USE_SIMU=0
EBILLING_EXPIRY_PERIOD=30
```

URL de callback à enregistrer chez Digitech :

`https://gabpharma.online/api/v1/payments/ebilling/callback/`

Tests LAB avec `EBILLING_USE_SIMU=1` et MSISDN `077000001` (succès) / `077010001` (échec).

> `DB_HOST` : dans Dokploy, créez une base **PostgreSQL** dans le même projet. Copiez le hostname interne (ex. `gabpharma-db-xxxxx`).

### Domaine
- Domaine : `gabpharma.online`
- HTTPS : activé (Let's Encrypt via Dokploy)

## Causes fréquentes du 502

| Cause | Solution |
|-------|----------|
| Port Dokploy ≠ 8000 | Mettre **8000** dans les paramètres de l'app |
| Gunicorn sur 127.0.0.1 | `GUNICORN_BIND=0.0.0.0:8000` |
| `DJANGO_SECRET_KEY` manquant | App crash au démarrage → voir logs |
| `DJANGO_ALLOWED_HOSTS` incorrect | Ajouter `gabpharma.online` |
| Base PostgreSQL inaccessible | Vérifier `DB_HOST` (réseau Dokploy) |
| Build sans Dockerfile | Pousser le `Dockerfile` sur GitHub puis redéployer |

## Vérifier les logs

Dans Dokploy → votre application → **Logs** / **Deployments**

Erreurs typiques :
```
ValueError: Définissez DJANGO_SECRET_KEY...
ValueError: Définissez DJANGO_ALLOWED_HOSTS...
django.db.utils.OperationalError: could not connect to server
```

## Après correction

1. Commit + push sur GitHub
2. Dokploy → **Redeploy**
3. Attendre 1–2 min (migrations + collectstatic)
4. Tester : https://gabpharma.online/health/ → `{"status": "ok"}`

## Volume persistant (médias)

Montez un volume Dokploy sur `/app/media` pour conserver les fichiers uploadés (ordonnances, logos).

## Onboarding pharmacie

Voir [PHARMACIE_ONBOARDING.md](PHARMACIE_ONBOARDING.md) — rattachement titulaire, abonnement, connexion portail.
