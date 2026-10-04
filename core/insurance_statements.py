"""Relevé mensuel de tiers payant (pharmacie → assureur).

Une pharmacie avance la part assurance de ses clients ; elle se fait rembourser
en envoyant chaque mois à l'assureur un relevé de ces parts. Le relevé réunit :
- les ventes hors app payées en tiers payant (caisse) ;
- les commandes Gab'Pharma avec prise en charge assurance ;
- les dossiers de prise en charge (PC) facturés.
"""
from __future__ import annotations

import io
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from orders.models import CounterSale, Order
from payments.models import CoverageInvoice, InsuranceProvider, InsuranceStatement

EXCLUDED_ORDER_STATUSES = {Order.Status.CART, Order.Status.CANCELLED, Order.Status.REFUNDED}

SOURCE_COUNTER = "Vente comptoir"
SOURCE_ORDER = "Commande Gab'Pharma"
SOURCE_COVERAGE = "Prise en charge (PC)"


@dataclass
class StatementLine:
    date: datetime
    source: str
    reference: str
    beneficiary: str
    insured_number: str
    items: str
    gross: int
    insurance_amount: int


def month_start(value: date) -> date:
    return value.replace(day=1)


def month_bounds(period: date):
    """Bornes [début, fin[ du mois en datetime conscient du fuseau."""
    start = month_start(period)
    nxt = (start + timedelta(days=32)).replace(day=1)
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(start, time.min), tz),
        timezone.make_aware(datetime.combine(nxt, time.min), tz),
    )


def _counter_qs(pharmacy, provider, start, end):
    return (
        CounterSale.objects.filter(
            pharmacy=pharmacy,
            insurance_provider=provider,
            status=CounterSale.Status.COMPLETED,
            insurance_amount__gt=0,
        )
        .annotate(sold=Coalesce("client_created_at", "created_at"))
        .filter(sold__gte=start, sold__lt=end)
    )


def _order_qs(pharmacy, provider, start, end):
    return Order.objects.filter(
        pharmacy=pharmacy,
        insurance_provider=provider,
        insurance_coverage__gt=0,
        created_at__gte=start,
        created_at__lt=end,
    ).exclude(status__in=EXCLUDED_ORDER_STATUSES)


def _coverage_qs(pharmacy, provider, start, end):
    return CoverageInvoice.objects.filter(
        coverage_request__pharmacy=pharmacy,
        coverage_request__provider=provider,
        insurance_share__gt=0,
        created_at__gte=start,
        created_at__lt=end,
    )


def statement_lines(pharmacy, provider, period: date) -> list[StatementLine]:
    start, end = month_bounds(period)
    lines: list[StatementLine] = []

    for s in _counter_qs(pharmacy, provider, start, end).prefetch_related("items"):
        lines.append(StatementLine(
            date=s.sold,
            source=SOURCE_COUNTER,
            reference=s.code,
            beneficiary=s.customer_name,
            insured_number=s.insured_number,
            items=", ".join(f"{i.quantity}× {i.medicine_name}" for i in s.items.all()),
            gross=s.subtotal - s.discount,
            insurance_amount=s.insurance_amount,
        ))

    orders = _order_qs(pharmacy, provider, start, end).select_related("client").prefetch_related("items")
    for o in orders:
        profile = getattr(o.client, "client_profile", None)
        lines.append(StatementLine(
            date=o.created_at,
            source=SOURCE_ORDER,
            reference=o.code,
            beneficiary=o.client.get_full_name() or o.client.username,
            insured_number=getattr(profile, "insurance_number", "") or "",
            items=", ".join(f"{i.quantity}× {i.medicine_name}" for i in o.items.all()),
            gross=o.subtotal - o.discount,
            insurance_amount=o.insurance_coverage,
        ))

    invoices = _coverage_qs(pharmacy, provider, start, end).select_related(
        "coverage_request", "coverage_request__client"
    ).prefetch_related("lines")
    for inv in invoices:
        req = inv.coverage_request
        lines.append(StatementLine(
            date=inv.created_at,
            source=SOURCE_COVERAGE,
            reference=f"{req.reference} / {inv.reference}",
            beneficiary=req.client.get_full_name() or req.client.username,
            insured_number=req.insured_number,
            items=", ".join(f"{l.quantity}× {l.name}" for l in inv.lines.all() if l.is_covered),
            gross=inv.total,
            insurance_amount=inv.insurance_share,
        ))

    lines.sort(key=lambda l: l.date)
    return lines


def totals_by_source(lines: list[StatementLine]) -> dict:
    out = {"counter": 0, "order": 0, "coverage": 0}
    for l in lines:
        key = {SOURCE_COUNTER: "counter", SOURCE_ORDER: "order", SOURCE_COVERAGE: "coverage"}[l.source]
        out[key] += l.insurance_amount
    out["total"] = out["counter"] + out["order"] + out["coverage"]
    out["count"] = len(lines)
    return out


def month_overview(pharmacy, period: date) -> list[dict]:
    """Une ligne par assureur ayant du tiers payant sur le mois (ou un relevé existant)."""
    start, end = month_bounds(period)
    provider_ids = set()
    provider_ids.update(
        CounterSale.objects.filter(pharmacy=pharmacy, insurance_amount__gt=0, status=CounterSale.Status.COMPLETED)
        .annotate(sold=Coalesce("client_created_at", "created_at"))
        .filter(sold__gte=start, sold__lt=end)
        .values_list("insurance_provider_id", flat=True)
    )
    provider_ids.update(
        Order.objects.filter(pharmacy=pharmacy, insurance_coverage__gt=0, created_at__gte=start, created_at__lt=end)
        .exclude(status__in=EXCLUDED_ORDER_STATUSES)
        .values_list("insurance_provider_id", flat=True)
    )
    provider_ids.update(
        CoverageInvoice.objects.filter(
            coverage_request__pharmacy=pharmacy, insurance_share__gt=0, created_at__gte=start, created_at__lt=end
        ).values_list("coverage_request__provider_id", flat=True)
    )
    statements = {
        s.provider_id: s
        for s in InsuranceStatement.objects.filter(pharmacy=pharmacy, period=month_start(period))
    }
    provider_ids.update(statements)
    provider_ids.discard(None)

    rows = []
    for provider in InsuranceProvider.objects.filter(pk__in=provider_ids).order_by("name"):
        live = totals_by_source(statement_lines(pharmacy, provider, period))
        st = statements.get(provider.pk)
        rows.append({
            "provider": provider,
            "live": live,
            "statement": st,
            "out_of_date": bool(st and st.total != live["total"]),
        })
    return rows


def _reference(pharmacy, provider, period: date) -> str:
    return f"RTP-{period:%Y%m}-{provider.code[:6].upper()}-{secrets.token_hex(2).upper()}"


@transaction.atomic
def generate_statement(pharmacy, provider, period: date, *, user) -> InsuranceStatement:
    """Crée ou recalcule le relevé (tant qu'il n'a pas été envoyé)."""
    period = month_start(period)
    totals = totals_by_source(statement_lines(pharmacy, provider, period))
    if not totals["count"]:
        raise ValueError("Aucune part assurance sur ce mois pour cet assureur.")
    st = (
        InsuranceStatement.objects.select_for_update()
        .filter(pharmacy=pharmacy, provider=provider, period=period)
        .first()
    )
    if st and st.status != InsuranceStatement.Status.DRAFT:
        raise ValueError("Ce relevé a déjà été envoyé : il ne peut plus être recalculé.")
    if not st:
        st = InsuranceStatement(
            pharmacy=pharmacy, provider=provider, period=period,
            reference=_reference(pharmacy, provider, period), created_by=user,
        )
    st.counter_total = totals["counter"]
    st.order_total = totals["order"]
    st.coverage_total = totals["coverage"]
    st.total = totals["total"]
    st.line_count = totals["count"]
    st.save()
    return st


def mark_sent(st: InsuranceStatement) -> InsuranceStatement:
    if st.status != InsuranceStatement.Status.DRAFT:
        raise ValueError("Ce relevé a déjà été envoyé.")
    st.status = InsuranceStatement.Status.SENT
    st.sent_at = timezone.now()
    st.save(update_fields=["status", "sent_at", "updated_at"])
    return st


def record_payment(st: InsuranceStatement, amount: int) -> InsuranceStatement:
    """Enregistre un règlement de l'assureur (cumulatif)."""
    if st.status == InsuranceStatement.Status.DRAFT:
        raise ValueError("Marquez d'abord le relevé comme envoyé.")
    amount = int(amount or 0)
    if amount <= 0:
        raise ValueError("Montant de règlement invalide.")
    st.paid_amount = min(st.total, st.paid_amount + amount)
    st.status = (
        InsuranceStatement.Status.PAID if st.paid_amount >= st.total else InsuranceStatement.Status.PARTIAL
    )
    st.paid_at = timezone.now()
    st.save(update_fields=["paid_amount", "status", "paid_at", "updated_at"])
    return st


def outstanding_summary(pharmacy) -> dict:
    """Créances assureurs : relevés envoyés non soldés."""
    qs = InsuranceStatement.objects.filter(
        pharmacy=pharmacy,
        status__in=[InsuranceStatement.Status.SENT, InsuranceStatement.Status.PARTIAL],
    )
    agg = qs.aggregate(total=Sum("total"), paid=Sum("paid_amount"))
    return {"count": qs.count(), "due": (agg["total"] or 0) - (agg["paid"] or 0)}


def build_statement_pdf(st: InsuranceStatement, lines: list[StatementLine]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    def fmt(n):
        return f"{int(n):,}".replace(",", " ") + " F"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=1.4 * cm, rightMargin=1.4 * cm,
        topMargin=1.2 * cm, bottomMargin=1.2 * cm,
        title=f"Relevé de tiers payant {st.reference}",
    )
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7.5, leading=9)
    green = colors.HexColor("#015533")
    ph = st.pharmacy
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
              "août", "septembre", "octobre", "novembre", "décembre"]
    story = [
        Paragraph(f"<b>Relevé de tiers payant</b> — {months[st.period.month - 1]} {st.period.year}", styles["Title"]),
        Paragraph(
            f"<b>Pharmacie :</b> {ph.name} · {ph.address} · {ph.phone}<br/>"
            f"<b>Organisme :</b> {st.provider.name} ({st.provider.code})<br/>"
            f"<b>Référence :</b> {st.reference} · émis le {timezone.localtime(st.updated_at):%d/%m/%Y}",
            styles["Normal"],
        ),
        Spacer(1, 0.4 * cm),
    ]
    data = [["Date", "Origine", "Référence", "Bénéficiaire", "N° assuré", "Produits", "Montant", "Part assurance"]]
    for l in lines:
        data.append([
            timezone.localtime(l.date).strftime("%d/%m/%Y"),
            l.source, l.reference, Paragraph(l.beneficiary or "—", small), l.insured_number or "—",
            Paragraph(l.items or "—", small), fmt(l.gross), fmt(l.insurance_amount),
        ])
    data.append(["", "", "", "", "", "Total à rembourser", "", fmt(sum(l.insurance_amount for l in lines))])
    table = Table(
        data, repeatRows=1,
        colWidths=[2 * cm, 3.3 * cm, 3.4 * cm, 4 * cm, 2.6 * cm, 7.4 * cm, 2.3 * cm, 2.6 * cm],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), green),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (6, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F3F8F5")]),
        ("LINEABOVE", (0, -1), (-1, -1), 1, green),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 9),
    ]))
    story += [
        table,
        Spacer(1, 0.6 * cm),
        Paragraph(
            "Document établi par la pharmacie via Gab'Pharma. Les ordonnances originales sont "
            "conservées à l'officine et tenues à la disposition de l'organisme.",
            small,
        ),
        Spacer(1, 1.2 * cm),
        Paragraph("Cachet et signature du pharmacien : ______________________________", styles["Normal"]),
    ]
    doc.build(story)
    return buf.getvalue()
