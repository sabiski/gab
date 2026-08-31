"""Portail institution partenaire (CDC §4.2 / §4.13) — lecture contractuelle."""
from django.db.models import Count, Sum

from accounts.models import PartnerProfile, User
from backoffice.decorators import partner_roles, portal_permission_required, role_required
from backoffice.views import _ctx
from django.shortcuts import get_object_or_404, render
from payments.models import InsuranceClaim


def _partner_profile(user):
    return get_object_or_404(PartnerProfile, user=user)


@role_required(*partner_roles)
@portal_permission_required("dashboard")
def partner_dashboard(request):
    profile = _partner_profile(request.user)
    claims = InsuranceClaim.objects.none()
    if profile.insurance_provider_id:
        claims = InsuranceClaim.objects.filter(provider_id=profile.insurance_provider_id)
    elif profile.partner_type == PartnerProfile.PartnerType.INSURER:
        claims = InsuranceClaim.objects.none()

    stats = {
        "pending": claims.filter(status=InsuranceClaim.Status.PENDING).count(),
        "approved": claims.filter(status=InsuranceClaim.Status.APPROVED).count(),
        "paid": claims.filter(status=InsuranceClaim.Status.PAID).count(),
        "total_amount": claims.filter(
            status__in={InsuranceClaim.Status.APPROVED, InsuranceClaim.Status.PAID}
        ).aggregate(s=Sum("amount"))["s"]
        or 0,
    }
    by_status = (
        claims.values("status")
        .annotate(c=Count("id"), total=Sum("amount"))
        .order_by("status")
    )
    recent = claims.select_related("provider", "order").order_by("-created_at")[:20]

    return render(
        request,
        "backoffice/partner/dashboard.html",
        _ctx(
            request,
            "dashboard",
            profile=profile,
            stats=stats,
            by_status=by_status,
            recent_claims=recent,
        ),
    )
