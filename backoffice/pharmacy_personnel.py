"""Portail pharmacie — gestion du personnel (ERP RH)."""
import csv
from datetime import datetime, timedelta

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from accounts.mail import generate_temp_password, send_account_credentials
from accounts.models import User
from backoffice.decorators import pharmacy_roles, role_required
from backoffice.views import _audit, _create_staff_user, _ctx
from core.pharmacy_filters import (
    filter_pharmacy_absences,
    filter_pharmacy_employees,
    filter_pharmacy_payslips,
)
from core.pharmacy_access import (
    PERM_PAYROLL,
    PERM_STAFF,
    allocate_employee_code,
    can_manage_staff,
    employee_for,
    ensure_owner_membership,
    get_role_permissions_map,
    has_pharmacy_permission,
    pharmacies_for_user,
    pharmacy_default_route,
    pharmacy_for_user,
    pharmacy_permission_flags,
    pharmacy_permissions_matrix,
    set_active_pharmacy,
    team_effective_permissions,
)
from core.platform_access import PHARMACY_PERM_LABELS
from pharmacies.models import (
    EmployeeAbsence,
    EmployeePayslip,
    EmployeeShift,
    PharmacyEmployee,
)


def _personnel_redirect(request, tab="team"):
    params = {}
    for key in (
        "week",
        "q",
        "role",
        "active",
        "contract",
        "absence_status",
        "absence_type",
        "payslip_status",
        "period_month",
        "period_year",
        "pharmacy_id",
    ):
        val = request.POST.get(key) or request.GET.get(key)
        if val:
            params[key] = val
    params["tab"] = tab
    return redirect(f"{reverse('bo_pharmacy_personnel')}?{urlencode(params)}")


def _week_start(date_val):
    return date_val - timedelta(days=date_val.weekday())


def _find_pharmacy_employee(pharmacy, email, username):
    qs = PharmacyEmployee.objects.filter(pharmacy=pharmacy).select_related("user")
    if email:
        emp = qs.filter(user__email__iexact=email).first()
        if emp:
            return emp
    if username:
        return qs.filter(user__username__iexact=username).first()
    return None


def _permission_labels_for_role(job_role):
    perm_map = get_role_permissions_map()
    return [
        PHARMACY_PERM_LABELS[p]
        for p in sorted(perm_map.get(job_role, set()))
        if p in PHARMACY_PERM_LABELS
    ]


def _role_change_message(emp, old_role):
    labels = _permission_labels_for_role(emp.job_role)
    access = ", ".join(labels) if labels else "aucun module ERP"
    return (
        f"Rôle mis à jour : {emp.get_job_role_display()}. "
        f"Accès : {access}."
    )


def _apply_employee_post(emp, request):
    emp.job_role = request.POST.get("job_role", emp.job_role)
    emp.job_title = request.POST.get("job_title", emp.job_title).strip()
    emp.contract_type = request.POST.get("contract_type", emp.contract_type)
    emp.base_salary = int(request.POST.get("base_salary") or emp.base_salary)
    emp.national_id = request.POST.get("national_id", emp.national_id).strip()
    emp.professional_license = request.POST.get(
        "professional_license", emp.professional_license
    ).strip()
    emp.emergency_contact = request.POST.get(
        "emergency_contact", emp.emergency_contact
    ).strip()
    emp.emergency_phone = request.POST.get(
        "emergency_phone", emp.emergency_phone
    ).strip()
    emp.notes = request.POST.get("notes", emp.notes).strip()


@role_required(*pharmacy_roles)
def pharmacy_personnel(request):
    pharmacy = pharmacy_for_user(request.user, request)
    if not pharmacy:
        messages.warning(request, "Aucune pharmacie associée à votre compte.")
        return render(
            request,
            "backoffice/pharmacy/personnel.html",
            _ctx(request, "personnel", pharmacy=None, tab="team"),
        )

    if request.GET.get("pharmacy_id"):
        try:
            ph = pharmacies_for_user(request.user).filter(
                pk=int(request.GET["pharmacy_id"])
            ).first()
            if ph:
                set_active_pharmacy(request, ph)
                pharmacy = ph
        except (TypeError, ValueError):
            pass

    ensure_owner_membership(pharmacy)
    membership = employee_for(request.user, pharmacy)
    can_manage = can_manage_staff(request.user, pharmacy)
    can_payroll = has_pharmacy_permission(request.user, pharmacy, PERM_PAYROLL)

    tab = request.GET.get("tab", "team")
    if not has_pharmacy_permission(request.user, pharmacy, PERM_STAFF):
        if tab != "absences":
            messages.error(
                request,
                "Cette section est réservée au gérant. Vous pouvez gérer les commandes.",
            )
            return redirect(pharmacy_default_route(request.user, pharmacy))

    if request.method == "POST":
        action = request.POST.get("action", "")
        if not can_manage and action not in {"request_absence"}:
            messages.error(request, "Vous n'avez pas les droits de gestion du personnel.")
            return _personnel_redirect(request, request.POST.get("tab", "team"))

        if action == "add_employee":
            first = request.POST.get("first_name", "").strip()
            last = request.POST.get("last_name", "").strip()
            email = request.POST.get("email", "").strip()
            phone = request.POST.get("phone", "").strip()
            username = request.POST.get("username", "").strip() or (
                email.split("@")[0] if email else ""
            )
            if not first or not last:
                messages.error(request, "Prénom et nom obligatoires.")
            elif not username:
                messages.error(request, "Identifiant ou e-mail obligatoire.")
            else:
                existing_emp = _find_pharmacy_employee(pharmacy, email, username)
                if existing_emp:
                    old_role = existing_emp.job_role
                    _apply_employee_post(existing_emp, request)
                    existing_emp.is_active = True
                    existing_emp.ended_at = None
                    u = existing_emp.user
                    u.first_name = first
                    u.last_name = last
                    if email:
                        u.email = email
                    if phone:
                        u.phone = phone
                    u.save(update_fields=["first_name", "last_name", "email", "phone"])
                    existing_emp.save()
                    msg = (
                        f"Collaborateur déjà enregistré — fiche de "
                        f"{existing_emp.display_name} mise à jour."
                    )
                    if old_role != existing_emp.job_role:
                        msg += f" {_role_change_message(existing_emp, old_role)}"
                    messages.success(request, msg)
                else:
                    global_user = None
                    if email:
                        global_user = User.objects.filter(email__iexact=email).first()
                    if not global_user:
                        global_user = User.objects.filter(
                            username__iexact=username
                        ).first()

                    if global_user and PharmacyEmployee.objects.filter(
                        pharmacy=pharmacy, user=global_user
                    ).exists():
                        messages.error(
                            request,
                            "Ce compte est déjà rattaché à votre officine. "
                            "Modifiez sa fiche dans la liste ci-dessous.",
                        )
                    elif global_user:
                        code = allocate_employee_code(
                            pharmacy, request.POST.get("employee_code", "").strip()
                        )
                        emp = PharmacyEmployee.objects.create(
                            pharmacy=pharmacy,
                            user=global_user,
                            employee_code=code,
                            job_role=request.POST.get(
                                "job_role", PharmacyEmployee.JobRole.PHARMACIST
                            ),
                            job_title=request.POST.get("job_title", "").strip(),
                            contract_type=request.POST.get(
                                "contract_type", PharmacyEmployee.ContractType.CDI
                            ),
                            base_salary=int(request.POST.get("base_salary") or 0),
                            national_id=request.POST.get("national_id", "").strip(),
                            professional_license=request.POST.get(
                                "professional_license", ""
                            ).strip(),
                            emergency_contact=request.POST.get(
                                "emergency_contact", ""
                            ).strip(),
                            emergency_phone=request.POST.get(
                                "emergency_phone", ""
                            ).strip(),
                            notes=request.POST.get("notes", "").strip(),
                            is_active=True,
                        )
                        u = global_user
                        u.first_name = first
                        u.last_name = last
                        if email:
                            u.email = email
                        if phone:
                            u.phone = phone
                        u.save(update_fields=["first_name", "last_name", "email", "phone"])
                        _audit(
                            request,
                            "attach",
                            "personnel",
                            f"Rattachement {emp.display_name} ({emp.employee_code})",
                        )
                        messages.success(
                            request,
                            f"{emp.display_name} rattaché à l'officine "
                            f"(matricule {emp.employee_code}, rôle "
                            f"{emp.get_job_role_display()}).",
                        )
                    elif User.objects.filter(username=username).exists():
                        messages.error(
                            request,
                            f"Identifiant « {username} » déjà utilisé par un autre compte. "
                            "Utilisez un autre identifiant ou modifiez la fiche existante.",
                        )
                    else:
                        try:
                            with transaction.atomic():
                                user, plain, delivery = _create_staff_user(
                                    request,
                                    role=User.Role.PHARMACIST,
                                    username=username,
                                    email=email,
                                    first_name=first,
                                    last_name=last,
                                    phone=phone,
                                    city=pharmacy.city,
                                )
                                code = allocate_employee_code(
                                    pharmacy,
                                    request.POST.get("employee_code", "").strip(),
                                )
                                emp = PharmacyEmployee.objects.create(
                                    pharmacy=pharmacy,
                                    user=user,
                                    employee_code=code,
                                    job_role=request.POST.get(
                                        "job_role",
                                        PharmacyEmployee.JobRole.PHARMACIST,
                                    ),
                                    job_title=request.POST.get("job_title", "").strip(),
                                    contract_type=request.POST.get(
                                        "contract_type",
                                        PharmacyEmployee.ContractType.CDI,
                                    ),
                                    base_salary=int(
                                        request.POST.get("base_salary") or 0
                                    ),
                                    national_id=request.POST.get(
                                        "national_id", ""
                                    ).strip(),
                                    professional_license=request.POST.get(
                                        "professional_license", ""
                                    ).strip(),
                                    emergency_contact=request.POST.get(
                                        "emergency_contact", ""
                                    ).strip(),
                                    emergency_phone=request.POST.get(
                                        "emergency_phone", ""
                                    ).strip(),
                                    notes=request.POST.get("notes", "").strip(),
                                )
                        except IntegrityError:
                            messages.error(
                                request,
                                "Impossible d'enregistrer ce matricule (doublon). "
                                "Laissez le champ matricule vide pour une attribution "
                                "automatique.",
                            )
                        else:
                            _audit(
                                request,
                                "create",
                                "personnel",
                                f"Employé {emp.display_name} ({emp.employee_code}) — "
                                f"{pharmacy.name}",
                            )
                            msg = (
                                f"Employé {emp.display_name} ajouté "
                                f"(matricule {emp.employee_code})."
                            )
                            if delivery:
                                notice = delivery.notice(plain_password=plain)
                                if notice:
                                    msg += f" {notice}"
                            elif plain and email:
                                msg += f" Mot de passe temporaire : {plain}"
                            messages.success(request, msg)

        elif action == "update_employee":
            emp = get_object_or_404(
                PharmacyEmployee, pk=request.POST.get("employee_id"), pharmacy=pharmacy
            )
            if (
                emp.job_role == PharmacyEmployee.JobRole.OWNER
                and membership
                and membership.job_role != PharmacyEmployee.JobRole.OWNER
            ):
                messages.error(request, "Seul le titulaire peut modifier le gérant.")
                return _personnel_redirect(request, "team")
            new_code = request.POST.get("employee_code", "").strip()
            if new_code and new_code != emp.employee_code:
                if PharmacyEmployee.objects.filter(
                    pharmacy=pharmacy, employee_code=new_code
                ).exclude(pk=emp.pk).exists():
                    messages.error(
                        request,
                        f"Le matricule « {new_code} » est déjà utilisé dans cette officine.",
                    )
                    return _personnel_redirect(request, "team")
                emp.employee_code = new_code
            old_role = emp.job_role
            _apply_employee_post(emp, request)
            emp.is_active = request.POST.get("is_active") == "on"
            if not emp.is_active and not emp.ended_at:
                emp.ended_at = timezone.localdate()
            elif emp.is_active:
                emp.ended_at = None
            u = emp.user
            u.first_name = request.POST.get("first_name", u.first_name).strip()
            u.last_name = request.POST.get("last_name", u.last_name).strip()
            u.email = request.POST.get("email", u.email).strip()
            u.phone = request.POST.get("phone", u.phone).strip()
            u.save(update_fields=["first_name", "last_name", "email", "phone"])
            emp.save()
            if old_role != emp.job_role:
                messages.success(
                    request,
                    f"Fiche {emp.display_name} mise à jour. "
                    f"{_role_change_message(emp, old_role)}",
                )
            else:
                messages.success(request, f"Fiche {emp.display_name} mise à jour.")

        elif action == "resend_credentials":
            emp = get_object_or_404(
                PharmacyEmployee, pk=request.POST.get("employee_id"), pharmacy=pharmacy
            )
            u = emp.user
            if not u.email:
                messages.error(request, "Ajoutez une adresse e-mail à la fiche employé.")
            else:
                plain = generate_temp_password()
                u.set_password(plain)
                u.save(update_fields=["password"])
                delivery = send_account_credentials(u, plain, request=request)
                if delivery.ok:
                    messages.success(
                        request,
                        f"Identifiants renvoyés pour {emp.display_name}. "
                        f"{delivery.notice(plain_password=plain)}",
                    )
                else:
                    messages.warning(
                        request, delivery.notice(plain_password=plain)
                    )

        elif action == "deactivate_employee":
            emp = get_object_or_404(
                PharmacyEmployee, pk=request.POST.get("employee_id"), pharmacy=pharmacy
            )
            if emp.job_role == PharmacyEmployee.JobRole.OWNER:
                messages.error(request, "Impossible de désactiver le titulaire.")
            else:
                emp.is_active = False
                emp.ended_at = timezone.localdate()
                emp.save(update_fields=["is_active", "ended_at", "updated_at"])
                messages.success(request, f"{emp.display_name} désactivé.")

        elif action == "add_shift":
            emp = get_object_or_404(
                PharmacyEmployee,
                pk=request.POST.get("employee_id"),
                pharmacy=pharmacy,
                is_active=True,
            )
            try:
                shift_date = datetime.strptime(
                    request.POST.get("shift_date"), "%Y-%m-%d"
                ).date()
                start = datetime.strptime(request.POST.get("start_time"), "%H:%M").time()
                end = datetime.strptime(request.POST.get("end_time"), "%H:%M").time()
            except (ValueError, TypeError):
                messages.error(request, "Date ou horaires invalides.")
            else:
                EmployeeShift.objects.update_or_create(
                    employee=emp,
                    shift_date=shift_date,
                    start_time=start,
                    defaults={
                        "end_time": end,
                        "break_minutes": int(request.POST.get("break_minutes") or 0),
                        "notes": request.POST.get("notes", "").strip(),
                        "created_by": request.user,
                    },
                )
                messages.success(
                    request,
                    f"Planning enregistré pour {emp.display_name} le {shift_date:%d/%m/%Y}.",
                )

        elif action == "delete_shift":
            shift = get_object_or_404(
                EmployeeShift,
                pk=request.POST.get("shift_id"),
                employee__pharmacy=pharmacy,
            )
            shift.delete()
            messages.success(request, "Créneau supprimé.")

        elif action == "request_absence":
            emp = membership
            if not emp:
                messages.error(request, "Vous n'êtes pas rattaché à cette pharmacie.")
            else:
                try:
                    start = datetime.strptime(
                        request.POST.get("start_date"), "%Y-%m-%d"
                    ).date()
                    end = datetime.strptime(
                        request.POST.get("end_date"), "%Y-%m-%d"
                    ).date()
                except (ValueError, TypeError):
                    messages.error(request, "Dates invalides.")
                else:
                    if end < start:
                        messages.error(
                            request, "La date de fin doit être après le début."
                        )
                    else:
                        EmployeeAbsence.objects.create(
                            employee=emp,
                            absence_type=request.POST.get(
                                "absence_type", EmployeeAbsence.AbsenceType.LEAVE
                            ),
                            start_date=start,
                            end_date=end,
                            reason=request.POST.get("reason", "").strip(),
                        )
                        messages.success(
                            request, "Demande d'absence envoyée au gérant."
                        )

        elif action == "review_absence" and can_manage:
            absence = get_object_or_404(
                EmployeeAbsence,
                pk=request.POST.get("absence_id"),
                employee__pharmacy=pharmacy,
            )
            decision = request.POST.get("decision")
            if decision in {
                EmployeeAbsence.Status.APPROVED,
                EmployeeAbsence.Status.REJECTED,
            }:
                absence.status = decision
                absence.reviewed_by = request.user
                absence.reviewed_at = timezone.now()
                absence.save(update_fields=["status", "reviewed_by", "reviewed_at"])
                label = (
                    "approuvée"
                    if decision == EmployeeAbsence.Status.APPROVED
                    else "refusée"
                )
                messages.success(
                    request,
                    f"Absence de {absence.employee.display_name} {label}.",
                )

        elif action == "generate_payslip" and can_payroll:
            emp = get_object_or_404(
                PharmacyEmployee,
                pk=request.POST.get("employee_id"),
                pharmacy=pharmacy,
                is_active=True,
            )
            try:
                year = int(request.POST.get("period_year"))
                month = int(request.POST.get("period_month"))
            except (TypeError, ValueError):
                messages.error(request, "Période invalide.")
            else:
                slip, created = EmployeePayslip.objects.get_or_create(
                    employee=emp,
                    period_year=year,
                    period_month=month,
                    defaults={
                        "base_salary": emp.base_salary,
                        "bonus": int(request.POST.get("bonus") or 0),
                        "deductions": int(request.POST.get("deductions") or 0),
                        "hours_worked": request.POST.get("hours_worked") or 0,
                        "notes": request.POST.get("payslip_notes", "").strip(),
                    },
                )
                if not created:
                    slip.base_salary = int(
                        request.POST.get("base_salary") or emp.base_salary
                    )
                    slip.bonus = int(request.POST.get("bonus") or slip.bonus)
                    slip.deductions = int(
                        request.POST.get("deductions") or slip.deductions
                    )
                    slip.hours_worked = (
                        request.POST.get("hours_worked") or slip.hours_worked
                    )
                    slip.notes = request.POST.get("payslip_notes", slip.notes).strip()
                    slip.save()
                messages.success(
                    request,
                    f"Bulletin {month:02d}/{year} — {emp.display_name} : {slip.net_salary} F net.",
                )

        elif action == "validate_payslip" and can_payroll:
            slip = get_object_or_404(
                EmployeePayslip,
                pk=request.POST.get("payslip_id"),
                employee__pharmacy=pharmacy,
            )
            slip.status = request.POST.get("status", EmployeePayslip.Status.VALIDATED)
            if slip.status == EmployeePayslip.Status.PAID:
                slip.paid_at = timezone.localdate()
            slip.save(update_fields=["status", "paid_at", "updated_at", "net_salary"])
            messages.success(request, f"Bulletin {slip} → {slip.get_status_display()}.")

        return _personnel_redirect(request, request.POST.get("tab", "team"))

    tab = request.GET.get("tab", "team")
    if tab == "roles" and not can_manage:
        messages.info(
            request,
            "La consultation des rôles et permissions est réservée au gérant.",
        )
        return redirect(f"{reverse('bo_pharmacy_personnel')}?tab=team")

    today = timezone.localdate()
    week_param = request.GET.get("week")
    if week_param:
        try:
            week_start = datetime.strptime(week_param, "%Y-%m-%d").date()
        except ValueError:
            week_start = _week_start(today)
    else:
        week_start = _week_start(today)
    week_end = week_start + timedelta(days=6)
    week_days = [week_start + timedelta(days=i) for i in range(7)]

    employees = filter_pharmacy_employees(
        PharmacyEmployee.objects.filter(pharmacy=pharmacy).select_related("user"),
        request,
    ).order_by("-is_active", "job_role", "user__last_name")
    active_employees = employees.filter(is_active=True)
    shifts = EmployeeShift.objects.filter(
        employee__pharmacy=pharmacy,
        shift_date__gte=week_start,
        shift_date__lte=week_end,
    ).select_related("employee", "employee__user")
    shifts_by_emp_date = {}
    for s in shifts:
        shifts_by_emp_date.setdefault(s.employee_id, {})[s.shift_date] = s

    shift_grid = []
    for e in active_employees:
        row_days = []
        emp_map = shifts_by_emp_date.get(e.id, {})
        for d in week_days:
            row_days.append(emp_map.get(d))
        shift_grid.append({"employee": e, "days": row_days})

    absences_qs = filter_pharmacy_absences(
        EmployeeAbsence.objects.filter(employee__pharmacy=pharmacy).select_related(
            "employee", "employee__user", "reviewed_by"
        ),
        request,
    )
    pending_absences = absences_qs.filter(
        status=EmployeeAbsence.Status.PENDING
    ).count()
    absences = absences_qs[:50]

    payslips_qs = filter_pharmacy_payslips(
        EmployeePayslip.objects.filter(employee__pharmacy=pharmacy).select_related(
            "employee", "employee__user"
        ),
        request,
    )
    payslips = payslips_qs[:40]

    on_shift_today = shifts.filter(shift_date=today).count()
    payroll_total = sum(
        p.net_salary
        for p in payslips_qs
        if p.period_year == today.year and p.period_month == today.month
    )

    kpis = {
        "active_count": active_employees.count(),
        "on_shift_today": on_shift_today,
        "pending_absences": pending_absences,
        "payroll_month": payroll_total,
    }

    if request.GET.get("export") == "payroll_csv" and can_payroll:
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="paie-{pharmacy.slug}-{today:%Y%m}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(
            [
                "Matricule",
                "Employé",
                "Rôle",
                "Période",
                "Base F",
                "Primes F",
                "Retenues F",
                "Net F",
                "Heures",
                "Statut",
            ]
        )
        for p in payslips:
            writer.writerow(
                [
                    p.employee.employee_code,
                    p.employee.display_name,
                    p.employee.get_job_role_display(),
                    f"{p.period_month:02d}/{p.period_year}",
                    p.base_salary,
                    p.bonus,
                    p.deductions,
                    p.net_salary,
                    p.hours_worked,
                    p.get_status_display(),
                ]
            )
        return response

    pharmacies = list(pharmacies_for_user(request.user))
    pharmacy_matrix, perm_labels = pharmacy_permissions_matrix()
    team_perm_rows, employee_perm_by_id = team_effective_permissions(employees)
    my_permissions = pharmacy_permission_flags(request.user, pharmacy)
    my_permission_labels = [
        perm_labels[k]
        for k, allowed in my_permissions.items()
        if allowed and k in perm_labels
    ]

    return render(
        request,
        "backoffice/pharmacy/personnel.html",
        _ctx(
            request,
            "personnel",
            pharmacy=pharmacy,
            pharmacies=pharmacies,
            tab=tab,
            employees=employees,
            active_employees=active_employees,
            membership=membership,
            can_manage=can_manage,
            can_payroll=can_payroll,
            kpis=kpis,
            week_start=week_start,
            week_end=week_end,
            week_days=week_days,
            prev_week=(week_start - timedelta(days=7)).isoformat(),
            next_week=(week_start + timedelta(days=7)).isoformat(),
            shifts_by_emp_date=shifts_by_emp_date,
            shift_grid=shift_grid,
            absences=absences,
            payslips=payslips,
            job_roles=PharmacyEmployee.JobRole.choices,
            contract_types=PharmacyEmployee.ContractType.choices,
            absence_types=EmployeeAbsence.AbsenceType.choices,
            absence_statuses=EmployeeAbsence.Status.choices,
            payslip_statuses=EmployeePayslip.Status.choices,
            today=today,
            search_q=request.GET.get("q", "").strip(),
            search_role=request.GET.get("role", "").strip(),
            search_active=request.GET.get("active", "").strip(),
            search_contract=request.GET.get("contract", "").strip(),
            search_absence_status=request.GET.get("absence_status", "").strip(),
            search_absence_type=request.GET.get("absence_type", "").strip(),
            search_payslip_status=request.GET.get("payslip_status", "").strip(),
            search_period_month=request.GET.get("period_month", "").strip(),
            search_period_year=request.GET.get("period_year", "").strip(),
            employees_count=employees.count(),
            absences_count=absences_qs.count(),
            payslips_count=payslips_qs.count(),
            pharmacy_matrix=pharmacy_matrix,
            perm_labels=perm_labels,
            team_perm_rows=team_perm_rows,
            employee_perm_by_id=employee_perm_by_id,
            my_permission_labels=my_permission_labels,
        ),
    )
