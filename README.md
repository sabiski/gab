# Gab'Pharma

Plateforme nationale du médicament (Gabon) — **Django + Tailwind CSS + PWA**, avec API REST `/api/v1/` prête pour Flutter (v2.0).

## Stack

| Couche | Techno |
|--------|--------|
| Backend | Django 5 + Django REST Framework + JWT |
| Front web / PWA | Templates Django + Tailwind CSS (CDN) + Service Worker |
| BDD (dev) | SQLite — PostgreSQL recommandé en prod |
| Mobile v2 | Flutter consommera `/api/v1/` |

## Couleurs (logo)

- Primary (vert) `#228545` / `#015533`
- Secondary (lime) `#8DC63F`
- Texte `#14291C` / muted `#5B6B63`
- Fond `#F5F8F6`
- Typo : **Plus Jakarta Sans**

## Démarrage rapide

```bash
cd gabpharma
source .venv/Scripts/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8080
```

Ouvrir : http://127.0.0.1:8080/

## Comptes de démo (back-office `/espace/`)

| Profil | Identifiant | Mot de passe | URL |
|--------|-------------|--------------|-----|
| Super Admin | `admin` | `admin123` | `/espace/admin/` |
| Patient | `patient` | `patient123` | `/espace/client/` |
| Pharmacien | `pharmacien` | `pharma123` | `/espace/pharmacie/` |
| Livreur | `livreur` | `livreur123` | `/espace/livreur/` |
| Autorité | `autorite` | `autorite123` | `/espace/autorite/` |
| Support | `support` | `support123` | `/espace/support/` |

Connexion : `/auth/connexion/` — Django Admin technique : `/django-admin/`

## API Flutter (v1 réservée)

Authentification JWT :

```http
POST /api/v1/auth/login/
POST /api/v1/auth/register/
POST /api/v1/auth/refresh/
GET  /api/v1/users/me/
```

Ressources principales :

```http
GET  /api/v1/pharmacies/
GET  /api/v1/pharmacies/nearby/
GET  /api/v1/pharmacies/on_duty/
GET  /api/v1/medicines/search/?q=Doliprane
GET  /api/v1/medicines/availability/?q=Doliprane
GET  /api/v1/categories/
POST /api/v1/orders/
PATCH /api/v1/orders/{id}/status/
POST /api/v1/payments/charge/
POST /api/v1/deliveries/{id}/assign/
POST /api/v1/deliveries/{id}/validate/
GET  /api/v1/notifications/
GET  /api/v1/favorites/
```

Règle CDC v1.1 : la **disponibilité** n’est exposée qu’après une recherche explicite (`/medicines/availability/` ou `?q=` côté web).

## Apps Django

- `accounts` — utilisateurs multi-rôles
- `pharmacies` — officines, avis, documents
- `catalog` — médicaments, catégories, stocks
- `orders` — commandes, ordonnances
- `deliveries` — livraisons, incidents
- `payments` — paiements, abonnements, assurances
- `notifications` — notifications + audit
- `core` — UI patient PWA
- `api` — endpoints REST v1

## PWA

- Manifest : `/manifest.webmanifest`
- Service worker : `/static/js/sw.js`
- Navigation bas (mobile) fidèle aux maquettes

## Prochaines étapes suggérées

1. Portail Pharmacie + Admin métier (tableaux de bord)
2. Panier / paiement Moov & Airtel (stubs déjà en place)
3. Espace Livreur
4. App Flutter branchée sur la même API
5. PostgreSQL + PostGIS pour distances réelles
