from django.contrib import admin

from orders.models import Order, OrderEvaluation, OrderItem, Prescription


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("code", "client", "pharmacy", "status", "total", "created_at")
    list_filter = ("status", "delivery_mode", "is_urgent")
    search_fields = ("code", "client__username")
    inlines = [OrderItemInline]


@admin.register(OrderEvaluation)
class OrderEvaluationAdmin(admin.ModelAdmin):
    list_display = ("order", "pharmacy_rating", "courier_rating", "created_at")
    list_filter = ("pharmacy_rating", "courier_rating")
    search_fields = ("order__code", "client__username")


admin.site.register(Prescription)
