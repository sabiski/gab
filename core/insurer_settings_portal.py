"""Paramètres portail assureur — données et persistance."""
from __future__ import annotations

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import InsurerPortalSettings, PartnerProfile, PlatformSettings
from core.insurer_profile import InsurerPortalProfile

User = get_user_model()

SETTINGS_TABS = [
    ("general", "Général"),
    ("profile", "Profil"),
    ("security", "Sécurité"),
    ("users", "Utilisateurs"),
    ("notifications", "Notifications"),
    ("integrations", "Intégrations"),
    ("preferences", "Préférences"),
    ("system", "Système"),
]

LANGUAGE_CHOICES = [("fr", "Français"), ("en", "English")]
TIMEZONE_CHOICES = [("Africa/Libreville", "Libreville (GMT+1)")]
CURRENCY_CHOICES = [("XAF", "Franc CFA (XAF)")]
DATE_FORMAT_CHOICES = [("d/m/Y", "JJ/MM/AAAA"), ("m/d/Y", "MM/JJ/AAAA"), ("Y-m-d", "AAAA-MM-JJ")]

CATEGORY_LABELS = {
    "claims": "Demandes de prise en charge",
    "payments": "Paiements et remboursements",
    "fraud": "Alertes fraude et sécurité",
    "contracts": "Échéances de contrats et polices",
    "system": "Informations système",
    "reports": "Rapports et statistiques",
}


def get_portal_settings(profile: InsurerPortalProfile) -> InsurerPortalSettings | None:
    if not profile._partner:
        return None
    return InsurerPortalSettings.for_partner(profile._partner)


def _portal_view(portal: InsurerPortalSettings | None, platform: PlatformSettings):
    if portal:
        return portal
    return SimpleNamespace(
        language="fr",
        timezone="Africa/Libreville",
        currency="XAF",
        date_format="d/m/Y",
        notification_frequency=InsurerPortalSettings.Frequency.IMMEDIATE,
        maintenance_mode=False,
        session_expiry_minutes=platform.session_expiry_minutes,
        category_preferences=InsurerPortalSettings.default_categories(),
    )


def build_settings_context(profile: InsurerPortalProfile, user) -> dict:
    partner = profile._partner
    platform = PlatformSettings.load()
    portal_raw = get_portal_settings(profile)
    portal = _portal_view(portal_raw, platform)
    provider = profile.insurance_provider

    org_id = f"GA-{profile.acronym or 'ASS'}-{partner.pk:03d}" if partner else f"GA-PREVIEW-{provider.pk if provider else 0:03d}"

    team_count = 0
    if provider:
        team_count = PartnerProfile.objects.filter(
            partner_type=PartnerProfile.PartnerType.INSURER,
            insurance_provider=provider,
        ).count()

    last_login = user.last_login
    last_login_display = timezone.localtime(last_login).strftime("%d/%m/%Y à %H:%M") if last_login else "—"

    integrations = []
    raw = platform.integrations or {}
    insurer_keys = {
        "cnamgs": "API CNAMGS",
        "ascoma": "ASCOMA",
        "airtel_money": "Paiements Mobile Money",
        "moov_money": "Moov Money",
        "delivery_service": "Stockage cloud",
    }
    for key, label in insurer_keys.items():
        entry = raw.get(key, {})
        connected = entry.get("connected", False)
        integrations.append({
            "key": key,
            "label": entry.get("label") or label,
            "status": "Connecté" if connected else "Inactif",
            "tone": "success" if connected else "muted",
            "active": connected,
        })
    integrations.append({
        "key": "email",
        "label": "Services e-mail",
        "status": "Actif",
        "tone": "success",
        "active": True,
    })

    categories = []
    prefs = portal.category_preferences if portal else InsurerPortalSettings.default_categories()
    for key, label in CATEGORY_LABELS.items():
        categories.append({"key": key, "label": label, "enabled": prefs.get(key, True)})

    channels = []
    if portal and partner:
        from notifications.models import NotificationPreference

        np, _ = NotificationPreference.objects.get_or_create(user=partner.user)
        channels = [
            {"key": "email", "label": "E-mail", "active": np.email_enabled, "icon": "mail"},
            {"key": "sms", "label": "SMS", "active": np.sms_enabled, "icon": "sms"},
            {"key": "push", "label": "Application (Push)", "active": np.push_enabled, "icon": "smartphone"},
            {"key": "in_app", "label": "Web", "active": True, "icon": "language"},
        ]

    return {
        "org": {
            "name": profile.organization_name,
            "acronym": profile.acronym,
            "account_type": "Compte professionnel",
            "org_id": org_id,
            "registration_number": partner.registration_number if partner else "",
            "tax_id": partner.tax_id if partner else "",
            "address": profile.headquarters_address or "Libreville, Gabon",
            "phone": user.phone or "—",
            "email": user.email,
            "country": partner.country if partner else "Gabon",
            "validated": profile.is_validated,
        },
        "portal": portal,
        "portal_raw": portal_raw,
        "platform": platform,
        "security": {
            "two_factor": platform.two_factor_required,
            "last_login": last_login_display,
            "session_expiry": portal.session_expiry_minutes if portal else platform.session_expiry_minutes,
        },
        "team_count": max(team_count, 1),
        "integrations": integrations,
        "categories": categories,
        "channels": channels,
        "language_choices": LANGUAGE_CHOICES,
        "timezone_choices": TIMEZONE_CHOICES,
        "currency_choices": CURRENCY_CHOICES,
        "date_format_choices": DATE_FORMAT_CHOICES,
        "frequency_choices": InsurerPortalSettings.Frequency.choices,
    }


def save_general_settings(profile: InsurerPortalProfile, user, post) -> None:
    user.first_name = post.get("first_name", user.first_name)
    user.last_name = post.get("last_name", user.last_name)
    user.phone = post.get("phone", user.phone)
    user.save(update_fields=["first_name", "last_name", "phone"])
    if profile._partner:
        profile.headquarters_address = post.get("headquarters_address", profile.headquarters_address)
        profile.rep_job_title = post.get("rep_job_title", profile.rep_job_title)
        profile.save(update_fields=["headquarters_address", "rep_job_title"])


def save_preferences(profile: InsurerPortalProfile, post) -> None:
    portal = get_portal_settings(profile)
    if not portal:
        return
    portal.language = post.get("language", portal.language)
    portal.timezone = post.get("timezone", portal.timezone)
    portal.currency = post.get("currency", portal.currency)
    portal.date_format = post.get("date_format", portal.date_format)
    portal.save(update_fields=["language", "timezone", "currency", "date_format", "updated_at"])


def save_notification_settings(profile: InsurerPortalProfile, user, post) -> None:
    portal = get_portal_settings(profile)
    if not portal:
        return
    prefs = dict(portal.category_preferences or InsurerPortalSettings.default_categories())
    for key in CATEGORY_LABELS:
        prefs[key] = post.get(f"cat_{key}") == "on"
    portal.category_preferences = prefs
    portal.notification_frequency = post.get("notification_frequency", portal.notification_frequency)
    portal.save(update_fields=["category_preferences", "notification_frequency", "updated_at"])

    from notifications.models import NotificationPreference

    np, _ = NotificationPreference.objects.get_or_create(user=user)
    np.email_enabled = post.get("channel_email") == "on"
    np.sms_enabled = post.get("channel_sms") == "on"
    np.push_enabled = post.get("channel_push") == "on"
    np.save(update_fields=["email_enabled", "sms_enabled", "push_enabled", "updated_at"])


def save_system_settings(profile: InsurerPortalProfile, post) -> None:
    portal = get_portal_settings(profile)
    if not portal:
        return
    portal.maintenance_mode = post.get("maintenance_mode") == "on"
    try:
        portal.session_expiry_minutes = int(post.get("session_expiry_minutes") or portal.session_expiry_minutes)
    except ValueError:
        pass
    portal.save(update_fields=["maintenance_mode", "session_expiry_minutes", "updated_at"])
