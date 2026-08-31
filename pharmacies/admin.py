from django.contrib import admin

from pharmacies.models import (
    Pharmacy,
    PharmacyDocument,
    PharmacyEmployee,
    PharmacyReview,
    EmployeeShift,
    EmployeeAbsence,
    EmployeePayslip,
)


@admin.register(PharmacyEmployee)
class PharmacyEmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "employee_code",
        "user",
        "pharmacy",
        "job_role",
        "is_active",
        "hired_at",
    )
    list_filter = ("pharmacy", "job_role", "is_active", "contract_type")
    search_fields = ("employee_code", "user__username", "user__email")


admin.site.register(EmployeeShift)
admin.site.register(EmployeeAbsence)
admin.site.register(EmployeePayslip)


@admin.register(Pharmacy)
class PharmacyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "city", "status", "rating", "is_24h", "is_on_duty")
    list_filter = ("status", "city", "is_24h", "is_on_duty")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "code", "address")


admin.site.register(PharmacyDocument)
admin.site.register(PharmacyReview)
