"""Vues patient intégrées à l'espace /espace/client/ (pas de sortie vers le site public)."""
from functools import wraps

from django.contrib.auth.decorators import login_required

from accounts.models import User
from backoffice.decorators import role_required, client_roles
from core import views as site_views


def _mark_client_portal(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        request.client_portal = True
        return view_func(request, *args, **kwargs)

    return wrapper


def _client_only(view_func):
    decorated = role_required(*client_roles)(login_required(login_url="login")(view_func))
    return _mark_client_portal(decorated)


@_client_only
def client_catalog(request):
    return site_views.search(request)


@_client_only
def client_favorites(request):
    return site_views.favorites_page(request)


@_client_only
def client_favorite_toggle(request, stock_id):
    return site_views.favorite_toggle(request, stock_id)


@_client_only
def client_cart(request):
    return site_views.cart_view(request)


@_client_only
def client_cart_add(request, stock_id):
    return site_views.cart_add_view(request, stock_id)


@_client_only
def client_checkout(request):
    return site_views.checkout_view(request)


@_client_only
def client_checkout_insurance_quote(request):
    return site_views.checkout_insurance_quote(request)


@_client_only
def client_subscriptions(request):
    return site_views.subscription_plans(request)


@_client_only
def client_emergency(request):
    return site_views.emergency_page(request)


@_client_only
def client_messages(request):
    return site_views.message_inbox(request)


@_client_only
def client_chat(request, slug):
    return site_views.pharmacy_chat(request, slug)


@_client_only
def client_product(request, stock_id):
    return site_views.product_detail(request, stock_id)


@_client_only
def client_verify_stock(request, stock_id):
    return site_views.verify_stock_availability(request, stock_id)


@_client_only
def client_order_confirmed(request, code):
    return site_views.payment_confirmed(request, code)


@_client_only
def client_order_tracking(request, code):
    return site_views.order_tracking_api(request, code)


@_client_only
def client_delivery_qr(request, code):
    return site_views.client_delivery_qr(request, code)
