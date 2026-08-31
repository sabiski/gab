"""Contexte portail assurance — profil réel ou aperçu admin depuis InsuranceProvider."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.models import PartnerProfile
from payments.models import InsuranceProvider


@dataclass
class InsurerPortalProfile:
    organization_name: str
    acronym: str = ""
    insurance_provider: InsuranceProvider | None = None
    user: Any = None
    validated_at: Any = None
    headquarters_address: str = ""
    rep_job_title: str = ""
    preview: bool = False
    _partner: PartnerProfile | None = None

    @classmethod
    def from_partner(cls, profile: PartnerProfile) -> InsurerPortalProfile:
        return cls(
            organization_name=profile.organization_name,
            acronym=profile.acronym,
            insurance_provider=profile.insurance_provider,
            user=profile.user,
            validated_at=profile.validated_at,
            headquarters_address=profile.headquarters_address,
            rep_job_title=profile.rep_job_title,
            preview=False,
            _partner=profile,
        )

    @classmethod
    def from_provider(cls, provider: InsuranceProvider, user: Any) -> InsurerPortalProfile:
        return cls(
            organization_name=provider.name,
            acronym=provider.code,
            insurance_provider=provider,
            user=user,
            validated_at=timezone.now(),
            preview=True,
        )

    @property
    def insurance_provider_id(self) -> int | None:
        if self.insurance_provider:
            return self.insurance_provider.pk
        if self._partner:
            return self._partner.insurance_provider_id
        return None

    @property
    def display_name(self) -> str:
        return self.acronym or self.organization_name

    @property
    def is_validated(self) -> bool:
        if self._partner:
            return self._partner.is_validated
        return True

    def save(self, update_fields=None) -> None:
        if not self._partner:
            return
        self._partner.headquarters_address = self.headquarters_address
        self._partner.rep_job_title = self.rep_job_title
        self._partner.save(update_fields=update_fields or ["headquarters_address", "rep_job_title"])


def resolve_insurer_profile(user, request=None) -> InsurerPortalProfile:
    """Profil portail assureur — compte partenaire ou aperçu admin (CNAMGS, NSIA…)."""
    from backoffice.decorators import admin_roles

    if user.role in admin_roles:
        partners = PartnerProfile.objects.filter(
            partner_type=PartnerProfile.PartnerType.INSURER
        ).select_related("insurance_provider", "user")
        provider_id = (request.GET.get("provider") if request else None) or None
        if provider_id:
            partner = partners.filter(insurance_provider_id=provider_id).first()
            if partner:
                return InsurerPortalProfile.from_partner(partner)
            provider = InsuranceProvider.objects.filter(pk=provider_id).first()
            if provider:
                return InsurerPortalProfile.from_provider(provider, user)
        partner = partners.first()
        if partner:
            return InsurerPortalProfile.from_partner(partner)
        provider = InsuranceProvider.objects.filter(is_active=True).order_by("name").first()
        if not provider:
            provider = InsuranceProvider.objects.order_by("name").first()
        if provider:
            return InsurerPortalProfile.from_provider(provider, user)
        raise Http404(
            "Aucun assureur en base. Ajoutez CNAMGS ou un autre organisme dans "
            "Assurances — pilotage, ou créez un compte via /auth/inscription-assurance/."
        )

    partner = get_object_or_404(
        PartnerProfile.objects.select_related("insurance_provider"),
        user=user,
        partner_type=PartnerProfile.PartnerType.INSURER,
    )
    return InsurerPortalProfile.from_partner(partner)
