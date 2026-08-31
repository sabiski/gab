"""Matrice rôles & permissions plateforme (CDC §4.2 / p.27)."""
from __future__ import annotations

from accounts.models import User
from pharmacies.models import PharmacyEmployee

# Modules du back-office admin (clés sidebar)
MOD_DASHBOARD = "dashboard"
MOD_USERS = "users"
MOD_PHARMACIES = "pharmacies"
MOD_MEDICINES = "medicines"
MOD_ORDERS = "orders"
MOD_COURIERS = "couriers"
MOD_PAYMENTS = "payments"
MOD_INSURANCE = "insurance"
MOD_SUBSCRIPTIONS = "subscriptions"
MOD_INCIDENTS = "incidents"
MOD_NOTIFICATIONS = "notifications"
MOD_TICKETS = "tickets"
MOD_AUDIT = "audit"
MOD_HERO = "hero"
MOD_ADS = "ads"
MOD_TIPS = "tips"
MOD_ACCESS_CONFIG = "access_config"
MOD_PLATFORM_SETTINGS = "platform_settings"

ADMIN_MODULE_LABELS = {
    MOD_DASHBOARD: "Tableau de bord",
    MOD_USERS: "Utilisateurs",
    MOD_PHARMACIES: "Pharmacies",
    MOD_MEDICINES: "Médicaments",
    MOD_ORDERS: "Commandes",
    MOD_COURIERS: "Livreurs",
    MOD_PAYMENTS: "Paiements",
    MOD_INSURANCE: "Assurances",
    MOD_SUBSCRIPTIONS: "Abonnements",
    MOD_INCIDENTS: "Incidents / Support",
    MOD_NOTIFICATIONS: "Notifications",
    MOD_TICKETS: "Réclamations",
    MOD_AUDIT: "Journal d'audit",
    MOD_HERO: "Hero accueil",
    MOD_ADS: "Publicités",
    MOD_TIPS: "Conseils site",
    MOD_ACCESS_CONFIG: "Rôles & permissions",
    MOD_PLATFORM_SETTINGS: "Paramètres plateforme",
}

ALL_ADMIN_MODULES = tuple(ADMIN_MODULE_LABELS.keys())

DEFAULT_ADMIN_MODULES = {
    User.Role.SUPERADMIN: ALL_ADMIN_MODULES,
    User.Role.ADMIN: tuple(
        m for m in ALL_ADMIN_MODULES if m not in {MOD_AUDIT, MOD_PLATFORM_SETTINGS}
    ),
    User.Role.SUPPORT: (MOD_INCIDENTS, MOD_TICKETS),
}

CDC_ROLE_PROFILES = [
    {
        "role": User.Role.SUPERADMIN,
        "label": "Super Administrateur",
        "scope": "Accès complet à tous les modules et paramètres système.",
        "restrictions": "Aucune — traçabilité renforcée sur ses actions.",
        "badge": "critical",
    },
    {
        "role": User.Role.ADMIN,
        "label": "Administrateur",
        "scope": "Gestion des utilisateurs, pharmacies, contenus, hors paramètres système critiques.",
        "restrictions": "Pas d'accès à la configuration système bas niveau ni au journal d'audit.",
        "badge": "admin",
    },
    {
        "role": User.Role.REGIONAL_SUPERVISOR,
        "label": "Superviseur régional",
        "scope": "Vue et actions limitées à sa région de rattachement.",
        "restrictions": "Pas de vision nationale consolidée.",
        "badge": "regional",
    },
    {
        "role": User.Role.PHARMACIST,
        "label": "Gestionnaire pharmacie / Pharmacien",
        "scope": "Stocks, commandes, statistiques de sa pharmacie ; le pharmacien valide en plus les ordonnances.",
        "restrictions": "Pas d'accès aux données d'autres pharmacies.",
        "badge": "pharmacy",
    },
    {
        "role": User.Role.COURIER,
        "label": "Livreur",
        "scope": "Missions, revenus et profil ; rattaché à une pharmacie à la fois.",
        "restrictions": "Aucun accès aux données commerciales des pharmacies.",
        "badge": "courier",
    },
    {
        "role": User.Role.CLIENT,
        "label": "Client / Patient",
        "scope": "Recherche, commande, suivi, profil personnel.",
        "restrictions": "Aucun accès aux données d'autres clients.",
        "badge": "client",
    },
    {
        "role": User.Role.PARTNER,
        "label": "Institution partenaire",
        "scope": "Lecture des données contractuelles (assureur, laboratoire, grossiste) : remboursements, stats agrégées.",
        "restrictions": "Pas de données nominatives hors périmètre contractuel.",
        "badge": "partner",
    },
    {
        "role": User.Role.AUTHORITY,
        "label": "Autorité sanitaire",
        "scope": "Indicateurs nationaux, conformité, statistiques agrégées.",
        "restrictions": "Pas d'accès aux données financières internes des pharmacies.",
        "badge": "authority",
    },
    {
        "role": User.Role.SUPPORT,
        "label": "Support",
        "scope": "Messagerie et réclamations, incidents livraison.",
        "restrictions": "Pas d'accès aux paramètres système ni aux données financières.",
        "badge": "support",
    },
]

PHARMACY_PERM_LABELS = {
    "orders": "Commandes",
    "stocks": "Stocks",
    "stats": "Statistiques",
    "prescriptions": "Validation ordonnances",
    "settings": "Paramètres officine",
    "staff": "Personnel & RH",
    "payroll": "Paie",
}

DEFAULT_PHARMACY_ROLE_PERMISSIONS = {
    PharmacyEmployee.JobRole.OWNER: [
        "orders",
        "stocks",
        "stats",
        "prescriptions",
        "settings",
        "staff",
        "payroll",
    ],
    PharmacyEmployee.JobRole.MANAGER: [
        "orders",
        "stocks",
        "stats",
        "settings",
        "staff",
        "payroll",
    ],
    PharmacyEmployee.JobRole.PHARMACIST: [
        "orders",
        "stocks",
        "stats",
        "prescriptions",
    ],
    PharmacyEmployee.JobRole.PREPARER: ["orders", "stocks"],
    PharmacyEmployee.JobRole.CASHIER: ["orders"],
    PharmacyEmployee.JobRole.INTERN: ["orders"],
}

PHARMACY_JOB_LABELS = dict(PharmacyEmployee.JobRole.choices)

PHARMACY_ROLE_DESCRIPTIONS = {
    PharmacyEmployee.JobRole.OWNER: (
        "Titulaire / gérant — accès complet à l'officine, paramètres et gestion du personnel."
    ),
    PharmacyEmployee.JobRole.MANAGER: (
        "Gestionnaire — commandes, stocks, statistiques, paramètres, RH et paie."
    ),
    PharmacyEmployee.JobRole.PHARMACIST: (
        "Pharmacien — commandes, stocks, statistiques et validation des ordonnances."
    ),
    PharmacyEmployee.JobRole.PREPARER: (
        "Préparateur — préparation des commandes et gestion des stocks."
    ),
    PharmacyEmployee.JobRole.CASHIER: (
        "Caissier — prise et suivi des commandes clients."
    ),
    PharmacyEmployee.JobRole.INTERN: (
        "Stagiaire — consultation et commandes avec droits limités."
    ),
}


def _config():
    from accounts.models import AccessConfiguration

    return AccessConfiguration.load()


def get_platform_role_modules(role: str) -> tuple[str, ...]:
    """Modules back-office admin autorisés pour un rôle plateforme."""
    cfg = _config()
    defaults = DEFAULT_ADMIN_MODULES.get(role, ())
    stored = (cfg.platform_role_modules or {}).get(role)
    if stored is None:
        return defaults
    merged = list(stored)
    for module in defaults:
        if module not in merged:
            merged.append(module)
    return tuple(merged)


def set_platform_role_modules(role: str, modules: list[str]) -> None:
    cfg = _config()
    data = dict(cfg.platform_role_modules or {})
    data[role] = modules
    cfg.platform_role_modules = data
    cfg.save(update_fields=["platform_role_modules", "updated_at"])


def get_pharmacy_role_permissions_map() -> dict[str, set[str]]:
    """Permissions ERP par rôle métier pharmacie (titulaire, pharmacien, etc.)."""
    cfg = _config()
    stored = cfg.pharmacy_role_permissions or {}
    result = {}
    for job_role, default in DEFAULT_PHARMACY_ROLE_PERMISSIONS.items():
        key = job_role if isinstance(job_role, str) else job_role
        if key in stored:
            result[key] = set(stored[key])
        else:
            result[key] = set(default)
    for key, perms in stored.items():
        if key not in result:
            result[key] = set(perms)
    return result


def pharmacy_role_permissions_set(job_role: str) -> set[str]:
    return get_pharmacy_role_permissions_map().get(job_role, set())


def save_pharmacy_role_permissions(mapping: dict[str, list[str]]) -> None:
    cfg = _config()
    cfg.pharmacy_role_permissions = mapping
    cfg.save(update_fields=["pharmacy_role_permissions", "updated_at"])


def admin_module_flags(user) -> dict[str, bool]:
    """Drapeaux d'affichage menu admin pour l'utilisateur connecté."""
    if user.role not in {User.Role.ADMIN, User.Role.SUPERADMIN}:
        return {}
    allowed = set(get_platform_role_modules(user.role))
    return {key: key in allowed for key in ALL_ADMIN_MODULES}


def can_edit_access_config(user) -> bool:
    """Au moins une section de la page configuration est modifiable."""
    return can_edit_pharmacy_permissions(user) or can_edit_platform_modules(user)


def can_edit_pharmacy_permissions(user) -> bool:
    """Matrice ERP pharmacie (titulaire, caissier, pharmacien…)."""
    return user.role in {User.Role.SUPERADMIN, User.Role.ADMIN}


def can_edit_platform_modules(user) -> bool:
    """Modules back-office admin (Super Admin, Admin, Support)."""
    return user.role == User.Role.SUPERADMIN


def can_edit_portal_permissions(user) -> bool:
    """Portails autorités, assureurs/partenaires, livreurs, etc."""
    return user.role == User.Role.SUPERADMIN


def platform_roles_for_config_ui():
    """Rôles plateforme éditables (modules admin)."""
    return [
        (User.Role.SUPERADMIN, "Super Administrateur"),
        (User.Role.ADMIN, "Administrateur"),
        (User.Role.SUPPORT, "Support"),
    ]


# ─── Portails métier (autorité, partenaire, livreur…) ─────────────────

AUTH_PORTAL_DASHBOARD = "dashboard"
AUTH_PORTAL_MAP = "map"
AUTH_PORTAL_STOCKS = "stocks"
AUTH_PORTAL_ALERTS = "alerts"
AUTH_PORTAL_TRENDS = "trends"
AUTH_PORTAL_PATIENTS = "patients"
AUTH_PORTAL_PHARMACIES = "pharmacies"
AUTH_PORTAL_DELIVERIES = "deliveries"
AUTH_PORTAL_DISPUTES = "disputes"
AUTH_PORTAL_REPORTS = "reports"
AUTH_PORTAL_CAMPAIGNS = "campaigns"
AUTH_PORTAL_NOTIFICATIONS = "notifications"
AUTH_PORTAL_SETTINGS = "settings"
AUTH_PORTAL_DECISION = "decision"
# Alias rétrocompatibilité
AUTH_PORTAL_RUPTURES = AUTH_PORTAL_STOCKS
AUTH_PORTAL_COMPLIANCE = AUTH_PORTAL_PHARMACIES

PARTNER_PORTAL_DASHBOARD = "dashboard"
PARTNER_PORTAL_CLAIMS = "claims"
PARTNER_PORTAL_MEMBERS = "members"
PARTNER_PORTAL_PHARMACIES = "pharmacies"
PARTNER_PORTAL_PAYMENTS = "payments"
PARTNER_PORTAL_CONTRACTS = "contracts"
PARTNER_PORTAL_SUBSCRIPTION = "subscription"
PARTNER_PORTAL_REPORTS = "reports"
PARTNER_PORTAL_FRAUD = "fraud"
PARTNER_PORTAL_NOTIFICATIONS = "notifications"
PARTNER_PORTAL_SETTINGS = "settings"
PARTNER_PORTAL_SUPPORT = "support"

COURIER_PORTAL_DASHBOARD = "dashboard"
COURIER_PORTAL_DELIVERIES = "deliveries"
COURIER_PORTAL_PROFILE = "profile"

SUPPORT_PORTAL_DASHBOARD = "dashboard"
SUPPORT_PORTAL_TICKETS = "tickets"
SUPPORT_PORTAL_INCIDENTS = "incidents"

REGIONAL_PORTAL_DASHBOARD = "dashboard"

PORTAL_MODULE_LABELS = {
    User.Role.AUTHORITY: {
        AUTH_PORTAL_DASHBOARD: "Tableau de bord",
        AUTH_PORTAL_MAP: "Carte sanitaire",
        AUTH_PORTAL_STOCKS: "Stocks de médicaments",
        AUTH_PORTAL_ALERTS: "Alertes sanitaires",
        AUTH_PORTAL_TRENDS: "Analyse et tendances",
        AUTH_PORTAL_PATIENTS: "Patients (données anonymisées)",
        AUTH_PORTAL_PHARMACIES: "Pharmacies & établissements",
        AUTH_PORTAL_DELIVERIES: "Livraisons",
        AUTH_PORTAL_DISPUTES: "Litiges & réclamations",
        AUTH_PORTAL_REPORTS: "Rapports & indicateurs",
        AUTH_PORTAL_CAMPAIGNS: "Campagnes de santé",
        AUTH_PORTAL_NOTIFICATIONS: "Notifications",
        AUTH_PORTAL_SETTINGS: "Paramètres",
        AUTH_PORTAL_DECISION: "Centre de décision",
    },
    User.Role.PARTNER: {
        PARTNER_PORTAL_DASHBOARD: "Tableau de bord",
        PARTNER_PORTAL_CLAIMS: "Demandes de prise en charge",
        PARTNER_PORTAL_MEMBERS: "Assurés",
        PARTNER_PORTAL_PHARMACIES: "Pharmacies partenaires",
        PARTNER_PORTAL_PAYMENTS: "Paiements et remboursements",
        PARTNER_PORTAL_CONTRACTS: "Contrats et polices",
        PARTNER_PORTAL_SUBSCRIPTION: "Abonnements",
        PARTNER_PORTAL_REPORTS: "Statistiques et rapports",
        PARTNER_PORTAL_FRAUD: "Fraudes et alertes",
        PARTNER_PORTAL_NOTIFICATIONS: "Notifications",
        PARTNER_PORTAL_SETTINGS: "Paramètres",
        PARTNER_PORTAL_SUPPORT: "Support et assistance",
    },
    User.Role.COURIER: {
        COURIER_PORTAL_DASHBOARD: "Accueil livreur",
        COURIER_PORTAL_DELIVERIES: "Livraisons",
        COURIER_PORTAL_PROFILE: "Profil livreur",
    },
    User.Role.SUPPORT: {
        SUPPORT_PORTAL_DASHBOARD: "Centre support",
        SUPPORT_PORTAL_TICKETS: "Réclamations",
        SUPPORT_PORTAL_INCIDENTS: "Incidents livraison",
    },
    User.Role.REGIONAL_SUPERVISOR: {
        REGIONAL_PORTAL_DASHBOARD: "Supervision régionale",
    },
}

DEFAULT_PORTAL_ROLE_MODULES = {
    role: tuple(
        key
        for key in labels.keys()
        if not (
            role == User.Role.AUTHORITY
            and key
            in {
                AUTH_PORTAL_DISPUTES,
                AUTH_PORTAL_NOTIFICATIONS,
                AUTH_PORTAL_SETTINGS,
            }
        )
    )
    for role, labels in PORTAL_MODULE_LABELS.items()
}


def authority_portal_permissions(user) -> dict[str, bool]:
    """Permissions menu portail Autorités — litiges superadmin ; pas de paramètres/notifications."""
    labels = PORTAL_MODULE_LABELS.get(User.Role.AUTHORITY, {})
    if user.role in {User.Role.ADMIN, User.Role.SUPERADMIN}:
        flags = {key: True for key in labels}
    else:
        flags = portal_module_flags_for_role(User.Role.AUTHORITY)
    flags[AUTH_PORTAL_DISPUTES] = user.role == User.Role.SUPERADMIN
    flags[AUTH_PORTAL_NOTIFICATIONS] = False
    flags[AUTH_PORTAL_SETTINGS] = False
    return flags


def partner_portal_permissions(user) -> dict[str, bool]:
    """Permissions menu portail compagnie d'assurance."""
    labels = PORTAL_MODULE_LABELS.get(User.Role.PARTNER, {})
    if user.role in {User.Role.ADMIN, User.Role.SUPERADMIN}:
        return {key: True for key in labels}
    return portal_module_flags_for_role(User.Role.PARTNER)

PORTAL_ROLES_FOR_CONFIG = [
    (User.Role.AUTHORITY, "Autorité sanitaire"),
    (User.Role.PARTNER, "Institution partenaire / Assurance"),
    (User.Role.COURIER, "Livreur"),
    (User.Role.SUPPORT, "Support"),
    (User.Role.REGIONAL_SUPERVISOR, "Superviseur régional"),
]


def get_portal_role_modules(role: str) -> tuple[str, ...]:
    cfg = _config()
    defaults = DEFAULT_PORTAL_ROLE_MODULES.get(role, ())
    stored = (cfg.portal_role_modules or {}).get(role)
    if stored is None:
        return defaults
    merged = list(stored)
    for module in defaults:
        if module not in merged:
            merged.append(module)
    return tuple(merged)


def set_portal_role_modules(role: str, modules: list[str]) -> None:
    cfg = _config()
    data = dict(cfg.portal_role_modules or {})
    data[role] = modules
    cfg.portal_role_modules = data
    cfg.save(update_fields=["portal_role_modules", "updated_at"])


def portal_module_flags(user) -> dict[str, bool]:
    """Drapeaux menu pour portails autorité, partenaire, livreur, support."""
    labels = PORTAL_MODULE_LABELS.get(user.role, {})
    if not labels:
        return {}
    allowed = set(get_portal_role_modules(user.role))
    return {key: key in allowed for key in labels}


def portal_module_flags_for_role(role: str) -> dict[str, bool]:
    """Drapeaux menu pour un rôle portail (ex. autorité pour un admin)."""
    labels = PORTAL_MODULE_LABELS.get(role, {})
    if not labels:
        return {}
    allowed = set(get_portal_role_modules(role))
    return {key: key in allowed for key in labels}


def portal_roles_for_config_ui():
    return PORTAL_ROLES_FOR_CONFIG
