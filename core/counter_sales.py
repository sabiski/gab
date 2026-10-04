"""Ventes hors application (comptoir, téléphone, WhatsApp) — caisse pharmacie.

Les clients qui ne passent pas par Gab'Pharma restent servis : la pharmacie
enregistre la vente pour garder un stock juste et un chiffre d'affaires complet.
Aucune commission plateforme n'est prélevée.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from catalog.models import PharmacyStock, StockMovement
from orders.models import CounterSale, CounterSaleItem


class CounterSaleError(ValueError):
    pass


@dataclass
class SaleLine:
    stock_id: int
    quantity: int


def parse_lines(post) -> list[SaleLine]:
    """Lit les lignes `stock_id[]` / `quantity[]` du formulaire caisse."""
    lines: dict[int, int] = {}
    for sid, qty in zip(post.getlist("stock_id"), post.getlist("quantity")):
        try:
            sid, qty = int(sid), int(qty)
        except (TypeError, ValueError):
            continue
        if qty > 0:
            lines[sid] = lines.get(sid, 0) + qty
    return [SaleLine(stock_id=s, quantity=q) for s, q in lines.items()]


@transaction.atomic
def create_counter_sale(
    *,
    pharmacy,
    user,
    lines: list[SaleLine],
    channel: str = CounterSale.Channel.COUNTER,
    payment_method: str = CounterSale.PaymentMethod.CASH,
    customer_name: str = "",
    customer_phone: str = "",
    discount: int = 0,
    insurance_provider=None,
    insurance_amount: int = 0,
    prescription_checked: bool = False,
    notes: str = "",
    insured_number: str = "",
    client_uuid=None,
    client_created_at=None,
    offline: bool = False,
) -> CounterSale:
    """Enregistre une vente.

    `offline=True` : vente déjà réalisée sur un appareil hors connexion. Les produits
    sont physiquement sortis, donc un stock devenu insuffisant entre-temps ne bloque
    pas la synchronisation : le stock est ramené à 0 et la vente est signalée.
    `client_uuid` rend l'envoi idempotent (une vente renvoyée deux fois n'est créée qu'une fois).
    """
    from core.pharmacy_notifications import check_stock_alert

    if client_uuid:
        existing = CounterSale.objects.filter(client_uuid=client_uuid).first()
        if existing:
            if existing.pharmacy_id != pharmacy.pk:
                raise CounterSaleError("Identifiant de vente déjà utilisé.")
            return existing

    if not lines:
        raise CounterSaleError("Ajoutez au moins un produit à la vente.")
    if channel not in CounterSale.Channel.values:
        channel = CounterSale.Channel.COUNTER
    if payment_method not in CounterSale.PaymentMethod.values:
        raise CounterSaleError("Mode de paiement invalide.")
    if payment_method == CounterSale.PaymentMethod.CREDIT and not (customer_name and customer_phone):
        raise CounterSaleError("Un crédit client exige le nom et le téléphone du client.")

    stocks = {
        s.pk: s
        for s in PharmacyStock.objects.select_for_update()
        .select_related("medicine")
        .filter(pharmacy=pharmacy, pk__in=[l.stock_id for l in lines])
    }
    items = []
    warnings = []
    for line in lines:
        stock = stocks.get(line.stock_id)
        if stock is None:
            raise CounterSaleError("Produit introuvable dans votre stock.")
        if line.quantity > stock.quantity:
            if not offline:
                raise CounterSaleError(
                    f"Stock insuffisant pour {stock.medicine} : {stock.quantity} disponible(s)."
                )
            warnings.append(f"{stock.medicine} : vendu {line.quantity}, stock {stock.quantity}")
        items.append((stock, line.quantity))

    needs_rx = any(s.medicine.requires_prescription for s, _ in items)
    if needs_rx and not prescription_checked:
        raise CounterSaleError(
            "Cette vente contient un médicament sur ordonnance : cochez « Ordonnance originale vérifiée »."
        )

    subtotal = sum(s.display_price * q for s, q in items)
    discount = max(0, min(int(discount or 0), subtotal))
    insurance_amount = max(0, int(insurance_amount or 0))
    insured_number = (insured_number or "").strip().upper()[:50]
    if payment_method == CounterSale.PaymentMethod.INSURANCE:
        if not insurance_provider:
            raise CounterSaleError("Choisissez l'assurance pour un tiers payant.")
        if not insured_number or not customer_name.strip():
            raise CounterSaleError(
                "Tiers payant : le nom du bénéficiaire et son n° d'assuré sont obligatoires "
                "(ils figurent sur le relevé envoyé à l'assureur)."
            )
        insurance_amount = min(insurance_amount, subtotal - discount)
    else:
        insurance_provider, insurance_amount, insured_number = None, 0, ""

    now = timezone.now()
    if client_created_at and not (now - timedelta(days=30) <= client_created_at <= now + timedelta(minutes=5)):
        client_created_at = None

    sale = CounterSale.objects.create(
        pharmacy=pharmacy,
        channel=channel,
        customer_name=customer_name.strip()[:150],
        customer_phone=customer_phone.strip()[:30],
        payment_method=payment_method,
        insurance_provider=insurance_provider,
        subtotal=subtotal,
        discount=discount,
        insurance_amount=insurance_amount,
        insured_number=insured_number,
        total=subtotal - discount - insurance_amount,
        prescription_checked=bool(prescription_checked and needs_rx),
        notes=notes.strip()[:255],
        created_by=user,
        client_uuid=client_uuid or None,
        client_created_at=client_created_at,
        recorded_offline=offline,
        sync_warning=("Stock insuffisant à la synchronisation — " + " ; ".join(warnings))[:255]
        if warnings
        else "",
    )
    for stock, qty in items:
        CounterSaleItem.objects.create(
            sale=sale,
            stock=stock,
            medicine=stock.medicine,
            medicine_name=str(stock.medicine),
            quantity=qty,
            unit_price=stock.display_price,
            requires_prescription=stock.medicine.requires_prescription,
        )
        previous = stock.quantity
        stock.quantity = max(0, previous - qty)
        stock.save(update_fields=["quantity", "updated_at"])
        StockMovement.objects.create(
            stock=stock,
            movement_type=StockMovement.MovementType.OUT,
            quantity_delta=stock.quantity - previous,
            quantity_after=stock.quantity,
            note=f"Vente hors app {sale.code}" + (" (hors connexion)" if offline else ""),
            created_by=user,
        )
        check_stock_alert(stock, previous_qty=previous)
    return sale


@transaction.atomic
def cancel_counter_sale(sale: CounterSale, *, user, reason: str) -> CounterSale:
    """Annule une vente et remet les produits en stock."""
    reason = (reason or "").strip()
    if sale.status == CounterSale.Status.CANCELLED:
        raise CounterSaleError("Cette vente est déjà annulée.")
    if not reason:
        raise CounterSaleError("Indiquez le motif de l'annulation.")
    for item in sale.items.select_related("stock"):
        if not item.stock_id:
            continue
        stock = PharmacyStock.objects.select_for_update().get(pk=item.stock_id)
        stock.quantity += item.quantity
        stock.save(update_fields=["quantity", "updated_at"])
        StockMovement.objects.create(
            stock=stock,
            movement_type=StockMovement.MovementType.IN,
            quantity_delta=item.quantity,
            quantity_after=stock.quantity,
            note=f"Annulation vente {sale.code}",
            created_by=user,
        )
    sale.status = CounterSale.Status.CANCELLED
    sale.cancellation_reason = reason[:255]
    sale.cancelled_at = timezone.now()
    sale.save(update_fields=["status", "cancellation_reason", "cancelled_at"])
    return sale


def counter_sales_summary(pharmacy, *, day=None) -> dict:
    """Totaux du jour (ventes validées) et répartition par moyen de paiement."""
    day = day or timezone.localdate()
    qs = CounterSale.objects.filter(
        pharmacy=pharmacy, status=CounterSale.Status.COMPLETED, created_at__date=day
    )
    agg = qs.aggregate(total=Sum("total"), insurance=Sum("insurance_amount"), n=Count("id"))
    labels = dict(CounterSale.PaymentMethod.choices)
    by_method = [
        {"method": r["payment_method"], "label": labels.get(r["payment_method"], r["payment_method"]),
         "total": r["s"] or 0, "count": r["n"]}
        for r in qs.values("payment_method").annotate(s=Sum("total"), n=Count("id")).order_by("-s")
    ]
    month_start = day.replace(day=1)
    month_total = (
        CounterSale.objects.filter(
            pharmacy=pharmacy, status=CounterSale.Status.COMPLETED, created_at__date__gte=month_start
        ).aggregate(s=Sum("total"))["s"]
        or 0
    )
    return {
        "day": day,
        "count": agg["n"] or 0,
        "cashed": agg["total"] or 0,
        "insurance": agg["insurance"] or 0,
        "by_method": by_method,
        "month_total": month_total,
    }
