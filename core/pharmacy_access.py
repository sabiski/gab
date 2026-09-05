"""Accès pharmacie multi-employés et permissions ERP."""
from django.db.models import Q

from accounts.models import User
from pharmacies.models import Pharmacy, PharmacyEmployee

SESSION_PHARMACY_KEY = "active_pharmacy_id"

PERM_ORDERS = "orders"
PERM_STOCKS = "stocks"
PERM_STATS = "stats"
PERM_RX = "prescriptions"
PERM_SETTINGS = "settings"
PERM_STAFF = "staff"
PERM_PAYROLL = "payroll"

ROLE_PERMISSIONS = None  # chargé dynamiquement — voir get_role_permissions_map()


def get_role_permissions_map():
    from core.platform_access import get_pharmacy_role_permissions_map

    return get_pharmacy_role_permissions_map()


def pharmacy_ids_for_user(user):
    return list(pharmacies_for_user(user).values_list("id", flat=True))


def pharmacies_for_user(user):
    """Pharmacies accessibles : titulaire ou membre actif du personnel."""
    if not user or not user.is_authenticated:
        return Pharmacy.objects.none()
    if user.role not in {User.Role.PHARMACIST, User.Role.ADMIN, User.Role.SUPERADMIN}:
        return Pharmacy.objects.none()
    if user.role in {User.Role.ADMIN, User.Role.SUPERADMIN}:
        return Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE).order_by("name")
    return (
        Pharmacy.objects.filter(
            Q(owner=user)
            | Q(employees__user=user, employees__is_active=True)
        )
        .distinct()
        .order_by("name")
    )


def ensure_owner_membership(pharmacy):
    """Crée la fiche titulaire si la pharmacie a un owner sans fiche employé."""
    if not pharmacy or not pharmacy.owner_id:
        return None
    owner = pharmacy.owner
    emp = (
        PharmacyEmployee.objects.filter(pharmacy=pharmacy, user=owner)
        .select_related("user", "pharmacy")
        .first()
    )
    if emp:
        updates = []
        if emp.job_role != PharmacyEmployee.JobRole.OWNER:
            emp.job_role = PharmacyEmployee.JobRole.OWNER
            updates.append("job_role")
        if not emp.is_active:
            emp.is_active = True
            updates.append("is_active")
        if updates:
            updates.append("updated_at")
            emp.save(update_fields=updates)
        return emp

    # Ne pas forcer TIT-001 : déjà pris par un autre employé → IntegrityError / 500.
    return PharmacyEmployee.objects.create(
        pharmacy=pharmacy,
        user=owner,
        employee_code=allocate_employee_code(pharmacy, "TIT-001"),
        job_role=PharmacyEmployee.JobRole.OWNER,
        job_title="Titulaire",
        contract_type=PharmacyEmployee.ContractType.CDI,
        is_active=True,
    )


def pharmacy_for_user(user, request=None):
    """Pharmacie active (session ou première accessible)."""
    qs = pharmacies_for_user(user)
    if not qs.exists():
        return None
    if request is not None:
        raw = request.session.get(SESSION_PHARMACY_KEY)
        if raw:
            try:
                ph = qs.filter(pk=int(raw)).first()
                if ph:
                    ensure_owner_membership(ph)
                    return ph
            except (TypeError, ValueError):
                pass
    ph = qs.first()
    if ph:
        ensure_owner_membership(ph)
    return ph


def set_active_pharmacy(request, pharmacy):
    if pharmacy:
        request.session[SESSION_PHARMACY_KEY] = pharmacy.pk


def employee_for(user, pharmacy):
    if not user or not pharmacy:
        return None
    if pharmacy.owner_id == user.pk:
        return ensure_owner_membership(pharmacy)
    return (
        PharmacyEmployee.objects.filter(
            pharmacy=pharmacy, user=user, is_active=True
        )
        .select_related("user", "pharmacy")
        .first()
    )


def has_pharmacy_permission(user, pharmacy, permission):
    if not user or not pharmacy:
        return False
    if user.role in {User.Role.ADMIN, User.Role.SUPERADMIN}:
        return True
    emp = employee_for(user, pharmacy)
    if not emp or not emp.is_active:
        return False
    return permission in get_role_permissions_map().get(emp.job_role, set())


def pharmacy_default_route(user, pharmacy):
    """Première section autorisée pour redirection (ex. caissier → commandes)."""
    flags = pharmacy_permission_flags(user, pharmacy)
    order = (
        PERM_ORDERS,
        PERM_STOCKS,
        PERM_STATS,
        PERM_SETTINGS,
        PERM_STAFF,
        PERM_PAYROLL,
        PERM_RX,
    )
    for perm in order:
        if flags.get(perm):
            if perm == PERM_ORDERS:
                return "bo_pharmacy_orders"
            if perm == PERM_STOCKS:
                return "bo_pharmacy_stocks"
            if perm == PERM_STATS:
                return "bo_pharmacy_stats"
            if perm == PERM_SETTINGS:
                return "bo_pharmacy_settings"
            if perm == PERM_STAFF:
                return "bo_pharmacy_personnel"
    return "bo_pharmacy_dashboard"


def can_manage_staff(user, pharmacy):
    return has_pharmacy_permission(user, pharmacy, PERM_STAFF)


def pharmacy_permission_flags(user, pharmacy):
    """Drapeaux de menu pour le portail pharmacie."""
    return {
        PERM_ORDERS: has_pharmacy_permission(user, pharmacy, PERM_ORDERS),
        PERM_STOCKS: has_pharmacy_permission(user, pharmacy, PERM_STOCKS),
        PERM_STATS: has_pharmacy_permission(user, pharmacy, PERM_STATS),
        PERM_RX: has_pharmacy_permission(user, pharmacy, PERM_RX),
        PERM_SETTINGS: has_pharmacy_permission(user, pharmacy, PERM_SETTINGS),
        PERM_STAFF: has_pharmacy_permission(user, pharmacy, PERM_STAFF),
        PERM_PAYROLL: has_pharmacy_permission(user, pharmacy, PERM_PAYROLL),
    }


def pharmacy_permissions_matrix():
    """Matrice lecture seule rôles métier × permissions ERP (portail pharmacie)."""
    from core.platform_access import (
        PHARMACY_JOB_LABELS,
        PHARMACY_PERM_LABELS,
        PHARMACY_ROLE_DESCRIPTIONS,
        get_pharmacy_role_permissions_map,
    )

    perm_map = get_pharmacy_role_permissions_map()
    rows = []
    for job in PharmacyEmployee.JobRole.values:
        rows.append(
            {
                "role": job,
                "label": PHARMACY_JOB_LABELS.get(job, job),
                "description": PHARMACY_ROLE_DESCRIPTIONS.get(job, ""),
                "perms_active": perm_map.get(job, set()),
            }
        )
    return rows, PHARMACY_PERM_LABELS


def team_effective_permissions(employees):
    """Permissions effectives par employé selon son rôle métier."""
    from core.platform_access import PHARMACY_PERM_LABELS

    perm_map = get_role_permissions_map()
    rows = []
    perm_by_id = {}
    for emp in employees:
        perms = sorted(perm_map.get(emp.job_role, set()))
        labels = [PHARMACY_PERM_LABELS[p] for p in perms if p in PHARMACY_PERM_LABELS]
        rows.append({"employee": emp, "permission_labels": labels})
        perm_by_id[emp.id] = labels
    return rows, perm_by_id


def allocate_employee_code(pharmacy, preferred=""):
    """Attribue un matricule unique pour l'officine (évite les doublons)."""
    preferred = (preferred or "").strip()
    if preferred and not PharmacyEmployee.objects.filter(
        pharmacy=pharmacy, employee_code=preferred
    ).exists():
        return preferred

    prefix = pharmacy.code.replace("-", "")[:6] if pharmacy.code else "EMP"
    max_num = 0
    for code in PharmacyEmployee.objects.filter(pharmacy=pharmacy).values_list(
        "employee_code", flat=True
    ):
        suffix = code.rsplit("-", 1)[-1]
        if suffix.isdigit():
            max_num = max(max_num, int(suffix))

    num = max_num + 1
    while True:
        candidate = f"{prefix}-{num:03d}"
        if not PharmacyEmployee.objects.filter(
            pharmacy=pharmacy, employee_code=candidate
        ).exists():
            return candidate
        num += 1


def next_employee_code(pharmacy):
    """Alias rétrocompatibilité."""
    return allocate_employee_code(pharmacy)
