"""Règles CDC v1.1 — catalogue libre, disponibilité masquée hors déclencheur explicite."""
from core.patient_access import ensure_search_access, user_needs_paywall

SESSION_VERIFY_STOCK_KEY = "gp_verify_stock_ids"


def _verified_stock_ids(request):
    raw = request.session.get(SESSION_VERIFY_STOCK_KEY) or []
    if not isinstance(raw, list):
        return set()
    return {int(x) for x in raw if str(x).isdigit()}


def mark_stock_verified(request, stock_id):
    ids = list(_verified_stock_ids(request))
    sid = int(stock_id)
    if sid not in ids:
        ids.append(sid)
    request.session[SESSION_VERIFY_STOCK_KEY] = ids[-50:]
    request.session.modified = True


def stock_availability_verified(request, stock_id):
    return int(stock_id) in _verified_stock_ids(request)


def can_show_availability(request, *, explicit_search=False, stock_id=None):
    """
    Affiche les indicateurs 🟢/stock faible/rupture uniquement si :
    - recherche textuelle explicite (paramètre q) avec forfait actif, ou
    - l'utilisateur a cliqué « Vérifier la disponibilité » pour ce produit.
    """
    if not request.user.is_authenticated or user_needs_paywall(request.user):
        return False
    if explicit_search and ensure_search_access(request.user, request.GET.get("q", "")):
        return True
    if stock_id is not None and stock_availability_verified(request, stock_id):
        return True
    return False


def catalog_mask_details():
    """CDC règle 1 — nom, prix, photo, description consultables librement."""
    return False
