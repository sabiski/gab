from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from accounts.models import User
from catalog.models import Category, Favorite, Medicine, PharmacyStock
from deliveries.models import Delivery, DeliveryIncident
from notifications.models import Notification
from orders.models import Order, OrderItem, Prescription
from payments.models import Payment
from pharmacies.models import Pharmacy


class UserSerializer(serializers.ModelSerializer):
    display_location = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "role",
            "status",
            "city",
            "district",
            "display_location",
            "loyalty_points",
            "avatar",
            "is_phone_verified",
            "is_email_verified",
        )
        read_only_fields = ("role", "status", "loyalty_points")


class RegisterSerializer(serializers.ModelSerializer):
    """Inscription API : patients uniquement (username auto depuis e-mail)."""

    password = serializers.CharField(write_only=True, min_length=8)
    email = serializers.EmailField(required=True)

    class Meta:
        model = User
        fields = ("email", "password", "first_name", "last_name", "phone", "city")

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Cet e-mail est déjà utilisé.")
        return value

    def create(self, validated_data):
        from accounts.models import ClientProfile
        from django.utils.text import slugify

        password = validated_data.pop("password")
        email = validated_data["email"]
        local = email.split("@")[0].strip().lower()
        base = slugify(local).replace("-", "_")[:40] or "patient"
        base = "".join(c for c in base if c.isalnum() or c in "._")[:40] or "patient"
        username, i = base, 1
        while User.objects.filter(username=username).exists():
            username = f"{base}{i}"
            i += 1
        user = User(
            **validated_data,
            username=username,
            role=User.Role.CLIENT,
            status=User.Status.ACTIVE,
        )
        user.set_password(password)
        user.save()
        ClientProfile.objects.get_or_create(user=user)
        return user


class GabPharmaTokenSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["username"] = user.username
        return token

    def validate(self, attrs):
        login_id = attrs.get(self.username_field, "")
        if "@" in str(login_id):
            match = User.objects.filter(email__iexact=login_id).first()
            if match:
                attrs[self.username_field] = match.username
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data


class PharmacySerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(read_only=True)

    class Meta:
        model = Pharmacy
        fields = (
            "id",
            "code",
            "name",
            "slug",
            "description",
            "phone",
            "address",
            "city",
            "district",
            "latitude",
            "longitude",
            "logo",
            "logo_color",
            "status",
            "status_label",
            "is_24h",
            "is_on_duty",
            "opening_hours",
            "rating",
            "review_count",
            "compliance_score",
        )


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug", "icon", "color", "bg_color", "order")


class MedicineSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    form_label = serializers.CharField(read_only=True)

    class Meta:
        model = Medicine
        fields = (
            "id",
            "name",
            "slug",
            "dci",
            "laboratory",
            "form",
            "form_label",
            "dosage",
            "description",
            "category",
            "image",
            "requires_prescription",
            "is_featured",
            "is_top",
        )


class PharmacyStockSerializer(serializers.ModelSerializer):
    medicine = MedicineSerializer(read_only=True)
    pharmacy = PharmacySerializer(read_only=True)
    availability = serializers.CharField(read_only=True)
    availability_label = serializers.CharField(read_only=True)
    formatted_price = serializers.CharField(read_only=True)
    display_price = serializers.IntegerField(read_only=True)

    class Meta:
        model = PharmacyStock
        fields = (
            "id",
            "medicine",
            "pharmacy",
            "quantity",
            "price",
            "promotional_price",
            "display_price",
            "formatted_price",
            "availability",
            "availability_label",
            "expiry_date",
            "lot_number",
        )


class OrderItemSerializer(serializers.ModelSerializer):
    line_total = serializers.IntegerField(read_only=True)

    class Meta:
        model = OrderItem
        fields = ("id", "medicine", "medicine_name", "quantity", "unit_price", "line_total")


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    pharmacy = PharmacySerializer(read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "code",
            "status",
            "delivery_mode",
            "pharmacy",
            "items",
            "subtotal",
            "delivery_fee",
            "discount",
            "insurance_coverage",
            "deposit_amount",
            "total",
            "notes",
            "is_urgent",
            "delivery_address",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("code", "validation_code")


class OrderCreateSerializer(serializers.Serializer):
    pharmacy_id = serializers.IntegerField()
    delivery_mode = serializers.ChoiceField(choices=Order.DeliveryMode.choices)
    delivery_address = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    items = serializers.ListField(
        child=serializers.DictField(child=serializers.IntegerField()),
        min_length=1,
    )


class DeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = Delivery
        fields = (
            "id",
            "order",
            "courier",
            "status",
            "distance_km",
            "estimated_minutes",
            "courier_lat",
            "courier_lng",
            "picked_up_at",
            "delivered_at",
        )


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "id",
            "order",
            "method",
            "amount",
            "status",
            "reference",
            "is_deposit",
            "created_at",
        )


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            "id",
            "title",
            "message",
            "notification_type",
            "channel",
            "is_read",
            "data",
            "created_at",
        )


class FavoriteSerializer(serializers.ModelSerializer):
    medicine = MedicineSerializer(source="stock.medicine", read_only=True)
    stock_id = serializers.IntegerField(source="stock.id", read_only=True)
    pharmacy_name = serializers.CharField(source="stock.pharmacy.name", read_only=True)

    class Meta:
        model = Favorite
        fields = ("id", "stock_id", "medicine", "pharmacy_name", "created_at")


class PrescriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Prescription
        fields = ("id", "file", "doctor_name", "status", "notes", "created_at")


class DeliveryIncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryIncident
        fields = (
            "id",
            "delivery",
            "incident_type",
            "description",
            "photo",
            "priority",
            "status",
            "created_at",
        )
