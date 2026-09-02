from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.models import User
from api.serializers import (
    CategorySerializer,
    DeliverySerializer,
    FavoriteSerializer,
    GabPharmaTokenSerializer,
    MedicineSerializer,
    NotificationSerializer,
    OrderCreateSerializer,
    OrderSerializer,
    PaymentSerializer,
    PharmacySerializer,
    PharmacyStockSerializer,
    PrescriptionSerializer,
    RegisterSerializer,
    UserSerializer,
)
from catalog.models import Category, Favorite, Medicine, PharmacyStock
from deliveries.models import Delivery
from notifications.models import Notification
from orders.models import Order, OrderItem, normalize_validation_code
from payments.models import Payment
from pharmacies.models import Pharmacy


class IsOwnerOrAdmin(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user.role in (User.Role.ADMIN, User.Role.SUPERADMIN):
            return True
        owner = getattr(obj, "client", None) or getattr(obj, "user", None)
        return owner == request.user


class LoginView(TokenObtainPairView):
    serializer_class = GabPharmaTokenSerializer


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class PharmacyViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PharmacySerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"
    search_fields = ("name", "city", "district", "address")
    filterset_fields = ("city", "is_24h", "is_on_duty", "status")

    def get_queryset(self):
        qs = Pharmacy.objects.filter(status=Pharmacy.Status.ACTIVE)
        lat = self.request.query_params.get("lat")
        lng = self.request.query_params.get("lng")
        # Distance calc reserved for PostGIS — sorted by rating for now
        if lat and lng:
            qs = qs.order_by("-rating", "name")
        return qs

    @action(detail=False, methods=["get"])
    def nearby(self, request):
        qs = self.get_queryset().order_by("-rating")[:20]
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def on_duty(self, request):
        qs = self.get_queryset().filter(Q(is_on_duty=True) | Q(is_24h=True))
        return Response(self.get_serializer(qs, many=True).data)


class MedicineViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MedicineSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"
    search_fields = ("name", "dci", "laboratory", "dosage")
    filterset_fields = ("category", "form", "requires_prescription", "is_featured")

    def get_queryset(self):
        return Medicine.objects.select_related("category").all()

    @action(detail=False, methods=["get"])
    def search(self, request):
        q = request.query_params.get("q", "").strip()
        category = request.query_params.get("category")
        qs = self.get_queryset()
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(dci__icontains=q) | Q(laboratory__icontains=q)
            )
        if category:
            qs = qs.filter(Q(category__slug=category) | Q(category_id=category))
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(self.get_serializer(qs, many=True).data)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategorySerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"


class StockSearchView(APIView):
    """
    Recherche médicaments avec disponibilité (CDC: dispo visible après recherche explicite).
    GET /api/v1/medicines/availability/?q=Doliprane
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from core.patient_access import ensure_search_access, user_needs_paywall

        q = request.query_params.get("q", "").strip()
        if not q:
            return Response(
                {"detail": "Paramètre q requis pour afficher la disponibilité."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user_needs_paywall(request.user):
            return Response(
                {
                    "detail": "Forfait recherche requis (Pass 1h 500 F, Standard 2000 F/jour ou Premium).",
                    "code": "search_paywall",
                    "plans_url": "/forfaits/",
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        ensure_search_access(request.user, q)
        stocks = (
            PharmacyStock.objects.filter(
                is_visible=True,
                quantity__gt=0,
                pharmacy__status=Pharmacy.Status.ACTIVE,
            )
            .filter(
                Q(medicine__name__icontains=q)
                | Q(medicine__dci__icontains=q)
                | Q(medicine__laboratory__icontains=q)
            )
            .select_related("medicine", "medicine__category", "pharmacy")
            .order_by("price")
        )
        return Response(PharmacyStockSerializer(stocks, many=True).data)


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role in (User.Role.ADMIN, User.Role.SUPERADMIN):
            return Order.objects.prefetch_related("items").select_related("pharmacy")
        return (
            Order.objects.filter(client=user)
            .exclude(status=Order.Status.CART)
            .prefetch_related("items")
            .select_related("pharmacy")
        )

    def create(self, request, *args, **kwargs):
        ser = OrderCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        pharmacy = get_object_or_404(Pharmacy, pk=data["pharmacy_id"], status=Pharmacy.Status.ACTIVE)
        order = Order.objects.create(
            client=request.user,
            pharmacy=pharmacy,
            status=Order.Status.PENDING,
            delivery_mode=data["delivery_mode"],
            delivery_address=data.get("delivery_address", ""),
            notes=data.get("notes", ""),
        )
        for item in data["items"]:
            medicine = get_object_or_404(Medicine, pk=item["medicine_id"])
            stock = get_object_or_404(PharmacyStock, pharmacy=pharmacy, medicine=medicine)
            OrderItem.objects.create(
                order=order,
                medicine=medicine,
                quantity=item["quantity"],
                unit_price=stock.display_price,
                medicine_name=str(medicine),
            )
        order.recalculate_totals()
        # Acompte COD (CDC v1.1)
        if request.data.get("payment_method") == "cod":
            from core.payment_settlement import cod_deposit_amount

            order.deposit_amount = cod_deposit_amount(order.total)
            order.save(update_fields=["deposit_amount"])
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"])
    def status(self, request, pk=None):
        order = self.get_object()
        new_status = request.data.get("status")
        if new_status not in dict(Order.Status.choices):
            return Response({"detail": "Statut invalide"}, status=400)
        order.status = new_status
        order.save(update_fields=["status", "updated_at"])
        return Response(OrderSerializer(order).data)


class PaymentChargeView(APIView):
    """Alias mobile — délègue au flux E-Billing quand configuré."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        checkout = PaymentCheckoutView()
        checkout.request = request
        checkout.format_kwarg = None
        return checkout.post(request)


class PaymentCheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from core.payments_web import PaymentLimitError, create_order_payment
        from payments.ebilling import EbillingError

        order = get_object_or_404(Order, pk=request.data.get("order_id"), client=request.user)
        method = request.data.get("method", Payment.Method.MOOV)
        return_url = (request.data.get("return_url") or "").strip()
        if not return_url:
            return_url = request.build_absolute_uri(f"/commande/{order.code}/confirmation/")
        try:
            flow = create_order_payment(order, method, return_url=return_url)
        except (PaymentLimitError, EbillingError) as exc:
            return Response({"detail": str(exc)}, status=400)

        payment = flow.payment
        if not payment:
            return Response({"detail": "Aucun paiement à traiter."}, status=400)

        payload = PaymentSerializer(payment).data
        payload["pending_online"] = flow.pending_online
        if flow.redirect_url:
            payload["payment_url"] = flow.redirect_url
            provider = payment.provider_response or {}
            payload["bill_id"] = provider.get("ebilling_bill_id")
        return Response(payload, status=status.HTTP_201_CREATED)


class PaymentStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, reference):
        payment = get_object_or_404(
            Payment.objects.select_related("order"),
            reference=reference,
            order__client=request.user,
        )
        return Response(
            {
                "reference": payment.reference,
                "status": payment.status,
                "amount": payment.amount,
                "method": payment.method,
                "order_id": payment.order_id,
                "order_code": payment.order.code,
                "order_status": payment.order.status,
                "bill_id": (payment.provider_response or {}).get("ebilling_bill_id"),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class EbillingCallbackView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        import logging

        from payments.ebilling import verify_webhook_signature
        from payments.ebilling_handlers import apply_ebilling_callback

        logger = logging.getLogger("gabpharma.ebilling")
        raw_body = request.body
        header_map = {k: v for k, v in request.headers.items()}
        if not verify_webhook_signature(
            method="POST",
            path=request.path,
            raw_body=raw_body,
            headers=header_map,
        ):
            logger.warning("Callback E-Billing rejeté : signature invalide.")
            return Response({"detail": "Signature invalide."}, status=401)

        content_type = (request.content_type or "").lower()
        if "json" in content_type:
            payload = request.data
            if hasattr(payload, "dict"):
                payload = payload.dict()
        else:
            payload = request.POST.dict()

        ok, message = apply_ebilling_callback(payload)
        if not ok:
            if "introuvable" in message.lower():
                return Response({"success": False, "message": message}, status=404)
            return Response({"success": False, "message": message}, status=400)
        return Response({"success": True, "message": message})


class DeliveryViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DeliverySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.COURIER:
            return Delivery.objects.filter(courier=user)
        if user.role in (User.Role.ADMIN, User.Role.SUPERADMIN):
            return Delivery.objects.all()
        return Delivery.objects.filter(order__client=user)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        delivery = self.get_object()
        courier_id = request.data.get("courier_id")
        courier = get_object_or_404(User, pk=courier_id, role=User.Role.COURIER)
        delivery.courier = courier
        delivery.status = Delivery.Status.ASSIGNED
        delivery.save()
        return Response(DeliverySerializer(delivery).data)

    @action(detail=True, methods=["post"])
    def validate(self, request, pk=None):
        delivery = self.get_object()
        if delivery.status != Delivery.Status.IN_TRANSIT:
            return Response(
                {"detail": "La livraison doit être en cours chez le client."},
                status=400,
            )
        code = normalize_validation_code(request.data.get("code", ""))
        qr_raw = request.data.get("qr_payload") or request.data.get("client_qr_payload")
        if qr_raw:
            from core.order_traceability import resolve_courier_delivery_validation

            try:
                resolve_courier_delivery_validation(
                    delivery, qr_raw=qr_raw
                )
            except Exception as exc:
                from core.order_traceability import DeliveryValidationError
                if isinstance(exc, DeliveryValidationError):
                    return Response({"detail": str(exc)}, status=400)
                raise
        else:
            expected = normalize_validation_code(delivery.order.validation_code)
            if code != expected:
                return Response({"detail": "Code incorrect"}, status=400)
        from core.delivery_transfer import clear_pending_transfer
        from core.order_evaluation import handle_order_delivered

        delivery.status = Delivery.Status.DELIVERED
        delivery.delivered_at = timezone.now()
        clear_pending_transfer(delivery, save=False)
        delivery.save(
            update_fields=[
                "status",
                "delivered_at",
                "transfer_code",
                "transfer_requested_at",
                "handoff_courier",
                "updated_at",
            ]
        )
        delivery.order.status = Order.Status.DELIVERED
        delivery.order.save(update_fields=["status", "updated_at"])
        handle_order_delivered(delivery.order, delivery)
        return Response(
            {
                "detail": "Livraison validée",
                "delivery": DeliverySerializer(delivery).data,
            }
        )


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notif = self.get_object()
        notif.is_read = True
        notif.save(update_fields=["is_read"])
        return Response(NotificationSerializer(notif).data)


class FavoriteViewSet(viewsets.ModelViewSet):
    serializer_class = FavoriteSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return Favorite.objects.filter(user=self.request.user).select_related(
            "stock__medicine", "stock__pharmacy"
        )

    def create(self, request, *args, **kwargs):
        stock = get_object_or_404(PharmacyStock, pk=request.data.get("stock_id"))
        fav, _ = Favorite.objects.get_or_create(user=request.user, stock=stock)
        return Response(FavoriteSerializer(fav).data, status=201)


class PrescriptionViewSet(viewsets.ModelViewSet):
    serializer_class = PrescriptionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Prescription.objects.filter(client=self.request.user)

    def perform_create(self, serializer):
        serializer.save(client=self.request.user)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def api_health(request):
    return Response(
        {
            "status": "ok",
            "app": "Gab'Pharma",
            "version": "1.0.0",
            "api": "v1",
            "flutter_ready": True,
        }
    )
