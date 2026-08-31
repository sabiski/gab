from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from accounts.models import User


def role_required(*roles):
    """Exige authentification + un des rôles CDC."""

    def decorator(view_func):
        @login_required(login_url="login")
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                return redirect(request.user.backoffice_home())
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


admin_roles = (User.Role.ADMIN, User.Role.SUPERADMIN)
superadmin_roles = (User.Role.SUPERADMIN,)
pharmacy_roles = (User.Role.PHARMACIST,)
courier_roles = (User.Role.COURIER,)
authority_roles = (User.Role.AUTHORITY,)
authority_access_roles = (
    User.Role.AUTHORITY,
    User.Role.ADMIN,
    User.Role.SUPERADMIN,
)
regional_roles = (User.Role.REGIONAL_SUPERVISOR,)
partner_roles = (User.Role.PARTNER,)
insurer_access_roles = (
    User.Role.PARTNER,
    User.Role.ADMIN,
    User.Role.SUPERADMIN,
)
support_roles = (User.Role.SUPPORT, User.Role.ADMIN, User.Role.SUPERADMIN)
client_roles = (User.Role.CLIENT,)


def pharmacy_permission_required(permission):
    """Exige une permission ERP sur la pharmacie active (CDC §4.2)."""

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            from core.pharmacy_access import (
                has_pharmacy_permission,
                pharmacy_default_route,
                pharmacy_for_user,
            )

            pharmacy = pharmacy_for_user(request.user, request)
            if not pharmacy:
                messages.error(request, "Aucune pharmacie associée à votre compte.")
                return redirect("bo_pharmacy_dashboard")
            if not has_pharmacy_permission(request.user, pharmacy, permission):
                messages.error(request, "Vous n'avez pas les droits pour accéder à cette section.")
                return redirect(pharmacy_default_route(request.user, pharmacy))
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


def portal_permission_required(module_key: str):
    """Exige un module portail (autorité, partenaire, livreur…)."""

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            from core.platform_access import (
                authority_portal_permissions,
                partner_portal_permissions,
                portal_module_flags,
            )

            legacy_map = {"ruptures": "stocks", "compliance": "pharmacies"}
            key = legacy_map.get(module_key, module_key)

            if request.user.role in admin_roles and request.path.startswith("/espace/autorite/"):
                flags = authority_portal_permissions(request.user)
            elif request.user.role in admin_roles and request.path.startswith("/espace/assurance/"):
                flags = partner_portal_permissions(request.user)
            elif request.user.role == User.Role.PARTNER:
                flags = partner_portal_permissions(request.user)
            else:
                flags = portal_module_flags(request.user)
            if flags and not flags.get(key, False):
                messages.error(request, "Vous n'avez pas les droits pour accéder à cette section.")
                if request.user.role in admin_roles:
                    return redirect("bo_admin_dashboard")
                return redirect(request.user.backoffice_home())
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
