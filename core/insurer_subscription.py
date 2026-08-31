"""Abonnements assurance — catalogue de formules et souscriptions bénéficiaires."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.utils import timezone

from accounts.models import ClientProfile, InsuranceMemberSubscription
from core.insurer_profile import InsurerPortalProfile
from payments.models import InsuranceProvider, InsuranceSubscriptionPlan

User = get_user_model()

DEFAULT_PLANS = [
    {
        "code": "essentiel",
        "name": "Essentiel",
        "premium_amount": 15_000,
        "coverage_rate": 60,
        "sort_order": 1,
        "description": "Couverture de base pour les particuliers.",
    },
    {
        "code": "entreprise",
        "name": "Santé Entreprise",
        "premium_amount": 35_000,
        "coverage_rate": 80,
        "sort_order": 2,
        "description": "Formule collective et familiale.",
    },
    {
        "code": "plus",
        "name": "Santé Plus",
        "premium_amount": 55_000,
        "coverage_rate": 100,
        "sort_order": 3,
        "description": "Couverture maximale et services étendus.",
    },
]

STATUS_UI = {
    InsuranceMemberSubscription.Status.ACTIVE: ("Actif", "active", "check_circle"),
    InsuranceMemberSubscription.Status.PENDING: ("En attente", "pending", "hourglass_top"),
    InsuranceMemberSubscription.Status.SUSPENDED: ("Suspendu", "suspended", "block"),
    InsuranceMemberSubscription.Status.EXPIRED: ("Expiré", "expired", "cancel"),
    InsuranceMemberSubscription.Status.CANCELLED: ("Résilié", "cancelled", "block"),
}


def _period_end(start: date, billing_period: str) -> date:
    if billing_period == InsuranceSubscriptionPlan.BillingPeriod.ANNUAL:
        try:
            return start.replace(year=start.year + 1) - timedelta(days=1)
        except ValueError:
            return date(start.year + 1, 2, 28)
    month = start.month + 1
    year = start.year
    if month > 12:
        month = 1
        year += 1
    try:
        next_start = date(year, month, start.day)
    except ValueError:
        next_start = date(year, month, 28)
    return next_start - timedelta(days=1)


def ensure_provider_plans(provider: InsuranceProvider) -> list[InsuranceSubscriptionPlan]:
    """Garantit le catalogue de formules pour un organisme."""
    plans = []
    for spec in DEFAULT_PLANS:
        plan, _ = InsuranceSubscriptionPlan.objects.get_or_create(
            provider=provider,
            code=spec["code"],
            defaults={
                "name": spec["name"],
                "premium_amount": spec["premium_amount"],
                "coverage_rate": spec["coverage_rate"],
                "sort_order": spec["sort_order"],
                "description": spec["description"],
                "is_active": True,
            },
        )
        plans.append(plan)
    return plans


def provider_plans(profile: InsurerPortalProfile, *, active_only: bool = False):
    if not profile.insurance_provider:
        return InsuranceSubscriptionPlan.objects.none()
    ensure_provider_plans(profile.insurance_provider)
    qs = InsuranceSubscriptionPlan.objects.filter(provider=profile.insurance_provider)
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.order_by("sort_order", "name")


def subscriptions_base_queryset(profile: InsurerPortalProfile):
    if not profile.insurance_provider_id:
        return InsuranceMemberSubscription.objects.none()
    return InsuranceMemberSubscription.objects.filter(
        insurance_provider_id=profile.insurance_provider_id,
    ).select_related(
        "user",
        "client_profile",
        "client_profile__user",
        "insurance_provider",
        "subscription_plan",
    )


def _next_reference(provider_id: int) -> str:
    year = timezone.localdate().year
    count = InsuranceMemberSubscription.objects.filter(
        insurance_provider_id=provider_id,
        reference__startswith=f"AB-{year}-",
    ).count()
    return f"AB-{year}-{count + 1:05d}"


def _resolve_user(identifier: str) -> User | None:
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    user = User.objects.filter(email__iexact=identifier).first()
    if user:
        return user
    user = User.objects.filter(username__iexact=identifier).first()
    if user:
        return user
    cp = ClientProfile.objects.filter(insurance_number__iexact=identifier).select_related("user").first()
    return cp.user if cp else None


def subscribe_user(
    user: User,
    provider: InsuranceProvider,
    plan: InsuranceSubscriptionPlan,
    *,
    status: str | None = None,
    employer_name: str = "",
) -> InsuranceMemberSubscription:
    """Souscrit un utilisateur à une formule — met à jour son profil client."""
    profile, _ = ClientProfile.objects.get_or_create(user=user)
    if not profile.insurance_number:
        profile.insurance_number = f"{provider.code}-{user.pk:06d}"
    profile.insurance_provider = provider
    profile.save(update_fields=["insurance_number", "insurance_provider"])

    today = timezone.localdate()
    sub_status = status or InsuranceMemberSubscription.Status.ACTIVE
    plan_code = plan.code if plan.code in dict(InsuranceMemberSubscription.Plan.choices) else "entreprise"

    return InsuranceMemberSubscription.objects.create(
        user=user,
        client_profile=profile,
        insurance_provider=provider,
        subscription_plan=plan,
        reference=_next_reference(provider.pk),
        plan=plan_code,
        premium_amount=plan.premium_amount,
        coverage_rate=plan.coverage_rate,
        employer_name=employer_name,
        status=sub_status,
        starts_at=today,
        ends_at=_period_end(today, plan.billing_period),
    )


def user_has_active_subscription(user, provider_id: int | None = None) -> bool:
    today = timezone.localdate()
    qs = InsuranceMemberSubscription.objects.filter(
        user=user,
        status=InsuranceMemberSubscription.Status.ACTIVE,
        starts_at__lte=today,
        ends_at__gte=today,
    )
    if provider_id:
        qs = qs.filter(insurance_provider_id=provider_id)
    return qs.exists()


def subscriptions_apply_search(qs, q: str):
    q = (q or "").strip()
    if not q:
        return qs
    return qs.filter(
        Q(reference__icontains=q)
        | Q(user__first_name__icontains=q)
        | Q(user__last_name__icontains=q)
        | Q(user__email__icontains=q)
        | Q(user__username__icontains=q)
        | Q(client_profile__insurance_number__icontains=q)
        | Q(employer_name__icontains=q)
        | Q(subscription_plan__name__icontains=q)
    )


def subscriptions_filter_tab(qs, tab: str):
    tab = (tab or "all").strip()
    today = timezone.localdate()
    if tab == "active":
        return qs.filter(
            status=InsuranceMemberSubscription.Status.ACTIVE,
            ends_at__gte=today,
        )
    if tab == "pending":
        return qs.filter(status=InsuranceMemberSubscription.Status.PENDING)
    if tab == "expired":
        return qs.filter(
            Q(status__in={
                InsuranceMemberSubscription.Status.EXPIRED,
                InsuranceMemberSubscription.Status.CANCELLED,
            })
            | Q(ends_at__lt=today, status=InsuranceMemberSubscription.Status.ACTIVE)
        )
    return qs


def subscriptions_apply_filters(qs, *, plan: str | None = None, status: str | None = None):
    if plan:
        qs = qs.filter(Q(plan=plan) | Q(subscription_plan__code=plan))
    if status:
        qs = qs.filter(status=status)
    return qs


def _subscription_user(sub: InsuranceMemberSubscription) -> User:
    return sub.user or (sub.client_profile.user if sub.client_profile_id else None)


def enrich_subscription(sub: InsuranceMemberSubscription) -> InsuranceMemberSubscription:
    user = _subscription_user(sub)
    sub.member_name = user.get_full_name() or user.username if user else "—"
    sub.member_number = "—"
    if sub.client_profile_id and sub.client_profile.insurance_number:
        sub.member_number = sub.client_profile.insurance_number
    elif user:
        cp = getattr(user, "client_profile", None)
        if cp and cp.insurance_number:
            sub.member_number = cp.insurance_number
    sub.member_city = user.city if user else "—"
    sub.plan_label = (
        sub.subscription_plan.name
        if sub.subscription_plan_id
        else sub.get_plan_display()
    )
    sub.ui_status = STATUS_UI.get(sub.status, ("—", "pending", "info"))
    return sub


def subscriptions_stats(profile: InsurerPortalProfile) -> dict:
    qs = subscriptions_base_queryset(profile)
    today = timezone.localdate()
    total = qs.count()
    active = qs.filter(
        status=InsuranceMemberSubscription.Status.ACTIVE,
        ends_at__gte=today,
    ).count()
    pending = qs.filter(status=InsuranceMemberSubscription.Status.PENDING).count()
    expired = qs.filter(
        Q(status__in={
            InsuranceMemberSubscription.Status.EXPIRED,
            InsuranceMemberSubscription.Status.CANCELLED,
        })
        | Q(ends_at__lt=today, status=InsuranceMemberSubscription.Status.ACTIVE)
    ).count()

    def pct(n):
        return round((n / total) * 100, 1) if total else 0

    monthly_revenue = qs.filter(
        status=InsuranceMemberSubscription.Status.ACTIVE,
        ends_at__gte=today,
    ).aggregate(s=Sum("premium_amount"))["s"] or 0

    return {
        "total": total,
        "active": active,
        "pending": pending,
        "expired": expired,
        "pct_total": 100 if total else 0,
        "pct_active": pct(active),
        "pct_pending": pct(pending),
        "pct_expired": pct(expired),
        "monthly_revenue": int(monthly_revenue),
        "plans_count": provider_plans(profile).count(),
    }


def subscriptions_charts_data(profile: InsurerPortalProfile) -> dict:
    qs = subscriptions_base_queryset(profile)
    plan_counts: dict[str, int] = {}
    for sub in qs.iterator():
        label = sub.subscription_plan.name if sub.subscription_plan_id else sub.get_plan_display()
        plan_counts[label] = plan_counts.get(label, 0) + 1
    if not plan_counts:
        for p in provider_plans(profile):
            plan_counts[p.name] = 0
    return {
        "plan_split": {
            "labels": list(plan_counts.keys()),
            "data": list(plan_counts.values()),
            "colors": ["#9ca3af", "#3b82f6", "#7c3aed", "#10b981", "#f59e0b"][: len(plan_counts)],
        },
    }


def subscriptions_export_csv(qs) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Référence", "Bénéficiaire", "N° assuré", "Formule", "Cotisation", "Début", "Fin", "Statut"]
    )
    for sub in qs.iterator():
        enrich_subscription(sub)
        writer.writerow([
            sub.reference,
            sub.member_name,
            sub.member_number,
            sub.plan_label,
            sub.premium_amount,
            sub.starts_at.strftime("%d/%m/%Y"),
            sub.ends_at.strftime("%d/%m/%Y"),
            sub.get_status_display(),
        ])
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="abonnements-{timezone.localdate().isoformat()}.csv"'
    )
    return response


def update_subscription_status(sub_id: int, provider_id: int, new_status: str) -> bool:
    allowed = {s for s, _ in InsuranceMemberSubscription.Status.choices}
    if new_status not in allowed:
        return False
    return (
        InsuranceMemberSubscription.objects.filter(
            pk=sub_id,
            insurance_provider_id=provider_id,
        ).update(status=new_status, updated_at=timezone.now())
        > 0
    )


def toggle_plan_active(plan_id: int, provider_id: int) -> bool:
    plan = InsuranceSubscriptionPlan.objects.filter(pk=plan_id, provider_id=provider_id).first()
    if not plan:
        return False
    plan.is_active = not plan.is_active
    plan.save(update_fields=["is_active", "updated_at"])
    return True
