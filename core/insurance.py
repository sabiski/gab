"""Module Assurance — vérification, calcul et prise en charge au checkout (CDC §4.13)."""
from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Sum
from django.utils import timezone

from payments.models import InsuranceClaim, InsuranceProvider

DEFAULT_ANNUAL_CAP = 500_000


@dataclass
class InsuranceQuote:
    ok: bool
    reason: str = ""
    coverage_amount: int = 0
    client_share: int = 0
    gross_total: int = 0
    provider: InsuranceProvider | None = None

    @property
    def coverage_rate(self) -> int:
        if not self.provider or self.gross_total <= 0:
            return 0
        return int((self.coverage_amount / self.gross_total) * 100)


def _client_profile(user):
    from accounts.models import ClientProfile

    profile, _ = ClientProfile.objects.get_or_create(user=user)
    return profile


def _annual_claimed(user, provider) -> int:
    year = timezone.localdate().year
    total = (
        InsuranceClaim.objects.filter(
            client=user,
            provider=provider,
            created_at__year=year,
        )
        .exclude(status=InsuranceClaim.Status.REJECTED)
        .aggregate(s=Sum("amount"))["s"]
    )
    return int(total or 0)


def verify_insured(user, provider: InsuranceProvider | None) -> tuple[bool, str]:
    """Contrôle abonnement actif et identité du bénéficiaire."""
    if not provider or not provider.is_active:
        return False, "Organisme d'assurance indisponible."
    from core.insurer_subscription import user_has_active_subscription

    profile = _client_profile(user)
    if user_has_active_subscription(user, provider.pk):
        if profile.insurance_provider_id != provider.pk:
            profile.insurance_provider = provider
            profile.save(update_fields=["insurance_provider"])
        return True, ""

    number = (profile.insurance_number or "").strip().upper()
    if len(number) < 5:
        return False, "Numéro d'assuré manquant — souscrivez à une formule ou complétez votre profil."
    if profile.insurance_provider_id and profile.insurance_provider_id != provider.pk:
        return False, "Vous n'êtes pas affilié à cet organisme d'assurance."
    if number.endswith("X"):
        return False, "Affiliation inactive ou numéro non reconnu par l'assureur."
    return False, (
        "Aucun abonnement actif auprès de cet organisme. "
        "Souscrivez à une formule ou contactez votre assureur."
    )


def quote_insurance(user, provider: InsuranceProvider | None, gross_total: int) -> InsuranceQuote:
    """Calcule prise en charge et reste à charge avant paiement."""
    gross_total = max(0, int(gross_total))
    if not provider:
        return InsuranceQuote(ok=False, reason="Choisissez un organisme d'assurance.", gross_total=gross_total)

    ok, reason = verify_insured(user, provider)
    if not ok:
        return InsuranceQuote(ok=False, reason=reason, gross_total=gross_total, provider=provider)

    annual_cap = getattr(provider, "annual_cap", None) or DEFAULT_ANNUAL_CAP
    already = _annual_claimed(user, provider)
    remaining_cap = max(0, annual_cap - already)
    if remaining_cap <= 0:
        return InsuranceQuote(
            ok=False,
            reason="Plafond annuel de prise en charge atteint pour votre contrat.",
            gross_total=gross_total,
            provider=provider,
        )

    theoretical = int(gross_total * provider.coverage_rate / 100)
    coverage = min(theoretical, remaining_cap, gross_total)
    client_share = max(0, gross_total - coverage)

    if coverage <= 0:
        return InsuranceQuote(
            ok=False,
            reason="Aucune prise en charge possible pour cette commande.",
            gross_total=gross_total,
            provider=provider,
        )

    return InsuranceQuote(
        ok=True,
        coverage_amount=coverage,
        client_share=client_share,
        gross_total=gross_total,
        provider=provider,
    )


def apply_insurance_to_order(order, user, provider: InsuranceProvider | None) -> InsuranceQuote:
    """Applique la couverture sur la commande et recalcule le total client."""
    gross = order.subtotal + order.delivery_fee - order.discount
    quote = quote_insurance(user, provider, gross)
    if quote.ok:
        order.insurance_coverage = quote.coverage_amount
        order.insurance_provider = provider
        order.save(update_fields=["insurance_coverage", "insurance_provider", "updated_at"])
    else:
        order.insurance_coverage = 0
        order.insurance_provider = None
        order.save(update_fields=["insurance_coverage", "insurance_provider", "updated_at"])
    order.recalculate_totals()
    quote.client_share = order.total
    return quote


def create_insurance_claim_for_order(order, user, provider, amount: int) -> InsuranceClaim:
    """Crée la demande de prise en charge transmise à l'assureur."""
    claim = InsuranceClaim.objects.create(
        client=user,
        provider=provider,
        order=order,
        amount=amount,
        status=InsuranceClaim.Status.PENDING,
        review_notes="Demande automatique à la validation de la commande.",
    )
    from core.insurer_notifications import on_insurance_claim_created

    on_insurance_claim_created(claim)
    return claim


def order_gross_before_insurance(order) -> int:
    """Montant brut avant déduction assurance (FCFA)."""
    loyalty = getattr(order, "loyalty_discount", 0) or 0
    return max(0, order.subtotal + order.delivery_fee - order.discount - loyalty)


def get_pending_insurance_claim(order):
    if not order.insurance_provider_id or order.insurance_coverage <= 0:
        return None
    return (
        order.insurance_claims.filter(status=InsuranceClaim.Status.PENDING)
        .order_by("-created_at")
        .first()
    )


def order_awaiting_insurance(order) -> bool:
    return get_pending_insurance_claim(order) is not None


def finalize_insurance_approval(claim: InsuranceClaim) -> None:
    """Après validation assureur : synchronise la commande, notifie pharmacie et patient."""
    from core.pharmacy_notifications import notify_pharmacy_new_order
    from notifications.models import Notification
    from notifications.services import notify_user
    from orders.models import ensure_validation_code_normalized

    order = claim.order
    order.insurance_coverage = claim.amount
    order.recalculate_totals()
    notify_pharmacy_new_order(order)

    ensure_validation_code_normalized(order, save=True)
    code = order.validation_code_display
    notify_user(
        claim.client,
        f"Assurance validée — commande {order.code}",
        (
            f"Votre prise en charge a été validée par {claim.provider.name}. "
            f"Votre code de retrait / livraison : {code}. "
            "Présentez-le à la pharmacie ou au livreur."
        ),
        notification_type=Notification.Type.ORDER,
        data={"order_id": order.id, "code": order.code, "claim_id": claim.id},
        transactional=True,
    )


def finalize_insurance_rejection(claim: InsuranceClaim) -> None:
    """Après refus assureur : retire la prise en charge et recalcule le reste à charge."""
    from notifications.models import Notification
    from notifications.services import notify_user

    order = claim.order
    order.insurance_coverage = 0
    order.recalculate_totals()

    notes = (claim.review_notes or "").strip()
    reason = f" Motif : {notes}" if notes else ""
    notify_user(
        claim.client,
        f"Prise en charge refusée — commande {order.code}",
        (
            f"Votre demande auprès de {claim.provider.name} a été refusée.{reason} "
            "Contactez le support si vous souhaitez régulariser votre commande."
        ),
        notification_type=Notification.Type.INFO,
        data={"order_id": order.id, "claim_id": claim.id},
        transactional=True,
    )
