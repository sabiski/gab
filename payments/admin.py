from django.contrib import admin

from payments.models import (
    CourierEarning,
    InsuranceClaim,
    InsuranceProvider,
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


admin.site.register(Subscription)
admin.site.register(InsuranceProvider)
admin.site.register(InsuranceClaim)
