"""Workflow inventaire et mouvements de stock (CDC §4.5)."""
from __future__ import annotations

import csv
import io

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from catalog.models import (
    PharmacyStock,
    StockInventoryLine,
    StockInventorySession,
    StockMovement,
)


class InventoryError(ValueError):
    pass


def log_stock_movement(stock, movement_type, delta, user, note=""):
    if delta == 0:
        return None
    return StockMovement.objects.create(
        stock=stock,
        movement_type=movement_type,
        quantity_delta=delta,
        quantity_after=stock.quantity,
        note=note,
        created_by=user,
    )


def active_inventory_session(pharmacy):
    if not pharmacy:
        return None
    return (
        StockInventorySession.objects.filter(
            pharmacy=pharmacy,
            status=StockInventorySession.Status.IN_PROGRESS,
        )
        .select_related("started_by")
        .first()
    )


def _refresh_session_counters(session):
    counted_qs = session.lines.filter(counted_quantity__isnull=False)
    session.lines_counted = counted_qs.count()
    session.variance_lines = counted_qs.exclude(counted_quantity=F("expected_quantity")).count()
    session.save(update_fields=["lines_counted", "variance_lines"])


@transaction.atomic
def start_inventory_session(pharmacy, user, note=""):
    if active_inventory_session(pharmacy):
        raise InventoryError(
            "Un inventaire est déjà en cours. Terminez-le ou annulez-le avant d'en démarrer un nouveau."
        )
    session = StockInventorySession.objects.create(
        pharmacy=pharmacy,
        started_by=user,
        note=(note or "").strip()[:255],
        lines_total=0,
    )
    stocks = PharmacyStock.objects.filter(pharmacy=pharmacy).select_related("medicine")
    lines = [
        StockInventoryLine(
            session=session,
            stock=stock,
            expected_quantity=stock.quantity,
        )
        for stock in stocks
    ]
    StockInventoryLine.objects.bulk_create(lines)
    session.lines_total = len(lines)
    session.save(update_fields=["lines_total"])
    return session


@transaction.atomic
def save_inventory_counts(session, counts: dict):
    """counts: {stock_id: counted_quantity}"""
    if session.status != StockInventorySession.Status.IN_PROGRESS:
        raise InventoryError("Cette session d'inventaire n'est plus modifiable.")
    updated = 0
    for stock_id, raw in counts.items():
        try:
            counted = int(raw)
        except (TypeError, ValueError):
            continue
        if counted < 0:
            continue
        line = StockInventoryLine.objects.filter(session=session, stock_id=stock_id).first()
        if not line:
            continue
        line.counted_quantity = counted
        line.save(update_fields=["counted_quantity"])
        updated += 1
    _refresh_session_counters(session)
    return updated


@transaction.atomic
def complete_inventory_session(session, user):
    if session.status != StockInventorySession.Status.IN_PROGRESS:
        raise InventoryError("Inventaire déjà clôturé.")
    applied = 0
    for line in session.lines.select_related("stock"):
        if line.counted_quantity is None:
            continue
        stock = line.stock
        delta = line.counted_quantity - line.expected_quantity
        if delta == 0:
            continue
        stock.quantity = line.counted_quantity
        stock.save(update_fields=["quantity", "updated_at"])
        log_stock_movement(
            stock,
            StockMovement.MovementType.INVENTORY,
            delta,
            user,
            f"Inventaire #{session.pk}" + (f" — {line.note}" if line.note else ""),
        )
        applied += 1

    session.status = StockInventorySession.Status.COMPLETED
    session.completed_by = user
    session.completed_at = timezone.now()
    _refresh_session_counters(session)
    session.save(
        update_fields=[
            "status",
            "completed_by",
            "completed_at",
            "lines_counted",
            "variance_lines",
        ]
    )
    return applied


@transaction.atomic
def cancel_inventory_session(session, user=None):
    if session.status != StockInventorySession.Status.IN_PROGRESS:
        raise InventoryError("Impossible d'annuler cet inventaire.")
    session.status = StockInventorySession.Status.CANCELLED
    session.completed_by = user
    session.completed_at = timezone.now()
    session.save(update_fields=["status", "completed_by", "completed_at"])


@transaction.atomic
def quick_stock_movement(stock, movement_type, quantity, user, note=""):
    """Entrée ou sortie rapide (quantité toujours positive)."""
    quantity = int(quantity or 0)
    if quantity <= 0:
        raise InventoryError("Indiquez une quantité positive.")
    if movement_type == StockMovement.MovementType.OUT:
        quantity = min(quantity, stock.quantity)
        delta = -quantity
        default_note = "Sortie rapide"
    elif movement_type == StockMovement.MovementType.IN:
        delta = quantity
        default_note = "Entrée rapide"
    else:
        raise InventoryError("Type de mouvement non supporté.")
    stock.quantity = max(0, stock.quantity + delta)
    stock.save(update_fields=["quantity", "updated_at"])
    log_stock_movement(stock, movement_type, delta, user, note or default_note)
    return stock


@transaction.atomic
def import_stock_csv(pharmacy, user, file_obj):
    """Import CSV aligné sur l'export Gab'Pharma (colonne Stock / Médicament)."""
    if not pharmacy:
        raise InventoryError("Pharmacie introuvable.")
    raw = file_obj.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        raise InventoryError("Fichier CSV vide ou invalide.")

    updated = skipped = 0
    for row in reader:
        name = (row.get("Médicament") or row.get("medicament") or "").strip()
        if not name:
            skipped += 1
            continue
        try:
            qty = int(row.get("Stock") or row.get("stock") or row.get("Quantité") or 0)
        except ValueError:
            skipped += 1
            continue
        stock = PharmacyStock.objects.filter(
            pharmacy=pharmacy,
            medicine__name__icontains=name.split(" ")[0][:30],
        ).first()
        if not stock:
            skipped += 1
            continue
        old = stock.quantity
        if old == qty:
            skipped += 1
            continue
        stock.quantity = max(0, qty)
        stock.save(update_fields=["quantity", "updated_at"])
        log_stock_movement(
            stock,
            StockMovement.MovementType.ADJUST,
            qty - old,
            user,
            "Import CSV inventaire",
        )
        updated += 1
    return {"updated": updated, "skipped": skipped}
