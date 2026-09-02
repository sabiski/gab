"""Portail compagnie d'assurance — Gab'Pharma."""
from __future__ import annotations

from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClientProfile, PartnerProfile, User
from backoffice.decorators import insurer_access_roles, portal_permission_required, role_required
from backoffice.utils import paginate
from backoffice.views import _audit, _ctx
from core.insurer_claims import (
    build_claim_detail,
    claim_display_reference,
    claim_has_prescription,
    claim_insured_number,
    claim_prescription_url,
    claim_status_ui,
    claims_apply_filters,
    claims_apply_search,
    claims_base_queryset,
    claims_stats,
    get_claim_for_profile,
    partner_pharmacies_for_claims,
)
from core.insurer_profile import resolve_insurer_profile
from core.insurer_portal_stats import charts_json, insurer_dashboard_widgets
from notifications.models import Notification
from notifications.services import notify_user
from payments.models import InsuranceClaim


def _insurer_profile(user, request=None):
    return resolve_insurer_profile(user, request)


def _render(request, section, template, profile, **extra):
    from backoffice.decorators import admin_roles
    from core.platform_access import partner_portal_permissions
    from core.partner_subscription import partner_has_platform_access, partner_subscription_summary

    if profile.account_incomplete:
        ctx = _ctx(
            request,
            section,
            partner_profile=profile,
            portal_permissions=partner_portal_permissions(request.user),
            insurer_notif_count=0,
            **extra,
        )
        return render(request, "backoffice/insurer/account_setup_required.html", ctx)

    if not profile.preview and request.user.role not in admin_roles:
        partner = profile._partner
        if partner and not partner_has_platform_access(partner):
            sub_info = partner_subscription_summary(partner)
            ctx = _ctx(
                request,
                section,
                partner_profile=profile,
                subscription_blocked=True,
                platform_subscription=sub_info,
                portal_permissions=partner_portal_permissions(request.user),
                insurer_notif_count=request.user.notifications.filter(is_read=False).count(),
                **extra,
            )
            return render(request, "backoffice/insurer/subscription_required.html", ctx)

    unread = request.user.notifications.filter(is_read=False).count()
    ctx = _ctx(
        request,
        section,
        partner_profile=profile,
        portal_permissions=partner_portal_permissions(request.user),
        insurer_notif_count=unread,
        **extra,
    )
    return render(request, template, ctx)


def _claims_qs(profile, status=None):
    qs = claims_base_queryset(profile)
    if status:
        qs = qs.filter(status=status)
    return qs


def _validate_review_attachment(uploaded_file) -> str:
    if not uploaded_file:
        return ""
    if uploaded_file.size > 5 * 1024 * 1024:
        return "Le fichier ne doit pas dépasser 5 Mo."
    content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    allowed = {"application/pdf", "image/jpeg", "image/png", "image/jpg", "image/pjpeg"}
    if content_type and content_type not in allowed:
        return "Formats acceptés : PDF, JPG ou PNG."
    return ""


def _handle_claim_action(request, profile, claim_id, new_status, review_notes="", attachment=None):
    from core.insurance import finalize_insurance_approval, finalize_insurance_rejection

    claim = get_object_or_404(_claims_qs(profile), pk=claim_id)
    allowed = dict(InsuranceClaim.Status.choices)
    if new_status not in allowed:
        messages.error(request, "Statut invalide.")
        return claim
    if attachment:
        err = _validate_review_attachment(attachment)
        if err:
            messages.error(request, err)
            return claim
    was_pending = claim.status == InsuranceClaim.Status.PENDING
    old_status = claim.status
    claim.status = new_status
    update_fields = ["status", "review_notes", "updated_at"]
    if review_notes is not None:
        claim.review_notes = review_notes.strip()
    if attachment:
        claim.review_attachment = attachment
        update_fields.append("review_attachment")
    claim.save(update_fields=update_fields)

    if was_pending and new_status == InsuranceClaim.Status.APPROVED:
        finalize_insurance_approval(claim)
    elif was_pending and new_status == InsuranceClaim.Status.REJECTED:
        finalize_insurance_rejection(claim)
    elif not was_pending:
        label = claim.get_status_display().lower()
        notify_user(
            claim.client,
            f"Demande de prise en charge {label}",
            (
                f"Votre demande {claim.order.code if claim.order_id else claim.pk} "
                f"auprès de {claim.provider.name} ({claim.amount} F) est {label}."
            ),
            notification_type=Notification.Type.INFO,
            data={"claim_id": claim.id},
            transactional=True,
        )
    if old_status != new_status:
        from core.insurer_fraud import sync_fraud_alerts_for_provider
        from core.insurer_notifications import on_insurance_claim_status_changed

        on_insurance_claim_status_changed(claim, old_status)
        sync_fraud_alerts_for_provider(claim.provider)
    _audit(request, "insurer_claim_status", "claims", f"{claim.pk} → {new_status}", True)
    return claim


@role_required(*insurer_access_roles)
@portal_permission_required("dashboard")
def insurer_dashboard(request):
    profile = _insurer_profile(request.user, request)
    widgets = insurer_dashboard_widgets(profile)
    return _render(
        request,
        "dashboard",
        "backoffice/insurer/dashboard.html",
        profile,
        widgets=widgets,
        charts_json=charts_json(widgets),
    )


def _claims_view(request, section, status=None, statuses=None, title="Demandes de prise en charge"):
    profile = _insurer_profile(request.user, request)

    if request.method == "POST":
        action = request.POST.get("action")
        claim_id = request.POST.get("claim_id")
        if action == "approve_claim":
            _handle_claim_action(request, profile, claim_id, InsuranceClaim.Status.APPROVED)
            messages.success(request, "Demande validée.")
        elif action == "reject_claim":
            notes = request.POST.get("review_notes", "").strip()
            if not notes:
                messages.error(request, "Indiquez le motif du refus.")
            else:
                _handle_claim_action(
                    request,
                    profile,
                    claim_id,
                    InsuranceClaim.Status.REJECTED,
                    review_notes=notes,
                )
                messages.success(request, "Demande refusée.")
        elif action == "mark_paid":
            _handle_claim_action(request, profile, claim_id, InsuranceClaim.Status.PAID)
            messages.success(request, "Demande marquée comme payée.")
        return redirect(f"{request.path}?{request.GET.urlencode()}" if request.GET else request.path)

    qs = _claims_qs(profile)
    if statuses:
        qs = qs.filter(status__in=statuses)
    elif status:
        qs = qs.filter(status=status)
    q = (request.GET.get("q") or "").strip()
    pharmacy_id = request.GET.get("pharmacy") or None
    overdue_only = request.GET.get("overdue") == "1"
    show_filters = request.GET.get("filters") == "1" or pharmacy_id or overdue_only

    qs = claims_apply_search(qs, q)
    qs = claims_apply_filters(qs, pharmacy_id=pharmacy_id, overdue_only=overdue_only)

    stats = claims_stats(profile)
    per_page = int(request.GET.get("per_page") or 10)
    if per_page not in {10, 25, 50}:
        per_page = 10

    page = paginate(request, qs, per_page)
    for claim in page.object_list:
        claim.ui_status = claim_status_ui(claim)
        claim.display_reference = claim_display_reference(claim)
        claim.insured_number = claim_insured_number(claim)
        claim.has_prescription = claim_has_prescription(claim)
        claim.prescription_url = claim_prescription_url(claim)

    active_filters = int(bool(pharmacy_id)) + int(bool(overdue_only))

    from pharmacies.models import Pharmacy

    filter_pharmacies = list(partner_pharmacies_for_claims(profile))
    filter_pharmacy = None
    if pharmacy_id:
        try:
            pid = int(pharmacy_id)
            filter_pharmacy = Pharmacy.objects.filter(pk=pid).first()
            if filter_pharmacy and not any(p.pk == pid for p in filter_pharmacies):
                filter_pharmacies = sorted([*filter_pharmacies, filter_pharmacy], key=lambda p: p.name)
        except (TypeError, ValueError):
            pass

    reset_urls = {
        "claims": "bo_insurer_claims",
        "claims_pending": "bo_insurer_claims_pending",
        "claims_approved": "bo_insurer_claims_approved",
        "claims_rejected": "bo_insurer_claims_rejected",
    }
    claims_reset_url = reverse(reset_urls.get(section, "bo_insurer_claims"))

    return _render(
        request,
        section,
        "backoffice/insurer/claims.html",
        profile,
        page_obj=page,
        total_count=qs.count(),
        claim_stats=stats,
        q=q,
        page_title=title,
        filter_status=status or "",
        pharmacy_id=pharmacy_id or "",
        overdue_only=overdue_only,
        show_filters=show_filters,
        active_filters=active_filters,
        filter_pharmacies=filter_pharmacies,
        filter_pharmacy=filter_pharmacy,
        per_page=per_page,
        claims_reset_url=claims_reset_url,
    )


@role_required(*insurer_access_roles)
@portal_permission_required("claims")
def insurer_claims(request):
    return _claims_view(request, "claims")


@role_required(*insurer_access_roles)
@portal_permission_required("claims")
def insurer_claims_pending(request):
    return _claims_view(
        request,
        "claims_pending",
        status=InsuranceClaim.Status.PENDING,
        title="Demandes en attente",
    )


@role_required(*insurer_access_roles)
@portal_permission_required("claims")
def insurer_claims_approved(request):
    return _claims_view(
        request,
        "claims_approved",
        statuses=[InsuranceClaim.Status.APPROVED, InsuranceClaim.Status.PAID],
        title="Demandes validées",
    )


@role_required(*insurer_access_roles)
@portal_permission_required("claims")
def insurer_claims_rejected(request):
    return _claims_view(
        request,
        "claims_rejected",
        status=InsuranceClaim.Status.REJECTED,
        title="Demandes refusées",
    )


@role_required(*insurer_access_roles)
@portal_permission_required("claims")
def insurer_claim_detail(request, claim_id):
    profile = _insurer_profile(request.user, request)
    claim = get_claim_for_profile(profile, claim_id)

    if request.method == "POST":
        decision = request.POST.get("decision")
        notes = request.POST.get("review_notes", "").strip()
        attachment = request.FILES.get("review_attachment")
        try:
            approved_amount = int(request.POST.get("approved_amount") or 0)
        except (TypeError, ValueError):
            approved_amount = 0

        if claim.status != InsuranceClaim.Status.PENDING:
            messages.warning(request, "Cette demande a déjà été traitée.")
        elif decision == "validate":
            if approved_amount <= 0 or approved_amount > claim.amount:
                messages.error(request, "Indiquez un montant valide pour la prise en charge.")
            elif approved_amount < claim.amount:
                messages.error(
                    request,
                    "Pour une prise en charge partielle, sélectionnez « Valider partiellement ».",
                )
            else:
                claim = _handle_claim_action(
                    request,
                    profile,
                    claim.pk,
                    InsuranceClaim.Status.APPROVED,
                    review_notes=notes,
                    attachment=attachment,
                )
                if claim.status == InsuranceClaim.Status.APPROVED:
                    messages.success(request, "Prise en charge validée.")
        elif decision == "partial":
            if not notes:
                messages.error(request, "Le commentaire est obligatoire pour une prise en charge partielle.")
            elif approved_amount <= 0 or approved_amount >= claim.amount:
                messages.error(request, "Indiquez un montant partiel inférieur au montant demandé.")
            else:
                if attachment:
                    err = _validate_review_attachment(attachment)
                    if err:
                        messages.error(request, err)
                        return redirect("bo_insurer_claim_detail", claim_id=claim.pk)
                original_amount = claim.amount
                claim.amount = approved_amount
                claim.review_notes = notes
                claim.status = InsuranceClaim.Status.APPROVED
                update_fields = ["amount", "review_notes", "status", "updated_at"]
                if attachment:
                    claim.review_attachment = attachment
                    update_fields.append("review_attachment")
                claim.save(update_fields=update_fields)
                from core.insurance import finalize_insurance_approval

                finalize_insurance_approval(claim)
                notify_user(
                    claim.client,
                    "Demande de prise en charge validée partiellement",
                    (
                        f"Votre demande {claim.order.code if claim.order_id else claim.pk} "
                        f"a été acceptée pour {approved_amount} F sur {original_amount} F. Motif : {notes}"
                    ),
                    notification_type=Notification.Type.INFO,
                    data={"claim_id": claim.id},
                    transactional=True,
                )
                _audit(request, "insurer_claim_partial", "claims", str(claim.pk), True)
                messages.success(request, "Prise en charge partielle enregistrée.")
        elif decision == "reject":
            if not notes:
                messages.error(request, "Le commentaire est obligatoire en cas de refus.")
            else:
                claim = _handle_claim_action(
                    request,
                    profile,
                    claim.pk,
                    InsuranceClaim.Status.REJECTED,
                    review_notes=notes,
                    attachment=attachment,
                )
                if claim.status == InsuranceClaim.Status.REJECTED:
                    messages.success(request, "Demande refusée.")
        else:
            messages.error(request, "Décision invalide.")
        return redirect("bo_insurer_claim_detail", claim_id=claim.pk)

    detail = build_claim_detail(claim)
    back_url = request.GET.get("next") or reverse("bo_insurer_claims")
    return _render(
        request,
        "claims_detail",
        "backoffice/insurer/claim_detail.html",
        profile,
        claim=claim,
        detail=detail,
        back_url=back_url,
    )


@role_required(*insurer_access_roles)
@portal_permission_required("members")
def insurer_members(request):
    from core.insurer_members import (
        enrich_member_row,
        member_cities_for_filter,
        members_apply_filters,
        members_apply_search,
        members_apply_tab,
        members_base_queryset,
        members_export_csv,
        members_stats,
    )

    profile = _insurer_profile(request.user, request)
    qs = members_base_queryset(profile)
    q = (request.GET.get("q") or "").strip()
    tab = (request.GET.get("tab") or "all").strip()
    city = (request.GET.get("city") or "").strip() or None
    member_status = (request.GET.get("member_status") or "").strip() or None
    show_filters = request.GET.get("filters") == "1" or bool(city or member_status)

    qs = members_apply_search(qs, q)
    qs = members_apply_tab(qs, tab)
    qs = members_apply_filters(qs, city=city, member_status=member_status)

    stats = members_stats(profile)
    per_page = int(request.GET.get("per_page") or 10)
    if per_page not in {10, 25, 50}:
        per_page = 10

    if request.GET.get("export") == "csv":
        return members_export_csv(profile, qs)

    page = paginate(request, qs, per_page)
    for member in page.object_list:
        enrich_member_row(member)

    active_filters = int(bool(city)) + int(bool(member_status))
    reset_params = []
    if tab and tab != "all":
        reset_params.append(f"tab={tab}")
    members_reset_url = reverse("bo_insurer_members")
    if reset_params:
        members_reset_url += "?" + "&".join(reset_params)

    return _render(
        request,
        "members",
        "backoffice/insurer/members.html",
        profile,
        page_obj=page,
        member_stats=stats,
        q=q,
        tab=tab,
        city=city or "",
        member_status=member_status or "",
        show_filters=show_filters,
        active_filters=active_filters,
        filter_cities=member_cities_for_filter(profile),
        per_page=per_page,
        members_reset_url=members_reset_url,
        inactive_tab_count=stats["inactive"] + stats["deregistered"],
        page_title="Gestion des assurés",
    )


@role_required(*insurer_access_roles)
@portal_permission_required("pharmacies")
def insurer_pharmacies(request):
    from core.insurer_pharmacies import (
        enrich_pharmacy_row,
        pharmacies_apply_filters,
        pharmacies_apply_search,
        pharmacies_apply_tab,
        pharmacies_base_queryset,
        pharmacies_export_csv,
        pharmacies_stats,
        pharmacy_cities_for_filter,
        pharmacy_regions_for_filter,
    )

    profile = _insurer_profile(request.user, request)
    qs = pharmacies_base_queryset(profile)
    q = (request.GET.get("q") or "").strip()
    tab = (request.GET.get("tab") or "all").strip()
    city = (request.GET.get("city") or "").strip() or None
    region = (request.GET.get("region") or "").strip() or None
    pharmacy_status = (request.GET.get("pharmacy_status") or "").strip() or None
    show_filters = request.GET.get("filters") == "1" or bool(city or region or pharmacy_status)

    qs = pharmacies_apply_search(qs, q)
    qs = pharmacies_apply_tab(qs, tab)
    qs = pharmacies_apply_filters(qs, city=city, region=region, status=pharmacy_status)

    stats = pharmacies_stats(profile)
    per_page = int(request.GET.get("per_page") or 10)
    if per_page not in {10, 25, 50}:
        per_page = 10

    if request.GET.get("export") == "csv":
        return pharmacies_export_csv(profile, qs)

    page = paginate(request, qs, per_page)
    for pharmacy in page.object_list:
        enrich_pharmacy_row(pharmacy)

    active_filters = int(bool(city)) + int(bool(region)) + int(bool(pharmacy_status))
    reset_params = []
    if tab and tab != "all":
        reset_params.append(f"tab={tab}")
    pharmacies_reset_url = reverse("bo_insurer_pharmacies")
    if reset_params:
        pharmacies_reset_url += "?" + "&".join(reset_params)

    return _render(
        request,
        "pharmacies",
        "backoffice/insurer/pharmacies.html",
        profile,
        page_obj=page,
        pharmacy_stats=stats,
        q=q,
        tab=tab,
        city=city or "",
        region=region or "",
        pharmacy_status=pharmacy_status or "",
        show_filters=show_filters,
        active_filters=active_filters,
        filter_cities=pharmacy_cities_for_filter(profile),
        filter_regions=pharmacy_regions_for_filter(profile),
        per_page=per_page,
        pharmacies_reset_url=pharmacies_reset_url,
        page_title="Gestion des pharmacies partenaires",
    )


@role_required(*insurer_access_roles)
@portal_permission_required("pharmacies")
def insurer_pharmacy_detail(request, pharmacy_id):
    import json

    from core.insurer_pharmacies import build_pharmacy_detail, get_pharmacy_for_insurer

    profile = _insurer_profile(request.user, request)
    pharmacy = get_pharmacy_for_insurer(profile, pharmacy_id)
    detail = build_pharmacy_detail(profile, pharmacy)
    back_url = request.GET.get("next") or reverse("bo_insurer_pharmacies")
    return _render(
        request,
        "pharmacies",
        "backoffice/insurer/pharmacy_detail.html",
        profile,
        detail=detail,
        pharmacy=pharmacy,
        back_url=back_url,
    )


@role_required(*insurer_access_roles)
@portal_permission_required("payments")
def insurer_payments(request):
    import json

    from core.insurer_payments import (
        enrich_transaction,
        parse_payment_dates,
        payments_apply_filters,
        payments_apply_search,
        payments_base_queryset,
        payments_charts_data,
        payments_export_csv,
        payments_stats,
    )

    profile = _insurer_profile(request.user, request)
    qs = payments_base_queryset(profile)
    q = (request.GET.get("q") or "").strip()
    tx_type = (request.GET.get("tx_type") or "").strip() or None
    tx_status = (request.GET.get("tx_status") or "").strip() or None
    date_from, date_to = parse_payment_dates(request)

    qs = payments_apply_search(qs, q)
    qs = payments_apply_filters(
        qs, tx_type=tx_type, status=tx_status, date_from=date_from, date_to=date_to
    )

    if request.GET.get("export") == "csv":
        return payments_export_csv(qs)

    stats = payments_stats(profile)
    charts = payments_charts_data(profile)
    per_page = int(request.GET.get("per_page") or 10)
    if per_page not in {10, 25, 50}:
        per_page = 10

    page = paginate(request, qs.order_by("-created_at"), per_page)
    for tx in page.object_list:
        enrich_transaction(tx)

    return _render(
        request,
        "payments",
        "backoffice/insurer/payments.html",
        profile,
        page_obj=page,
        payment_stats=stats,
        charts_json=json.dumps(charts),
        q=q,
        tx_type=tx_type or "",
        tx_status=tx_status or "",
        date_from=date_from.isoformat() if date_from else "",
        date_to=date_to.isoformat() if date_to else "",
        per_page=per_page,
        page_title="Paiements et remboursements",
    )


@role_required(*insurer_access_roles)
@portal_permission_required("contracts")
def insurer_contracts(request):
    import json

    from core.insurer_contracts import (
        contracts_apply_filters,
        contracts_apply_search,
        contracts_filter_tab,
        contracts_base_queryset,
        contracts_charts_data,
        contracts_export_csv,
        contracts_stats,
        enrich_contract_profile,
    )

    profile = _insurer_profile(request.user, request)
    qs = contracts_base_queryset(profile)
    q = (request.GET.get("q") or "").strip()
    tab = (request.GET.get("tab") or "all").strip()
    plan = (request.GET.get("plan") or "").strip() or None
    show_filters = request.GET.get("filters") == "1" or bool(plan)

    qs = contracts_apply_search(qs, q)
    qs = contracts_apply_filters(qs, plan=plan)
    qs = contracts_filter_tab(qs, tab)

    if request.GET.get("export") == "csv":
        return contracts_export_csv(qs)

    stats = contracts_stats(profile)
    charts = contracts_charts_data(profile)
    per_page = int(request.GET.get("per_page") or 10)
    if per_page not in {10, 25, 50}:
        per_page = 10

    page = paginate(request, qs, per_page)
    for row in page.object_list:
        enrich_contract_profile(row)

    contracts_reset_url = reverse("bo_insurer_contracts")
    if tab and tab != "all":
        contracts_reset_url += f"?tab={tab}"

    return _render(
        request,
        "contracts",
        "backoffice/insurer/contracts.html",
        profile,
        page_obj=page,
        contract_stats=stats,
        charts_json=json.dumps(charts, default=str),
        q=q,
        tab=tab,
        plan=plan or "",
        show_filters=show_filters,
        per_page=per_page,
        contracts_reset_url=contracts_reset_url,
        page_title="Contrats et polices",
    )


@role_required(*insurer_access_roles)
@portal_permission_required("subscription")
def insurer_subscription(request):
    import json

    from core.insurer_subscription import (
        enrich_subscription,
        provider_plans,
        subscribe_user,
        subscriptions_apply_filters,
        subscriptions_apply_search,
        subscriptions_base_queryset,
        subscriptions_charts_data,
        subscriptions_export_csv,
        subscriptions_filter_tab,
        subscriptions_stats,
        toggle_plan_active,
        update_subscription_status,
    )
    from payments.models import InsuranceSubscriptionPlan

    profile = _insurer_profile(request.user, request)
    plans = list(provider_plans(profile))

    if request.method == "POST" and not profile.preview:
        action = request.POST.get("action")
        provider = profile.insurance_provider
        if action == "update_status" and profile.insurance_provider_id:
            sub_id = request.POST.get("subscription_id")
            new_status = request.POST.get("status")
            if sub_id and new_status:
                if update_subscription_status(int(sub_id), profile.insurance_provider_id, new_status):
                    messages.success(request, "Statut de l'abonnement mis à jour.")
                else:
                    messages.error(request, "Mise à jour impossible.")
        elif action == "create_subscription" and provider:
            from core.insurer_subscription import _resolve_user

            plan = InsuranceSubscriptionPlan.objects.filter(
                pk=request.POST.get("plan_id"),
                provider=provider,
            ).first()
            user = _resolve_user(request.POST.get("member_identifier", ""))
            if not plan:
                messages.error(request, "Formule invalide.")
            elif not user:
                messages.error(request, "Bénéficiaire introuvable (e-mail, identifiant ou n° assuré).")
            else:
                subscribe_user(
                    user,
                    provider,
                    plan,
                    employer_name=(request.POST.get("employer_name") or "").strip(),
                )
                messages.success(request, f"Abonnement créé pour {user.get_full_name() or user.username}.")
        elif action == "toggle_plan" and profile.insurance_provider_id:
            plan_id = request.POST.get("plan_id")
            if plan_id and toggle_plan_active(int(plan_id), profile.insurance_provider_id):
                messages.success(request, "Formule mise à jour.")
        return redirect(request.get_full_path())

    qs = subscriptions_base_queryset(profile)
    q = (request.GET.get("q") or "").strip()
    tab = (request.GET.get("tab") or "all").strip()
    plan = (request.GET.get("plan") or "").strip() or None
    sub_status = (request.GET.get("status") or "").strip() or None

    qs = subscriptions_apply_search(qs, q)
    qs = subscriptions_filter_tab(qs, tab)
    qs = subscriptions_apply_filters(qs, plan=plan, status=sub_status)

    if request.GET.get("export") == "csv":
        return subscriptions_export_csv(qs)

    stats = subscriptions_stats(profile)
    charts = subscriptions_charts_data(profile)
    per_page = int(request.GET.get("per_page") or 10)
    if per_page not in {10, 25, 50}:
        per_page = 10

    page = paginate(request, qs.order_by("-created_at"), per_page)
    for sub in page.object_list:
        enrich_subscription(sub)

    return _render(
        request,
        "subscription",
        "backoffice/insurer/subscription.html",
        profile,
        page_obj=page,
        sub_stats=stats,
        charts_json=json.dumps(charts),
        q=q,
        tab=tab,
        plan=plan or "",
        sub_status=sub_status or "",
        per_page=per_page,
        page_title="Abonnements assurance",
        provider_plans=plans,
    )


@role_required(*insurer_access_roles)
@portal_permission_required("reports")
def insurer_reports(request):
    import json

    from core.insurer_reports import (
        build_reports_payload,
        parse_report_dates,
        reports_export_csv,
    )

    profile = _insurer_profile(request.user, request)
    date_from, date_to = parse_report_dates(request)
    zone = (request.GET.get("zone") or "").strip()
    indicator = (request.GET.get("indicator") or "").strip()

    payload = build_reports_payload(profile, date_from, date_to, zone=zone)
    if request.GET.get("export") == "csv":
        return reports_export_csv(payload)

    return _render(
        request,
        "reports",
        "backoffice/insurer/reports.html",
        profile,
        reports=payload,
        charts_json=json.dumps(payload["charts"]),
        date_from=date_from.isoformat() if date_from else "",
        date_to=date_to.isoformat() if date_to else "",
        zone=zone,
        indicator=indicator,
        is_demo=payload.get("is_demo", False),
    )


@role_required(*insurer_access_roles)
@portal_permission_required("fraud")
def insurer_fraud(request):
    import json

    from core.insurer_fraud import (
        fraud_alerts_queryset,
        fraud_apply_filters_qs,
        fraud_apply_search_qs,
        fraud_charts_data_from_qs,
        fraud_export_csv,
        fraud_stats_from_qs,
        sync_fraud_alerts,
        update_alert_status,
    )
    from core.insurer_reports import parse_report_dates

    profile = _insurer_profile(request.user, request)
    sync_fraud_alerts(profile)

    if request.method == "POST":
        alert_id = request.POST.get("alert_id")
        new_status = request.POST.get("status")
        if alert_id and new_status and profile.insurance_provider_id:
            if update_alert_status(int(alert_id), profile.insurance_provider_id, new_status):
                messages.success(request, "Statut de l'alerte mis à jour.")
            else:
                messages.error(request, "Impossible de mettre à jour l'alerte.")
        return redirect(request.get_full_path())

    date_from, date_to = parse_report_dates(request)
    q = (request.GET.get("q") or "").strip()
    alert_type = (request.GET.get("alert_type") or "").strip() or None
    alert_level = (request.GET.get("level") or "").strip() or None
    alert_status = (request.GET.get("status") or "").strip() or None
    zone = (request.GET.get("zone") or "").strip() or None

    qs = fraud_alerts_queryset(profile, date_from, date_to)
    qs = fraud_apply_search_qs(qs, q)
    qs = fraud_apply_filters_qs(
        qs, alert_type=alert_type, level=alert_level, status=alert_status, zone=zone
    )

    if request.GET.get("export") == "csv":
        return fraud_export_csv(qs.order_by("-detected_at"))

    stats = fraud_stats_from_qs(qs)
    charts = fraud_charts_data_from_qs(qs)
    per_page = int(request.GET.get("per_page") or 10)
    if per_page not in {10, 25, 50}:
        per_page = 10

    page = paginate(request, qs.order_by("-detected_at"), per_page)
    active_filters = sum(1 for v in (alert_type, alert_level, alert_status, zone) if v)

    return _render(
        request,
        "fraud",
        "backoffice/insurer/fraud.html",
        profile,
        page_obj=page,
        fraud_stats=stats,
        charts_json=json.dumps(charts),
        q=q,
        date_from=date_from.isoformat() if date_from else "",
        date_to=date_to.isoformat() if date_to else "",
        alert_type=alert_type or "",
        alert_level=alert_level or "",
        alert_status=alert_status or "",
        zone=zone or "",
        per_page=per_page,
        active_filters=active_filters,
        show_filters=request.GET.get("filters") == "1",
    )


@role_required(*insurer_access_roles)
@portal_permission_required("notifications")
def insurer_notifications(request):
    from notifications.models import Notification

    from core.insurer_notifications import (
        enrich_notification,
        get_notification_preferences,
        notifications_apply_filters,
        notifications_apply_search,
        notifications_apply_tab,
        notifications_queryset,
        notifications_stats,
        parse_notification_dates,
        portal_settings_for_profile,
        tab_counts,
    )
    from core.insurer_settings_portal import save_notification_settings

    profile = _insurer_profile(request.user, request)
    user = request.user

    from core.insurer_claims import claims_base_queryset
    from core.insurer_notifications import on_insurance_claim_created

    for claim in claims_base_queryset(profile).order_by("-created_at")[:25]:
        on_insurance_claim_created(claim)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "mark_all_read":
            Notification.objects.filter(user=user, is_read=False).update(is_read=True)
            messages.success(request, "Toutes les notifications ont été marquées comme lues.")
        elif action == "save_notification_prefs" and not profile.preview:
            save_notification_settings(profile, user, request.POST)
            messages.success(request, "Préférences de notification enregistrées.")
        elif action == "mark_read":
            notif_id = request.POST.get("notification_id")
            if notif_id:
                Notification.objects.filter(user=user, pk=notif_id).update(is_read=True)
        tab = request.POST.get("tab") or request.GET.get("tab") or "all"
        return redirect(f"{reverse('bo_insurer_notifications')}?tab={tab}")

    tab = (request.GET.get("tab") or "all").strip()
    q = (request.GET.get("q") or "").strip()
    category = (request.GET.get("category") or "").strip() or None
    date_from, date_to = parse_notification_dates(request)

    qs = notifications_queryset(user)
    qs = notifications_apply_tab(qs, tab)
    qs = notifications_apply_search(qs, q)
    qs = notifications_apply_filters(qs, category=category, date_from=date_from, date_to=date_to)

    per_page = int(request.GET.get("per_page") or 10)
    if per_page not in {10, 25, 50}:
        per_page = 10

    page = paginate(request, qs, per_page)
    for notif in page.object_list:
        notif.ui = enrich_notification(notif)

    portal_settings = portal_settings_for_profile(profile)
    notif_prefs = get_notification_preferences(user)
    from core.insurer_settings_portal import build_settings_context

    settings_ctx = build_settings_context(profile, user)

    return _render(
        request,
        "notifications",
        "backoffice/insurer/notifications.html",
        profile,
        page_obj=page,
        notif_stats=notifications_stats(user),
        tab_counts=tab_counts(user),
        tab=tab,
        q=q,
        category=category or "",
        date_from=date_from.isoformat() if date_from else "",
        date_to=date_to.isoformat() if date_to else "",
        per_page=per_page,
        portal_settings=portal_settings,
        notif_prefs=notif_prefs,
        settings_ctx=settings_ctx,
    )


@role_required(*insurer_access_roles)
@portal_permission_required("settings")
def insurer_settings(request):
    from core.insurer_settings_portal import (
        SETTINGS_TABS,
        build_settings_context,
        save_general_settings,
        save_notification_settings,
        save_preferences,
        save_system_settings,
    )

    profile = _insurer_profile(request.user, request)
    tab = (request.GET.get("tab") or "general").strip()
    if tab not in {t[0] for t in SETTINGS_TABS}:
        tab = "general"

    if request.method == "POST":
        if profile.preview:
            messages.warning(
                request,
                "Mode aperçu admin : créez un utilisateur partenaire assureur pour modifier les paramètres.",
            )
            return redirect(f"{reverse('bo_insurer_settings')}?tab={tab}")
        action = request.POST.get("action", "save_general")
        if action == "save_general":
            save_general_settings(profile, request.user, request.POST)
        elif action == "save_preferences":
            save_preferences(profile, request.POST)
        elif action == "save_notification_prefs":
            save_notification_settings(profile, request.user, request.POST)
        elif action == "save_system":
            save_system_settings(profile, request.POST)
        _audit(request, "update_insurer_settings", "insurer", profile.organization_name)
        messages.success(request, "Paramètres enregistrés.")
        return redirect(f"{reverse('bo_insurer_settings')}?tab={tab}")

    settings_ctx = build_settings_context(profile, request.user)
    from core.insurer_notifications import get_notification_preferences

    return _render(
        request,
        "settings",
        "backoffice/insurer/settings.html",
        profile,
        settings_tab=tab,
        settings_tabs=SETTINGS_TABS,
        settings_ctx=settings_ctx,
        notif_prefs=get_notification_preferences(request.user),
    )


@role_required(*insurer_access_roles)
@portal_permission_required("support")
def insurer_support(request):
    profile = _insurer_profile(request.user, request)
    return _render(request, "support", "backoffice/insurer/support.html", profile)


partner_dashboard = insurer_dashboard
