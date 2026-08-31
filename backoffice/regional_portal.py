"""Portail superviseur régional (CDC §4.2 / §4.12)."""
from django.db.models import Count, Sum

from accounts.models import User
from backoffice.decorators import regional_roles, role_required
from backoffice.views import _ctx
from catalog.models import PharmacyStock
from django.shortcuts import render
from orders.models import Order
from pharmacies.models import Pharmacy


def _region_for(user):
    return (user.assigned_region or "").strip()


@role_required(*regional_roles)
def regional_dashboard(request):
    region = _region_for(request.user)
    pharmacies = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
    if region:
        pharmacies = pharmacies.filter(region=region)
    pharmacy_ids = list(pharmacies.values_list("id", flat=True))

    orders_qs = Order.objects.filter(pharmacy_id__in=pharmacy_ids).exclude(
        status=Order.Status.CART
    )
    kpis = {
        "pharmacies": pharmacies.count(),
        "orders": orders_qs.count(),
        "ca": orders_qs.aggregate(s=Sum("total"))["s"] or 0,
        "low_stock": PharmacyStock.objects.filter(
            pharmacy_id__in=pharmacy_ids, quantity__lte=5, quantity__gt=0
        ).count(),
        "out_of_stock": PharmacyStock.objects.filter(
            pharmacy_id__in=pharmacy_ids, quantity=0
        ).count(),
    }
    by_city = (
        pharmacies.values("city")
        .annotate(c=Count("id"))
        .order_by("-c")[:8]
    )
    recent_orders = orders_qs.select_related("pharmacy", "client").order_by("-created_at")[:12]

    return render(
        request,
        "backoffice/regional/dashboard.html",
        _ctx(
            request,
            "dashboard",
            region=region or "Non définie",
            kpis=kpis,
            by_city=by_city,
            recent_orders=recent_orders,
            pharmacies=pharmacies.order_by("name")[:20],
        ),
    )
