"""Filtres de recherche portail pharmacie."""
from datetime import datetime
from urllib.parse import urlencode

from django.db.models import Exists, OuterRef, Q

from orders.models import Order
from pharmacies.models import EmployeeAbsence, EmployeePayslip, PharmacyEmployee


def list_query_params(request, *keys, **overrides):
    """Reconstruit une query string en conservant les filtres actifs."""
    params = {}
    for key in keys:
        val = request.GET.get(key)
        if val not in (None, ""):
            params[key] = val
    for key, val in overrides.items():
        if val in (None, ""):
            params.pop(key, None)
        else:
            params[key] = val
    return urlencode(params)


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def filter_pharmacy_orders(qs, request):
    """Filtre queryset commandes selon GET."""
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    delivery_mode = request.GET.get("delivery_mode", "").strip()
    payment = request.GET.get("payment", "").strip()
    date_from = parse_date(request.GET.get("date_from"))
    date_to = parse_date(request.GET.get("date_to"))
    urgent = request.GET.get("urgent") == "1"

    if q:
        qs = qs.filter(
            Q(code__icontains=q)
            | Q(client__username__icontains=q)
            | Q(client__first_name__icontains=q)
            | Q(client__last_name__icontains=q)
            | Q(client__email__icontains=q)
            | Q(client__phone__icontains=q)
            | Q(items__medicine_name__icontains=q)
            | Q(notes__icontains=q)
        ).distinct()
    if status and status in dict(Order.Status.choices):
        qs = qs.filter(status=status)
    if delivery_mode and delivery_mode in dict(Order.DeliveryMode.choices):
        qs = qs.filter(delivery_mode=delivery_mode)
    if payment:
        qs = qs.filter(payments__method=payment).distinct()
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if urgent:
        qs = qs.filter(is_urgent=True)
    return qs


def filter_pharmacy_employees(qs, request):
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "").strip()
    active = request.GET.get("active", "").strip()
    contract = request.GET.get("contract", "").strip()

    if q:
        qs = qs.filter(
            Q(employee_code__icontains=q)
            | Q(job_title__icontains=q)
            | Q(national_id__icontains=q)
            | Q(professional_license__icontains=q)
            | Q(user__username__icontains=q)
            | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__email__icontains=q)
            | Q(user__phone__icontains=q)
        )
    if role and role in dict(PharmacyEmployee.JobRole.choices):
        qs = qs.filter(job_role=role)
    if active == "active":
        qs = qs.filter(is_active=True)
    elif active == "inactive":
        qs = qs.filter(is_active=False)
    if contract and contract in dict(PharmacyEmployee.ContractType.choices):
        qs = qs.filter(contract_type=contract)
    return qs


def filter_pharmacy_absences(qs, request):
    q = request.GET.get("q", "").strip()
    absence_status = request.GET.get("absence_status", "").strip()
    absence_type = request.GET.get("absence_type", "").strip()

    if q:
        qs = qs.filter(
            Q(employee__employee_code__icontains=q)
            | Q(employee__user__first_name__icontains=q)
            | Q(employee__user__last_name__icontains=q)
            | Q(employee__user__username__icontains=q)
            | Q(reason__icontains=q)
        )
    if absence_status and absence_status in dict(EmployeeAbsence.Status.choices):
        qs = qs.filter(status=absence_status)
    if absence_type and absence_type in dict(EmployeeAbsence.AbsenceType.choices):
        qs = qs.filter(absence_type=absence_type)
    return qs


def filter_pharmacy_payslips(qs, request):
    q = request.GET.get("q", "").strip()
    payslip_status = request.GET.get("payslip_status", "").strip()
    period_month = request.GET.get("period_month", "").strip()
    period_year = request.GET.get("period_year", "").strip()

    if q:
        qs = qs.filter(
            Q(employee__employee_code__icontains=q)
            | Q(employee__user__first_name__icontains=q)
            | Q(employee__user__last_name__icontains=q)
            | Q(employee__user__username__icontains=q)
        )
    if payslip_status and payslip_status in dict(EmployeePayslip.Status.choices):
        qs = qs.filter(status=payslip_status)
    if period_year.isdigit():
        qs = qs.filter(period_year=int(period_year))
    if period_month.isdigit():
        qs = qs.filter(period_month=int(period_month))
    return qs


def filter_pharmacy_conversations(qs, request, user, message_model):
    q = request.GET.get("q", "").strip()
    unread = request.GET.get("unread") == "1"
    period = request.GET.get("period", "").strip()

    unread_qs = message_model.objects.filter(
        conversation=OuterRef("pk"),
        is_read=False,
    ).exclude(sender=user)
    qs = qs.annotate(has_unread=Exists(unread_qs))

    if q:
        qs = qs.filter(
            Q(client__username__icontains=q)
            | Q(client__first_name__icontains=q)
            | Q(client__last_name__icontains=q)
            | Q(client__email__icontains=q)
            | Q(client__phone__icontains=q)
            | Q(messages__body__icontains=q)
        ).distinct()
    if unread:
        qs = qs.filter(has_unread=True)
    if period in {"7", "30", "90"}:
        from datetime import timedelta

        from django.utils import timezone

        since = timezone.now() - timedelta(days=int(period))
        qs = qs.filter(updated_at__gte=since)
    return qs
