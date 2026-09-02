from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from api import views

router = DefaultRouter()
router.register(r"pharmacies", views.PharmacyViewSet, basename="pharmacy")
router.register(r"medicines", views.MedicineViewSet, basename="medicine")
router.register(r"categories", views.CategoryViewSet, basename="category")
router.register(r"orders", views.OrderViewSet, basename="order")
router.register(r"deliveries", views.DeliveryViewSet, basename="delivery")
router.register(r"notifications", views.NotificationViewSet, basename="notification")
router.register(r"favorites", views.FavoriteViewSet, basename="favorite")
router.register(r"prescriptions", views.PrescriptionViewSet, basename="prescription")

urlpatterns = [
    path("health/", views.api_health, name="api-health"),
    # Auth (Flutter / PWA)
    path("auth/login/", views.LoginView.as_view(), name="api-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="api-refresh"),
    path("auth/register/", views.RegisterView.as_view(), name="api-register"),
    path("users/me/", views.MeView.as_view(), name="api-me"),
    # Recherche & disponibilité
    path("medicines/availability/", views.StockSearchView.as_view(), name="api-stock-search"),
    path("pharmacies/search/", views.PharmacyViewSet.as_view({"get": "list"}), name="api-pharmacy-search"),
    path("medicines/search/", views.MedicineViewSet.as_view({"get": "search"}), name="api-medicine-search"),
    # Paiements
    path("payments/charge/", views.PaymentChargeView.as_view(), name="api-payment-charge"),
    path("payments/checkout/", views.PaymentCheckoutView.as_view(), name="api-payment-checkout"),
    path(
        "payments/status/<str:reference>/",
        views.PaymentStatusView.as_view(),
        name="api-payment-status",
    ),
    path(
        "payments/ebilling/callback/",
        views.EbillingCallbackView.as_view(),
        name="api-ebilling-callback",
    ),
    path("", include(router.urls)),
]
