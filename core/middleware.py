"""Redirige les patients connectés vers l'espace /espace/client/."""
from django.shortcuts import redirect
from django.urls import reverse

from accounts.models import User

# Préfixes publics → nom de route espace client
PUBLIC_TO_ESPACE = [
    ("/recherche", "bo_client_catalog"),
    ("/forfaits", "bo_client_subscriptions"),
    ("/urgence", "bo_client_emergency"),
    ("/messagerie", "bo_client_messages"),
    ("/panier", "bo_client_cart"),
    ("/commander", "bo_client_checkout"),
    ("/favoris", "bo_client_favorites"),
    ("/commandes", "bo_client_orders"),
    ("/profil", "bo_client_settings"),
    ("/pharmacies", "bo_client_catalog"),
]


class ClientEspaceMiddleware:
    """Empêche un client connecté de naviguer sur le site public patient."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user
            and user.is_authenticated
            and user.role == User.Role.CLIENT
            and not request.path.startswith("/espace/")
            and not request.path.startswith("/auth/")
            and not request.path.startswith("/api/")
            and not request.path.startswith("/django-admin/")
            and request.path not in {"/offline/", "/manifest.webmanifest"}
        ):
            path = request.path
            if path.startswith("/produit/"):
                parts = path.strip("/").split("/")
                if len(parts) >= 2 and parts[1].isdigit():
                    return redirect("bo_client_product", stock_id=int(parts[1]))
            if path.startswith("/messagerie/pharmacie/"):
                slug = path.rstrip("/").split("/")[-1]
                qs = request.META.get("QUERY_STRING", "")
                url = reverse("bo_client_chat", kwargs={"slug": slug})
                if qs:
                    url = f"{url}?{qs}"
                return redirect(url)
            if path.startswith("/commande/") and path.endswith("/confirme/"):
                code = path.strip("/").split("/")[1]
                return redirect("bo_client_order_confirmed", code=code)
            for prefix, route_name in PUBLIC_TO_ESPACE:
                if path == prefix or path.startswith(prefix + "/"):
                    if route_name == "bo_client_catalog" and path.startswith("/produit/"):
                        break
                    return redirect(route_name)
            if path in {"", "/"}:
                return redirect("bo_client_dashboard")

        return self.get_response(request)
