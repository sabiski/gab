from django.conf import settings

from catalog.models import Category
from core.cart import get_cart
from core.patient_access import access_status_label, user_has_premium, user_needs_paywall


def branding(request):
    nav_categories = Category.objects.filter(is_active=True, parent__isnull=True).order_by("order")[:12]
    data = {
        "brand": settings.GABPHARMA,
        "nav_categories": nav_categories,
        "favorite_ids": [],
        "cart_count": sum(get_cart(request).values()) if hasattr(request, "session") else 0,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        fav_ids = list(user.favorites.values_list("stock_id", flat=True))
        data["favorite_ids"] = fav_ids
        data["favorites_count"] = len(fav_ids)
    else:
        data["favorites_count"] = 0
    data["access_status"] = access_status_label(user) if user and user.is_authenticated else None
    data["search_paywall"] = user_needs_paywall(user) if user else True
    data["has_premium"] = user_has_premium(user) if user and user.is_authenticated else False
    return data
