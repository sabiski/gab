"""Consentements données personnelles / santé (Loi n°025/2023 — Gabon).

Le consentement santé est distinct des CGU et doit être exprès, écrit et tracé.
"""
from __future__ import annotations

from datetime import date

from django.utils import timezone

# Version du texte présenté à l'utilisateur (à incrémenter si le libellé change)
HEALTH_CONSENT_VERSION = "2026-08-28"
TERMS_VERSION = "2026-08-28"

HEALTH_CONSENT_LABEL = (
    "J'autorise expressément Gab'Pharma à traiter mes données de santé "
    "(ordonnances, commandes de médicaments, prises en charge) pour la mise "
    "en relation avec les pharmacies, assureurs et livreurs concernés. "
    "J'ai été informé(e) des finalités et des destinataires. Ce consentement "
    "est distinct des conditions d'utilisation."
)

PARENTAL_CONSENT_LABEL = (
    "Je confirme être le titulaire de l'autorité parentale (ou son représentant) "
    "et j'autorise le traitement des données de santé de ce mineur sur Gab'Pharma."
)


def ensure_client_profile(user):
    from accounts.models import ClientProfile

    profile, _ = ClientProfile.objects.get_or_create(user=user)
    return profile


def is_minor(profile) -> bool | None:
    """True / False si date de naissance connue, None sinon."""
    dob = getattr(profile, "date_of_birth", None)
    if not dob:
        return None
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age < 18


def has_valid_health_consent(profile) -> bool:
    if not profile or not profile.health_consent:
        return False
    if profile.health_consent_version != HEALTH_CONSENT_VERSION:
        return False
    minor = is_minor(profile)
    if minor is True and not profile.parental_consent:
        return False
    return True


def record_terms_acceptance(profile, *, request=None) -> None:
    profile.terms_accepted_at = timezone.now()
    profile.terms_version = TERMS_VERSION
    profile.save(update_fields=["terms_accepted_at", "terms_version"])


def record_health_consent(profile, *, parental_name: str = "", request=None) -> None:
    now = timezone.now()
    profile.health_consent = True
    profile.health_consent_at = now
    profile.health_consent_version = HEALTH_CONSENT_VERSION
    fields = ["health_consent", "health_consent_at", "health_consent_version"]
    if is_minor(profile) is True:
        profile.parental_consent = True
        profile.parental_consent_at = now
        profile.parental_consent_name = (parental_name or "").strip()[:200]
        fields += ["parental_consent", "parental_consent_at", "parental_consent_name"]
    profile.save(update_fields=fields)


def apply_health_consent_from_post(profile, post) -> str | None:
    """Valide le POST et enregistre. Retourne un message d'erreur ou None."""
    if not post.get("health_consent"):
        return (
            "Le consentement exprès au traitement de vos données de santé est obligatoire "
            "(distinct des conditions d'utilisation)."
        )
    if is_minor(profile) is True:
        if not post.get("parental_consent"):
            return "Pour un compte mineur, l'autorisation du titulaire de l'autorité parentale est requise."
        if not (post.get("parental_consent_name") or "").strip():
            return "Indiquez le nom du titulaire de l'autorité parentale."
    record_health_consent(
        profile,
        parental_name=post.get("parental_consent_name", ""),
    )
    return None


def consent_context(user) -> dict:
    profile = ensure_client_profile(user) if user.is_authenticated else None
    minor = is_minor(profile) if profile else None
    return {
        "health_consent_ok": has_valid_health_consent(profile) if profile else False,
        "health_consent_label": HEALTH_CONSENT_LABEL,
        "parental_consent_label": PARENTAL_CONSENT_LABEL,
        "health_consent_version": HEALTH_CONSENT_VERSION,
        "needs_health_consent": bool(profile) and not has_valid_health_consent(profile),
        "is_minor_account": minor is True,
        "minor_status_unknown": minor is None,
        "client_profile": profile,
    }
