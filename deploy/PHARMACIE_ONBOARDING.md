# Gab'Pharma — Mise en service d'une pharmacie partenaire

Ce guide s'adresse aux administrateurs Gab'Pharma qui onboardent une nouvelle officine sur la plateforme.

## Principe

| Élément | Rôle |
|--------|------|
| **Fiche pharmacie** | Officine (nom, adresse, stocks, commandes) |
| **Compte titulaire** | Utilisateur rôle **Pharmacien** qui se connecte au portail |
| **Abonnement plateforme** | Forfait Essentiel / Professionnel / Entreprise (facturation Gab'Pharma) |

La fiche pharmacie **sans titulaire rattaché** n'est pas accessible depuis un compte pharmacien.

## Étapes (ordre recommandé)

### 1. Créer le compte titulaire

**Admin → Utilisateurs → Ajouter**

- Rôle : **Pharmacien**
- E-mail obligatoire (mot de passe envoyé par e-mail)
- Optionnel : champ « Pharmacie titulaire » pour lier directement l'officine

### 2. Créer ou modifier la fiche officine

**Admin → Pharmacies → Ajouter** (ou **Modifier**)

- Renseigner nom, adresse, téléphone, logo
- **Titulaire** : sélectionner le compte pharmacien créé à l'étape 1
- **Statut** : `Active` pour publication sur le site public

### 3. Activer l'abonnement plateforme

**Admin → Abonnements plateforme → onglet Pharmacies**

- Choisir la pharmacie
- Sélectionner le forfait (Essentiel, Professionnel, Entreprise)
- Cocher « Facturation annuelle » si applicable
- Cliquer **Activer**

Le titulaire reçoit une notification dans son espace (`/espace/pharmacie/abonnement/`).

### 4. Connexion côté pharmacie

URL de connexion : **`https://gabpharma.online/auth/connexion/`**

- Identifiant : e-mail **ou** nom d'utilisateur
- Mot de passe : reçu par e-mail à la création du compte
- Code 2FA si activé sur la plateforme

Après connexion → redirection vers **`/espace/pharmacie/`** (tableau de bord).

## Vérifications rapides

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Portail vide / « Aucune pharmacie associée » | Pas de titulaire | Admin → Pharmacies → Modifier → Titulaire |
| Pharmacie invisible sur le site | Statut ≠ Active | Passer le statut à **Active** |
| Erreur 500 à l'activation abonnement | Bug notification (corrigé) | Redéployer la dernière version |
| CSRF à la connexion | Session expirée / cache | Recharger la page, HTTPS uniquement |

## Personnel supplémentaire

Le titulaire peut gérer son équipe depuis **Espace pharmacie → Personnel** (caissier, préparateur, pharmacien adjoint…).

Alternative admin : créer un compte **Pharmacien** et l'ajouter comme employé depuis le portail titulaire.

## URLs utiles

| Page | Chemin |
|------|--------|
| Connexion | `/auth/connexion/` |
| Tableau de bord pharmacie | `/espace/pharmacie/` |
| Abonnement (vue pharmacie) | `/espace/pharmacie/abonnement/` |
| Admin pharmacies | `/espace/admin/pharmacies/` |
| Admin abonnements | `/espace/admin/abonnements/` |
