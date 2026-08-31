"""Panier session + helpers commande web."""
from catalog.models import PharmacyStock
from deliveries.models import Delivery
from orders.models import Order, OrderItem, Prescription
from pharmacies.models import Pharmacy


CART_KEY = "gabpharma_cart"


def get_cart(request):
    return request.session.get(CART_KEY, {})


def save_cart(request, cart):
    request.session[CART_KEY] = cart
    request.session.modified = True


def cart_add(request, stock_id, qty=1):
    cart = get_cart(request)
    sid = str(stock_id)
    stock = PharmacyStock.objects.select_related("pharmacy", "medicine").filter(
        pk=stock_id, is_visible=True, quantity__gt=0
    ).first()
    if not stock:
        return None, "Produit indisponible."
    # Une seule pharmacie par panier
    if cart:
        first = PharmacyStock.objects.filter(pk=next(iter(cart.keys()))).first()
        if first and first.pharmacy_id != stock.pharmacy_id:
            return None, "Videz le panier ou commandez d’abord chez la même pharmacie."
    cart[sid] = min(int(cart.get(sid, 0)) + max(1, int(qty)), stock.quantity)
    save_cart(request, cart)
    return stock, None


def cart_lines(request):
    cart = get_cart(request)
    lines = []
    for sid, qty in cart.items():
        stock = (
            PharmacyStock.objects.select_related("medicine", "pharmacy")
            .filter(pk=sid)
            .first()
        )
        if stock:
            lines.append({"stock": stock, "qty": qty, "line_total": stock.display_price * qty})
    return lines


def cart_needs_prescription(lines):
    return any(l["stock"].medicine.requires_prescription for l in lines)


def cart_clear(request):
    request.session.pop(CART_KEY, None)
    request.session.modified = True


def ensure_delivery_for_order(order):
    """Crée une livraison PENDING quand la commande est prête (domicile/express)."""
    if order.delivery_mode == Order.DeliveryMode.PICKUP:
        return None
    if order.status != Order.Status.READY:
        return None
    delivery, _ = Delivery.objects.get_or_create(
        order=order,
        defaults={"status": Delivery.Status.PENDING},
    )
    if delivery.status in {Delivery.Status.DELIVERED, Delivery.Status.FAILED}:
        return delivery
    if delivery.status != Delivery.Status.PENDING and not delivery.courier_id:
        delivery.status = Delivery.Status.PENDING
        delivery.save(update_fields=["status", "updated_at"])
    return delivery
