from django.contrib import admin

from deliveries.models import Delivery, DeliveryIncident, DeliveryStep


class DeliveryStepInline(admin.TabularInline):
    model = DeliveryStep
    extra = 0


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("order", "courier", "status", "distance_km", "created_at")
    list_filter = ("status",)
    inlines = [DeliveryStepInline]


admin.site.register(DeliveryIncident)
