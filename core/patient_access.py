"""Forfaits recherche patient — Pass 1h / Standard journée / Premium (CDC v1.1)."""
from datetime import datetime, timedelta

import secrets

from django.utils import timezone

from accounts.models import User
from payments.models import PatientAccessPurchase, Payment

PLAN_CATALOG = {
    PatientAccessPurchase.Plan.PASS_1H: {
        "label": "Pass 1 heure",
        "amount": 500,
        "tagline": "Recherche ciblée en pharmacie",
        "description": "Accès illimité aux résultats et stocks pendant 1 h — le décompte démarre à la 1re recherche.",
        "features": [
            "Disponibilité par officine",
            "Prix et stocks en temps réel",
            "Pharmacies correspondantes",
        ],
        "tier": 1,
        "activate_on_search": True,
        "card_style": "violet",
        "icon": "schedule",
    },
    PatientAccessPurchase.Plan.STANDARD_DAY: {
        "label": "Standard journée",
        "amount": 2000,
        "tagline": "Toute la journée",
        "description": "Recherches illimitées jusqu'à minuit — idéal si vous comparez plusieurs traitements.",
        "features": [
            "Recherches illimitées 24 h calendaires",
            "Stocks & prix visibles",
            "Historique des recherches du jour",
        ],
        "tier": 2,
        "activate_on_search": False,
        "card_style": "green",
        "icon": "today",
    },
    PatientAccessPurchase.Plan.PREMIUM: {
        "label": "Premium",
        "amount": 5000,
        "tagline": "Accès total + priorités",
        "description": "Recherche illimitée 30 jours, livraison offerte, bouton d'urgence et priorités.",
        "features": [
            "Tout le Standard, 30 jours",
            "Livraison à domicile & express offertes",
            "Bouton d'urgence SOS + pharmacies alertées",
            "Commandes prioritaires",
        ],
        "tier": 3,
        "activate_on_search": False,
        "card_style": "amber",
        "icon": "workspace_premium",
    },
}

STAFF_BYPASS_ROLES = {
    User.Role.ADMIN,
    User.Role.SUPERADMIN,
    User.Role.PHARMACIST,
    User.Role.COURIER,
    User.Role.AUTHORITY,
    User.Role.SUPPORT,
}


def plan_meta(plan):
    return PLAN_CATALOG.get(plan, {})


def _end_of_local_day(dt=None):
    dt = dt or timezone.localtime()
    end = datetime.combine(dt.date(), datetime.max.time())
    if timezone.is_aware(dt):
        end = timezone.make_aware(end, timezone.get_current_timezone())
    return end


def _expire_if_needed(purchase):
    now = timezone.now()
    if purchase.status != PatientAccessPurchase.Status.ACTIVE:
        return purchase
    if purchase.plan == PatientAccessPurchase.Plan.PASS_1H:
        if purchase.activated_at and purchase.expires_at and now >= purchase.expires_at:
            purchase.status = PatientAccessPurchase.Status.EXPIRED
            purchase.save(update_fields=["status"])
    elif purchase.expires_at and now >= purchase.expires_at:
        purchase.status = PatientAccessPurchase.Status.EXPIRED
        purchase.save(update_fields=["status"])
    return purchase


def get_active_purchases(user):
    if not user.is_authenticated:
        return PatientAccessPurchase.objects.none()
    qs = user.access_purchases.filter(status=PatientAccessPurchase.Status.ACTIVE)
    active = []
    for p in qs:
        p = _expire_if_needed(p)
        if p.status == PatientAccessPurchase.Status.ACTIVE:
            active.append(p)
    active.sort(key=lambda x: x.purchased_at, reverse=True)
    return active


def get_best_access(user):
    active = get_active_purchases(user)
    return active[0] if active else None


def _cancel_other_active(user):
    """Un seul forfait actif à la fois — le nouveau remplace les précédents."""
    user.access_purchases.filter(status=PatientAccessPurchase.Status.ACTIVE).update(
        status=PatientAccessPurchase.Status.CANCELLED
    )


def delivery_fee_for_user(user, delivery_mode):
    """Frais livraison : 0 pour Premium (domicile / express), sinon 1500 F."""
    from orders.models import Order

    if delivery_mode == Order.DeliveryMode.PICKUP:
        return 0
    if user_has_premium(user):
        return 0
    return 1500


def user_needs_paywall(user):
    if not user.is_authenticated:
        return True
    if user.role in STAFF_BYPASS_ROLES:
        return False
    return get_best_access(user) is None


def user_has_premium(user):
    if not user.is_authenticated:
        return False
    if user.role in STAFF_BYPASS_ROLES:
        return user.role in {User.Role.ADMIN, User.Role.SUPERADMIN}
    best = get_best_access(user)
    return best is not None and best.plan == PatientAccessPurchase.Plan.PREMIUM


def ensure_search_access(user, query=""):
    """Active le pass 1h à la 1re recherche ; retourne True si accès OK."""
    if user_needs_paywall(user):
        return False
    best = get_best_access(user)
    if not best:
        return False
    if (
        best.plan == PatientAccessPurchase.Plan.PASS_1H
        and not best.activated_at
        and query
    ):
        now = timezone.now()
        best.activated_at = now
        best.first_search_at = now
        best.expires_at = now + timedelta(hours=1)
        best.save(update_fields=["activated_at", "first_search_at", "expires_at"])
    return True


def access_status_label(user):
    best = get_best_access(user)
    if not best:
        return None
    meta = plan_meta(best.plan)
    if best.plan == PatientAccessPurchase.Plan.PASS_1H and not best.activated_at:
        return f"{meta.get('label', 'Pass')} — en attente de 1re recherche"
    if best.expires_at:
        remaining = best.expires_at - timezone.now()
        if remaining.total_seconds() <= 0:
            return None
        hours = int(remaining.total_seconds() // 3600)
        mins = int((remaining.total_seconds() % 3600) // 60)
        label = meta.get("label", "Forfait")
        if best.plan == PatientAccessPurchase.Plan.STANDARD_DAY:
            return f"{label} — jusqu'à {timezone.localtime(best.expires_at).strftime('%H:%M')}"
        if hours >= 24:
            days = hours // 24
            return f"{label} — {days} jour{'s' if days > 1 else ''} restant{'s' if days > 1 else ''}"
        if hours:
            return f"{label} — {hours} h {mins} min restantes"
        return f"{label} — {mins} min restantes"
    return meta.get("label")


def purchase_plan(user, plan, payment_method):
    if plan not in PLAN_CATALOG:
        raise ValueError("Forfait inconnu")
    if payment_method not in {
        Payment.Method.MOOV,
        Payment.Method.AIRTEL,
        Payment.Method.CARD,
    }:
        raise ValueError("Mode de paiement invalide")

    cfg = PLAN_CATALOG[plan]
    now = timezone.now()
    activated_at = None
    expires_at = None

    if plan == PatientAccessPurchase.Plan.PASS_1H:
        pass
    elif plan == PatientAccessPurchase.Plan.STANDARD_DAY:
        activated_at = now
        expires_at = _end_of_local_day(now)
    elif plan == PatientAccessPurchase.Plan.PREMIUM:
        activated_at = now
        expires_at = now + timedelta(days=30)

    ref = f"GP-FORFAIT-{secrets.token_hex(3).upper()}"
    _cancel_other_active(user)
    purchase = PatientAccessPurchase.objects.create(
        user=user,
        plan=plan,
        amount=cfg["amount"],
        payment_method=payment_method,
        reference=ref,
        status=PatientAccessPurchase.Status.ACTIVE,
        activated_at=activated_at,
        expires_at=expires_at,
    )
    return purchase


def catalog_for_template():
    items = []
    for key, cfg in PLAN_CATALOG.items():
        row = {"key": key, **cfg}
        items.append(row)
    items.sort(key=lambda x: x["tier"])
    return items
