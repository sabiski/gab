"""URLs espace patient — rester sous /espace/client/."""
from django.urls import reverse

PORTAL_ROUTE_MAP = {
    "search": "bo_client_catalog",
    "cart": "bo_client_cart",
    "cart_add": "bo_client_cart_add",
    "checkout": "bo_client_checkout",
    "checkout_insurance_quote": "bo_client_checkout_insurance_quote",
    "favorites": "bo_client_favorites",
    "favorite_toggle": "bo_client_favorite_toggle",
    "subscription_plans": "bo_client_subscriptions",
    "emergency": "bo_client_emergency",
    "pharmacy_chat": "bo_client_chat",
    "messages_inbox": "bo_client_messages",
    "payment_confirmed": "bo_client_order_confirmed",
    "product_detail": "bo_client_product",
    "verify_stock_availability": "bo_client_verify_stock",
    "orders": "bo_client_orders",
    "profile": "bo_client_settings",
    "profile_personal": "bo_client_profile_personal",
    "profile_address": "bo_client_profile_address",
    "profile_payment": "bo_client_profile_payment",
    "profile_notifications": "bo_client_notifications",
    "profile_preferences": "bo_client_profile_preferences",
    "profile_security": "bo_client_profile_security",
    "profile_privacy": "bo_client_profile_privacy",
    "home": "bo_client_dashboard",
}


def in_client_espace(request) -> bool:
    if getattr(request, "client_portal", False):
        return True
    path = getattr(request, "path", "") or ""
    return path.startswith("/espace/client")


def portal_reverse(request, viewname, *args, **kwargs):
    if in_client_espace(request):
        viewname = PORTAL_ROUTE_MAP.get(viewname, viewname)
    return reverse(viewname, args=args, kwargs=kwargs)


def portal_context(request):
    ctx = {
        "client_portal": in_client_espace(request),
        "portal_u": lambda name, *a, **kw: portal_reverse(request, name, *a, **kw),
    }
    if in_client_espace(request):
        from accounts.models import User

        user = getattr(request, "user", None)
        if user and user.is_authenticated and user.role == User.Role.CLIENT:
            ctx["bo_role"] = "client"
            ctx["is_superadmin"] = False
    return ctx
