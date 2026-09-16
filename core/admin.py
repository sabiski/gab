from django.contrib import admin

from core.models import (
    Advertisement,
    HealthCampaign,
    PharmacistTip,
    PharmacyBoostAd,
    PharmacyBoostPurchase,
    SiteHero,
)


@admin.register(SiteHero)
class SiteHeroAdmin(admin.ModelAdmin):
    list_display = ("eyebrow", "title", "is_active", "updated_at")


@admin.register(PharmacistTip)
class PharmacistTipAdmin(admin.ModelAdmin):
    list_display = ("title", "pharmacy", "category", "is_published", "published_at")
    list_filter = ("is_published", "category")
    search_fields = ("title", "excerpt")


@admin.register(Advertisement)
class AdvertisementAdmin(admin.ModelAdmin):
    list_display = ("title", "placement", "is_active", "priority", "updated_at")
    list_filter = ("placement", "is_active")


@admin.register(PharmacyBoostPurchase)
class PharmacyBoostPurchaseAdmin(admin.ModelAdmin):
    list_display = ("pharmacy", "plan", "amount", "status", "starts_at", "ends_at")
    list_filter = ("plan", "status")


@admin.register(PharmacyBoostAd)
class PharmacyBoostAdAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "pharmacy",
        "status",
        "show_popup",
        "boost_catalog",
        "priority",
        "impressions",
    )
    list_filter = ("status", "show_popup", "boost_catalog")
    search_fields = ("title", "pharmacy__name")


@admin.register(HealthCampaign)
class HealthCampaignAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "title",
        "theme",
        "status",
        "start_date",
        "end_date",
        "people_reached",
        "target_population",
    )
    list_filter = ("theme", "status")
    search_fields = ("code", "title", "partner")
