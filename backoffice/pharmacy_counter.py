"""Portail pharmacie — ventes hors application (caisse comptoir / téléphone / WhatsApp)."""
from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.db import IntegrityError
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from backoffice.decorators import pharmacy_permission_required, role_required
from backoffice.views import _audit, _ctx, _pharmacy_for, pharmacy_roles
from catalog.models import PharmacyStock
from core.counter_sales import (
    CounterSaleError,
    SaleLine,
    cancel_counter_sale,
    counter_sales_summary,
    create_counter_sale,
    parse_lines,
)
from orders.models import CounterSale
from payments.models import InsuranceProvider


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_day(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


@role_required(*pharmacy_roles)
@pharmacy_permission_required("orders")
def pharmacy_counter(request):
    """Caisse : nouvelle vente hors app."""
    pharmacy = _pharmacy_for(request.user, request)
    if not pharmacy:
        return redirect("bo_pharmacy_dashboard")

    if request.method == "POST":
        provider = None
        if request.POST.get("insurance_provider"):
            provider = InsuranceProvider.objects.filter(
                pk=request.POST.get("insurance_provider"), is_active=True
            ).first()
        try:
            sale = create_counter_sale(
                pharmacy=pharmacy,
                user=request.user,
                lines=parse_lines(request.POST),
                channel=request.POST.get("channel", ""),
                payment_method=request.POST.get("payment_method", ""),
                customer_name=request.POST.get("customer_name", ""),
                customer_phone=request.POST.get("customer_phone", ""),
                discount=_int(request.POST.get("discount")),
                insurance_provider=provider,
                insurance_amount=_int(request.POST.get("insurance_amount")),
                prescription_checked=request.POST.get("prescription_checked") == "1",
                notes=request.POST.get("notes", ""),
                insured_number=request.POST.get("insured_number", ""),
            )
        except CounterSaleError as exc:
            messages.error(request, str(exc))
            return redirect("bo_pharmacy_counter")
        _audit(request, "counter_sale_create", "pharmacy", sale.code, True)
        messages.success(request, f"Vente {sale.code} enregistrée — {sale.total} F encaissés.")
        return redirect(f"{request.path}?done={sale.pk}")

    stocks = (
        PharmacyStock.objects.filter(pharmacy=pharmacy, quantity__gt=0)
        .select_related("medicine", "medicine__category")
        .order_by("medicine__name")
    )
    products = [
        {
            "id": s.pk,
            "name": str(s.medicine),
            "price": s.display_price,
            "qty": s.quantity,
            "rx": bool(s.medicine.requires_prescription),
            "category": getattr(s.medicine.category, "name", "") if s.medicine.category_id else "",
        }
        for s in stocks
    ]
    done_sale = None
    if request.GET.get("done"):
        done_sale = CounterSale.objects.filter(
            pk=_int(request.GET.get("done")), pharmacy=pharmacy
        ).first()
    recent = (
        CounterSale.objects.filter(pharmacy=pharmacy)
        .prefetch_related("items")
        .order_by("-created_at")[:6]
    )
    return render(
        request,
        "backoffice/pharmacy/counter.html",
        _ctx(
            request,
            "counter",
            products=products,
            summary=counter_sales_summary(pharmacy),
            recent_sales=recent,
            done_sale=done_sale,
            channels=CounterSale.Channel.choices,
            payment_methods=CounterSale.PaymentMethod.choices,
            insurance_providers=InsuranceProvider.objects.filter(is_active=True).order_by("name"),
        ),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required("orders")
def pharmacy_counter_history(request):
    """Historique des ventes hors app : filtres, annulation, export CSV."""
    pharmacy = _pharmacy_for(request.user, request)
    if not pharmacy:
        return redirect("bo_pharmacy_dashboard")

    if request.method == "POST" and request.POST.get("action") == "cancel":
        sale = get_object_or_404(CounterSale, pk=request.POST.get("sale_id"), pharmacy=pharmacy)
        try:
            cancel_counter_sale(sale, user=request.user, reason=request.POST.get("reason", ""))
        except CounterSaleError as exc:
            messages.error(request, str(exc))
        else:
            _audit(request, "counter_sale_cancel", "pharmacy", sale.code, True)
            messages.success(request, f"Vente {sale.code} annulée — produits remis en stock.")
        return redirect(request.get_full_path())

    today = timezone.localdate()
    date_from = _parse_day(request.GET.get("from")) or today.replace(day=1)
    date_to = _parse_day(request.GET.get("to")) or today
    qs = (
        CounterSale.objects.filter(
            pharmacy=pharmacy, created_at__date__gte=date_from, created_at__date__lte=date_to
        )
        .select_related("created_by", "insurance_provider")
        .prefetch_related("items")
    )
    channel = request.GET.get("channel", "")
    method = request.GET.get("method", "")
    if channel in CounterSale.Channel.values:
        qs = qs.filter(channel=channel)
    if method in CounterSale.PaymentMethod.values:
        qs = qs.filter(payment_method=method)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(code__icontains=q) | qs.filter(customer_name__icontains=q) | qs.filter(
            customer_phone__icontains=q
        )

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="ventes-hors-app-{date_from}-{date_to}.csv"'
        )
        response.write("﻿")
        writer = csv.writer(response, delimiter=";")
        writer.writerow(
            ["Code", "Date", "Canal", "Client", "Téléphone", "Produits", "Sous-total",
             "Remise", "Assurance", "Encaissé", "Paiement", "Statut", "Vendeur"]
        )
        for s in qs.order_by("created_at"):
            writer.writerow([
                s.code,
                timezone.localtime(s.created_at).strftime("%d/%m/%Y %H:%M"),
                s.get_channel_display(),
                s.customer_name,
                s.customer_phone,
                " | ".join(f"{i.quantity}x {i.medicine_name}" for i in s.items.all()),
                s.subtotal, s.discount, s.insurance_amount, s.total,
                s.get_payment_method_display(),
                s.get_status_display(),
                s.created_by.get_full_name() if s.created_by else "",
            ])
        return response

    agg = qs.filter(status=CounterSale.Status.COMPLETED).aggregate(
        count=Count("id"), cashed=Sum("total"), insurance=Sum("insurance_amount")
    )
    totals = {k: v or 0 for k, v in agg.items()}
    page = Paginator(qs.order_by("-created_at"), 25).get_page(request.GET.get("page"))
    return render(
        request,
        "backoffice/pharmacy/counter_history.html",
        _ctx(
            request,
            "counter",
            page=page,
            totals=totals,
            date_from=date_from,
            date_to=date_to,
            filter_channel=channel,
            filter_method=method,
            q=q,
            channels=CounterSale.Channel.choices,
            payment_methods=CounterSale.PaymentMethod.choices,
        ),
    )


@role_required(*pharmacy_roles)
@pharmacy_permission_required("orders")
def pharmacy_counter_receipt(request, pk):
    pharmacy = _pharmacy_for(request.user, request)
    sale = get_object_or_404(
        CounterSale.objects.select_related("pharmacy", "created_by", "insurance_provider"),
        pk=pk,
        pharmacy=pharmacy,
    )
    return render(request, "backoffice/pharmacy/counter_receipt.html", {"sale": sale})


@role_required(*pharmacy_roles)
@pharmacy_permission_required("orders")
@require_POST
def pharmacy_counter_api(request):
    """Enregistre une vente envoyée en JSON par la caisse (en ligne ou synchro hors connexion).

    Idempotent grâce à `uuid` : une vente renvoyée après une coupure n'est créée qu'une fois.
    """
    pharmacy = _pharmacy_for(request.user, request)
    if not pharmacy:
        return JsonResponse({"ok": False, "error": "Aucune pharmacie active."}, status=400)
    try:
        data = json.loads(request.body or b"{}")
        client_uuid = uuid.UUID(str(data.get("uuid")))
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Requête invalide."}, status=400)

    lines = [
        SaleLine(stock_id=_int(l.get("stock_id")), quantity=_int(l.get("quantity")))
        for l in data.get("lines") or []
        if _int(l.get("quantity")) > 0
    ]
    provider = None
    if data.get("insurance_provider"):
        provider = InsuranceProvider.objects.filter(pk=_int(data.get("insurance_provider"))).first()
    created_at = parse_datetime(str(data.get("created_at") or "")) if data.get("created_at") else None
    if created_at and timezone.is_naive(created_at):
        created_at = timezone.make_aware(created_at)
    kwargs = dict(
        pharmacy=pharmacy,
        user=request.user,
        lines=lines,
        channel=str(data.get("channel") or ""),
        payment_method=str(data.get("payment_method") or ""),
        customer_name=str(data.get("customer_name") or ""),
        customer_phone=str(data.get("customer_phone") or ""),
        discount=_int(data.get("discount")),
        insurance_provider=provider,
        insurance_amount=_int(data.get("insurance_amount")),
        insured_number=str(data.get("insured_number") or ""),
        prescription_checked=bool(data.get("prescription_checked")),
        notes=str(data.get("notes") or ""),
        client_uuid=client_uuid,
        client_created_at=created_at,
        offline=bool(data.get("offline")),
    )
    try:
        sale = create_counter_sale(**kwargs)
    except CounterSaleError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except IntegrityError:
        # Deux envois simultanés du même uuid : on renvoie la vente déjà créée.
        sale = CounterSale.objects.filter(client_uuid=client_uuid, pharmacy=pharmacy).first()
        if not sale:
            return JsonResponse({"ok": False, "error": "Conflit d'enregistrement."}, status=409)
    _audit(request, "counter_sale_create", "pharmacy", sale.code, True)
    return JsonResponse(
        {
            "ok": True,
            "sale_id": sale.pk,
            "code": sale.code,
            "total": sale.total,
            "warning": sale.sync_warning,
            "receipt_url": reverse("bo_pharmacy_counter_receipt", args=[sale.pk]),
            "done_url": reverse("bo_pharmacy_counter") + f"?done={sale.pk}",
        }
    )


@never_cache
def pharmacy_counter_service_worker(request):
    """Service worker limité à la caisse (portée /espace/pharmacie/caisse/)."""
    from django.conf import settings
    from django.contrib.staticfiles import finders

    path = finders.find("js/counter-sw.js") or (settings.BASE_DIR / "static" / "js" / "counter-sw.js")
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    response = HttpResponse(body, content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = reverse("bo_pharmacy_counter")
    return response
