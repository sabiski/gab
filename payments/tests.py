from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from core.coverage_requests import coverage_fee_for, create_coverage_request
from core.payment_settlement import (
    PaymentLimitError,
    calculate_courier_earning,
    check_daily_transaction_cap,
    courier_share,
    delivery_quote,
    delivery_tariff,
)
from deliveries.models import Delivery
from orders.models import Order
from payments.models import InsuranceProvider, PatientAccessPurchase, Payment, PlatformPaymentSettings
from pharmacies.models import Pharmacy


class BusinessModelTests(TestCase):
    """Modèle économique : 25 % livraison, Premium, frais de dossier PC."""

    def setUp(self):
        self.settings = PlatformPaymentSettings.load()
        self.client_user = User.objects.create_user("patient_t", password="x", role=User.Role.CLIENT)
        self.pharmacy = Pharmacy.objects.create(
            code="PH-T1",
            name="Pharmacie Test",
            slug="pharmacie-test",
            phone="000",
            address="Centre",
            latitude=Decimal("0.400000"),
            longitude=Decimal("9.450000"),
            status=Pharmacy.Status.ACTIVE,
        )
        self.provider = InsuranceProvider.objects.create(name="CNAMGS", code="CNAMGS-T")

    def _make_premium(self):
        PatientAccessPurchase.objects.create(
            user=self.client_user,
            plan=PatientAccessPurchase.Plan.PREMIUM,
            amount=5000,
            payment_method=Payment.Method.MOOV,
            status=PatientAccessPurchase.Status.ACTIVE,
            activated_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
        )

    def test_tariff_grows_with_distance_and_express(self):
        self.assertEqual(delivery_tariff(Order.DeliveryMode.PICKUP, 5), 0)
        self.assertEqual(delivery_tariff(Order.DeliveryMode.HOME), 1500)
        self.assertEqual(delivery_tariff(Order.DeliveryMode.HOME, 3), 2100)
        self.assertEqual(delivery_tariff(Order.DeliveryMode.EXPRESS, 3), 2600)
        # arrondi aux 50 F supérieurs
        self.assertEqual(delivery_tariff(Order.DeliveryMode.HOME, 1.1), 1750)

    def test_courier_gets_75_percent(self):
        self.assertEqual(courier_share(2000), 1500)
        self.assertEqual(courier_share(0), 0)

    def test_premium_free_delivery_above_threshold_only(self):
        self._make_premium()
        below = delivery_quote(self.client_user, Order.DeliveryMode.HOME, subtotal=4000)
        self.assertEqual(below["fee"], 1500)
        self.assertEqual(below["premium_missing"], 1000)
        above = delivery_quote(self.client_user, Order.DeliveryMode.HOME, subtotal=5000)
        self.assertEqual(above["fee"], 0)
        self.assertEqual(above["waived"], 1500)

    def test_courier_paid_even_when_premium_waives_fee(self):
        order = Order.objects.create(
            client=self.client_user,
            pharmacy=self.pharmacy,
            delivery_mode=Order.DeliveryMode.HOME,
            delivery_fee=0,
            delivery_fee_waived=2000,
        )
        delivery = Delivery.objects.create(order=order)
        earning = calculate_courier_earning(delivery)
        self.assertEqual(earning["total"], 1500)
        self.assertEqual(earning["platform_delivery_margin"], 0)

    def test_platform_keeps_25_percent_of_paid_delivery(self):
        order = Order.objects.create(
            client=self.client_user,
            pharmacy=self.pharmacy,
            delivery_mode=Order.DeliveryMode.HOME,
            delivery_fee=2000,
        )
        earning = calculate_courier_earning(Delivery.objects.create(order=order))
        self.assertEqual(earning["total"], 1500)
        self.assertEqual(earning["platform_delivery_margin"], 500)
        self.assertEqual(
            earning["base_fee"] + earning["distance_bonus"] + earning["express_bonus"],
            earning["total"],
        )

    def _create_pc(self, **kwargs):
        return create_coverage_request(
            client=self.client_user,
            provider=self.provider,
            pharmacy=self.pharmacy,
            insured_number="A123",
            request_type="pharmacy",
            desired_date=None,
            prescription_file=SimpleUploadedFile("rx.pdf", b"%PDF-1.4 test", "application/pdf"),
            prescription_date=None,
            doctor_name="",
            comment="",
            **kwargs,
        )

    def test_coverage_request_requires_fee_payment(self):
        with self.assertRaises(ValueError):
            self._create_pc()
        req = self._create_pc(payment_method=Payment.Method.AIRTEL)
        self.assertEqual(req.processing_fee, 500)
        self.assertFalse(req.fee_included_premium)
        self.assertTrue(req.fee_payment_reference)

    def test_premium_monthly_quota(self):
        self._make_premium()
        self.settings.premium_coverage_monthly_quota = 2
        self.settings.save()
        for _ in range(2):
            req = self._create_pc()
            self.assertEqual(req.processing_fee, 0)
            self.assertTrue(req.fee_included_premium)
        fee = coverage_fee_for(self.client_user)
        self.assertFalse(fee["included"])
        self.assertEqual(fee["fee"], 500)
        req = self._create_pc(payment_method=Payment.Method.MOOV)
        self.assertEqual(req.processing_fee, 500)

    def test_far_position_is_out_of_zone_and_not_billed_per_km(self):
        # Position en Europe (~6 000 km) : jamais 1 200 000 F de livraison
        quote = delivery_quote(
            self.client_user, Order.DeliveryMode.HOME,
            pharmacy=self.pharmacy, lat=48.85, lng=2.35, subtotal=1350,
        )
        self.assertTrue(quote["out_of_zone"])
        self.assertEqual(quote["fee"], 1500)
        near = delivery_quote(
            self.client_user, Order.DeliveryMode.HOME,
            pharmacy=self.pharmacy, lat=0.42, lng=9.46, subtotal=1350,
        )
        self.assertFalse(near["out_of_zone"])

    def test_cap_message_when_single_payment_exceeds_cap(self):
        with self.assertRaises(PaymentLimitError) as ctx:
            check_daily_transaction_cap(self.client_user, 1_209_150)
        msg = str(ctx.exception)
        self.assertIn("dépasse le plafond journalier de 500 000 FCFA", msg)
        self.assertNotIn("500,000", msg)
