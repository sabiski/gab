from django.contrib import admin

from django.contrib.auth.admin import UserAdmin as BaseUserAdmin



from accounts.models import (

    ClientProfile,

    CourierProfile,

    LoyaltyProgramSettings,

    LoyaltyReward,

    LoyaltyTransaction,

    LoyaltyVoucher,

    User,

)





@admin.register(User)

class UserAdmin(BaseUserAdmin):

    list_display = ("username", "email", "role", "status", "phone", "city", "is_staff")

    list_filter = ("role", "status", "is_staff")

    fieldsets = BaseUserAdmin.fieldsets + (

        (

            "Gab'Pharma",

            {

                "fields": (

                    "role",

                    "phone",

                    "avatar",

                    "status",

                    "is_phone_verified",

                    "is_email_verified",

                    "city",

                    "district",

                    "latitude",

                    "longitude",

                    "loyalty_points",

                )

            },

        ),

    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (

        (None, {"fields": ("role", "phone", "city")}),

    )





admin.site.register(ClientProfile)

admin.site.register(CourierProfile)





@admin.register(LoyaltyProgramSettings)

class LoyaltyProgramSettingsAdmin(admin.ModelAdmin):

    def has_add_permission(self, request):

        return not LoyaltyProgramSettings.objects.exists()





@admin.register(LoyaltyReward)

class LoyaltyRewardAdmin(admin.ModelAdmin):

    list_display = ("label", "points_cost", "reward_type", "value", "is_active", "sort_order")

    list_filter = ("reward_type", "is_active")





@admin.register(LoyaltyTransaction)

class LoyaltyTransactionAdmin(admin.ModelAdmin):

    list_display = ("user", "kind", "points", "balance_after", "created_at", "expires_at")

    list_filter = ("kind",)

    search_fields = ("user__username", "reason")





@admin.register(LoyaltyVoucher)

class LoyaltyVoucherAdmin(admin.ModelAdmin):

    list_display = ("code", "user", "reward", "status", "expires_at", "created_at")

    list_filter = ("status",)

    search_fields = ("code", "user__username")

