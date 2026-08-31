from django.contrib import admin

from catalog.models import Category, Favorite, Medicine, PharmacyStock, StockInventoryLine, StockInventorySession, StockMovement


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ("name", "dosage", "dci", "form", "category", "is_featured", "is_top")
    list_filter = ("form", "category", "is_featured", "requires_prescription")
    prepopulated_fields = {"slug": ("name", "dosage")}
    search_fields = ("name", "dci", "laboratory")


@admin.register(PharmacyStock)
class PharmacyStockAdmin(admin.ModelAdmin):
    list_display = ("medicine", "pharmacy", "quantity", "price", "updated_at")
    list_filter = ("pharmacy",)
    search_fields = ("medicine__name", "pharmacy__name")


admin.site.register(Favorite)


class StockInventoryLineInline(admin.TabularInline):
    model = StockInventoryLine
    extra = 0
    readonly_fields = ("stock", "expected_quantity", "counted_quantity", "note")


@admin.register(StockInventorySession)
class StockInventorySessionAdmin(admin.ModelAdmin):
    list_display = ("pharmacy", "status", "lines_total", "lines_counted", "variance_lines", "created_at")
    list_filter = ("status", "pharmacy")
    inlines = [StockInventoryLineInline]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("stock", "movement_type", "quantity_delta", "quantity_after", "created_at", "created_by")
    list_filter = ("movement_type",)
    search_fields = ("stock__medicine__name", "note")
