"""Portail pharmacie — publicités boost (popup + priorité catalogue)."""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from backoffice.decorators import pharmacy_permission_required, role_required
from backoffice.views import _ctx, _pharmacy_for, _set_image, pharmacy_roles
from core.models import PharmacyBoostAd
from core.pharmacy_boost import (
    BOOST_PLANS,
    active_boost_purchase,
    pharmacy_can_boost,
    purchase_boost,
)
from core.pharmacy_access import PERM_SETTINGS


@role_required(*pharmacy_roles)
@pharmacy_permission_required(PERM_SETTINGS)
def pharmacy_boost_ads(request):
    pharmacy = _pharmacy_for(request.user, request)
    if not pharmacy:
        messages.error(request, "Aucune pharmacie associée.")
        return redirect("bo_pharmacy_dashboard")

    purchase = active_boost_purchase(pharmacy)
    can_boost = pharmacy_can_boost(pharmacy)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "buy_boost":
            plan = request.POST.get("plan")
            try:
                purchase_boost(pharmacy, plan)
                messages.success(request, "Forfait boost activé. Créez votre publicité.")
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect("bo_pharmacy_boost")

        if action == "create":
            if not can_boost and not active_boost_purchase(pharmacy):
                messages.error(
                    request,
                    "Souscrivez à un forfait Boost (ou un abonnement Pro/Entreprise) pour publier.",
                )
                return redirect("bo_pharmacy_boost")
            title = (request.POST.get("title") or "").strip()
            image = request.FILES.get("image")
            if not title:
                messages.error(request, "Le titre est obligatoire.")
            elif not image:
                messages.error(request, "Ajoutez une photo / visuel pour la publicité.")
            else:
                live = active_boost_purchase(pharmacy)
                ad = PharmacyBoostAd(
                    pharmacy=pharmacy,
                    purchase=live,
                    title=title[:150],
                    description=(request.POST.get("description") or "").strip()[:500],
                    cta_label=(request.POST.get("cta_label") or "Voir l'offre").strip()[:60],
                    cta_url=(request.POST.get("cta_url") or "").strip()[:500],
                    show_popup=bool(request.POST.get("show_popup")),
                    boost_catalog=bool(request.POST.get("boost_catalog")),
                    priority=int(request.POST.get("priority") or 10),
                    status=PharmacyBoostAd.Status.ACTIVE
                    if request.POST.get("publish")
                    else PharmacyBoostAd.Status.DRAFT,
                    starts_at=timezone.now(),
                    ends_at=live.ends_at if live else None,
                    created_by=request.user,
                )
                ad.image = image
                ad.save()
                messages.success(request, "Publicité enregistrée.")
            return redirect("bo_pharmacy_boost")

        if action == "update":
            ad = get_object_or_404(PharmacyBoostAd, pk=request.POST.get("ad_id"), pharmacy=pharmacy)
            ad.title = (request.POST.get("title") or ad.title).strip()[:150]
            ad.description = (request.POST.get("description") or "").strip()[:500]
            ad.cta_label = (request.POST.get("cta_label") or ad.cta_label).strip()[:60]
            ad.cta_url = (request.POST.get("cta_url") or "").strip()[:500]
            ad.show_popup = bool(request.POST.get("show_popup"))
            ad.boost_catalog = bool(request.POST.get("boost_catalog"))
            try:
                ad.priority = int(request.POST.get("priority") or ad.priority)
            except (TypeError, ValueError):
                pass
            st = request.POST.get("status")
            if st in dict(PharmacyBoostAd.Status.choices):
                ad.status = st
            _set_image(ad, "image", request.FILES)
            ad.save()
            messages.success(request, "Publicité mise à jour.")
            return redirect("bo_pharmacy_boost")

        if action == "delete":
            ad = get_object_or_404(PharmacyBoostAd, pk=request.POST.get("ad_id"), pharmacy=pharmacy)
            ad.delete()
            messages.success(request, "Publicité supprimée.")
            return redirect("bo_pharmacy_boost")

    ads = pharmacy.boost_ads.select_related("purchase").order_by("-created_at")
    edit_obj = ads.filter(pk=request.GET.get("edit")).first() if request.GET.get("edit") else None
    return render(
        request,
        "backoffice/pharmacy/boost_ads.html",
        _ctx(
            request,
            "boost",
            pharmacy=pharmacy,
            ads=ads[:40],
            edit_obj=edit_obj,
            show_create=request.GET.get("new") == "1",
            boost_plans=BOOST_PLANS,
            active_purchase=purchase,
            can_boost=can_boost,
        ),
    )
