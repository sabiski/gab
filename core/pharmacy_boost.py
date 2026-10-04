"""Publicités boostées pharmacie — forfaits, popup, priorité catalogue."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from core.models import PharmacyBoostAd, PharmacyBoostPurchase
from pharmacies.models import Pharmacy

BOOST_PLANS = {
    PharmacyBoostPurchase.Plan.BOOST_7: {
        "label": "Boost 7 jours",
        "amount": 15_000,
        "days": 7,
        "tagline": "Visibilité courte — promo flash",
    },
    PharmacyBoostPurchase.Plan.BOOST_15: {
        "label": "Boost 15 jours",
        "amount": 28_000,
        "days": 15,
        "tagline": "Campagne demi-mois",
    },
    PharmacyBoostPurchase.Plan.BOOST_30: {
        "label": "Boost 30 jours",
        "amount": 45_000,
        "days": 30,
        "tagline": "Priorité catalogue + popup header",
    },
}


def expire_purchases(pharmacy=None):
    now = timezone.now()
    qs = PharmacyBoostPurchase.objects.filter(
        status=PharmacyBoostPurchase.Status.ACTIVE,
        ends_at__lt=now,
    )
    if pharmacy:
        qs = qs.filter(pharmacy=pharmacy)
    qs.update(status=PharmacyBoostPurchase.Status.EXPIRED)


def active_boost_purchase(pharmacy):
    expire_purchases(pharmacy)
    if not pharmacy:
        return None
    now = timezone.now()
    return (
        PharmacyBoostPurchase.objects.filter(
            pharmacy=pharmacy,
            status=PharmacyBoostPurchase.Status.ACTIVE,
            starts_at__lte=now,
            ends_at__gte=now,
        )
        .order_by("-ends_at")
        .first()
    )


def pharmacy_can_boost(pharmacy) -> bool:
    """Boost autorisé si forfait pub actif OU abonnement plateforme Pro/Entreprise."""
    if not pharmacy:
        return False
    if active_boost_purchase(pharmacy):
        return True
    plan = getattr(pharmacy, "subscription_plan", None)
    return plan in {
        Pharmacy.SubscriptionPlan.PROFESSIONAL,
        Pharmacy.SubscriptionPlan.ENTERPRISE,
    }


def purchase_boost(pharmacy, plan_code: str, *, reference: str = "") -> PharmacyBoostPurchase:
    meta = BOOST_PLANS.get(plan_code)
    if not meta:
        raise ValueError("Forfait boost invalide.")
    expire_purchases(pharmacy)
    now = timezone.now()
    return PharmacyBoostPurchase.objects.create(
        pharmacy=pharmacy,
        plan=plan_code,
        amount=meta["amount"],
        status=PharmacyBoostPurchase.Status.ACTIVE,
        reference=reference or f"BST-{pharmacy.pk}-{int(now.timestamp())}",
        starts_at=now,
        ends_at=now + timedelta(days=meta["days"]),
    )


def active_popup_ads(limit: int = 3):
    """Pubs popup actives pour le header (toutes pharmacies)."""
    expire_purchases()
    now = timezone.now()
    qs = (
        PharmacyBoostAd.objects.filter(
            status=PharmacyBoostAd.Status.ACTIVE,
            show_popup=True,
        )
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        .select_related("pharmacy", "purchase")
        .order_by("-priority", "-created_at")
    )
    ads = []
    for ad in qs[:20]:
        if ad.is_currently_active:
            ads.append(ad)
        if len(ads) >= limit:
            break
    return ads


def boosted_pharmacy_ids() -> set[int]:
    expire_purchases()
    now = timezone.now()
    ids = set(
        PharmacyBoostAd.objects.filter(
            status=PharmacyBoostAd.Status.ACTIVE,
            boost_catalog=True,
        )
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        .values_list("pharmacy_id", flat=True)
    )
    # Aussi les pharmacies avec forfait boost actif même sans annonce (priorité légère)
    live = PharmacyBoostPurchase.objects.filter(
        status=PharmacyBoostPurchase.Status.ACTIVE,
        starts_at__lte=now,
        ends_at__gte=now,
    ).values_list("pharmacy_id", flat=True)
    ids.update(live)
    return ids


def annotate_catalog_boost(queryset):
    """Place les stocks des pharmacies boostées en tête."""
    ids = list(boosted_pharmacy_ids())
    if not ids:
        return queryset, False
    return queryset.annotate(
        _boost_rank=Case(
            When(pharmacy_id__in=ids, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ), True


def apply_boost_ordering(queryset, *extra_order):
    qs, has_boost = annotate_catalog_boost(queryset)
    if has_boost:
        return qs.order_by("_boost_rank", *extra_order)
    if extra_order:
        return qs.order_by(*extra_order)
    return qs


def record_impression(ad: PharmacyBoostAd):
    from django.db.models import F

    PharmacyBoostAd.objects.filter(pk=ad.pk).update(impressions=F("impressions") + 1)


def record_click(ad: PharmacyBoostAd):
    from django.db.models import F

    PharmacyBoostAd.objects.filter(pk=ad.pk).update(clicks=F("clicks") + 1)


def approve_boost_ad(ad: PharmacyBoostAd, *, moderator=None, notes: str = "") -> PharmacyBoostAd:
    ad.status = PharmacyBoostAd.Status.ACTIVE
    ad.moderation_notes = (notes or "").strip()
    ad.moderated_at = timezone.now()
    ad.moderated_by = moderator
    ad.save(update_fields=["status", "moderation_notes", "moderated_at", "moderated_by", "updated_at"])
    return ad


def reject_boost_ad(ad: PharmacyBoostAd, *, moderator=None, notes: str = "") -> PharmacyBoostAd:
    ad.status = PharmacyBoostAd.Status.REJECTED
    ad.moderation_notes = (notes or "").strip() or "Refusée par la modération."
    ad.moderated_at = timezone.now()
    ad.moderated_by = moderator
    ad.save(update_fields=["status", "moderation_notes", "moderated_at", "moderated_by", "updated_at"])
    return ad
