from django.contrib import admin

from payments.models import (
    CoverageAccessLog,
    CoverageInvoice,
    CoverageRequest,
    CoverageRequestEvent,
    CourierEarning,
    InsuranceClaim,
    InsuranceProvider,
    InsuranceSubscriptionPlan,
    OrderSettlement,
    PatientAccessPurchase,
    Payment,
    PlatformPaymentSettings,
    Subscription,
)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("reference", "order", "method", "amount", "status", "is_deposit", "created_at")
    list_filter = ("method", "status")


@admin.register(PlatformPaymentSettings)
class PlatformPaymentSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "platform_commission_rate",
        "daily_transaction_cap",
        "payout_delay_days",
        "courier_base_fee",
    )


@admin.register(OrderSettlement)
class OrderSettlementAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "pharmacy_net",
        "platform_commission",
        "courier_earning",
        "pharmacy_payout_status",
        "payout_due_at",
    )
    list_filter = ("pharmacy_payout_status", "courier_payout_status")


@admin.register(CourierEarning)
class CourierEarningAdmin(admin.ModelAdmin):
    list_display = ("courier", "delivery", "total", "status", "created_at")
    list_filter = ("status",)


@admin.register(PatientAccessPurchase)
class PatientAccessPurchaseAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "plan", "amount", "status", "purchased_at", "expires_at")
    list_filter = ("plan", "status", "payment_method")


@admin.register(CoverageRequest)
class CoverageRequestAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "client",
        "provider",
        "pharmacy",
        "status",
        "estimated_amount",
        "insurance_amount",
        "patient_amount",
        "created_at",
    )
    list_filter = ("status", "provider")
    search_fields = ("reference", "insured_number", "client__email")


@admin.register(CoverageInvoice)
class CoverageInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "coverage_request",
        "total",
        "insurance_share",
        "patient_share",
        "payment_status",
        "created_at",
    )
    list_filter = ("payment_status",)


@admin.register(InsuranceSubscriptionPlan)
class InsuranceSubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "code", "premium_amount", "coverage_rate", "is_active")
    list_filter = ("is_active", "provider")


admin.site.register(Subscription)
admin.site.register(InsuranceProvider)
admin.site.register(InsuranceClaim)
admin.site.register(CoverageRequestEvent)
admin.site.register(CoverageAccessLog)
