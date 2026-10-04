import shutil
import tempfile
from datetime import timedelta
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import User
from core.field_crypto import encrypt_uploaded_file
from core.prescription_privacy import run_purge
from notifications.models import Notification
from orders.models import Order, Prescription, PrescriptionAccessLog
from payments.models import CoverageRequest, InsuranceProvider
from pharmacies.models import Pharmacy

TEST_MEDIA = tempfile.mkdtemp(prefix="gp-test-media-")


def _png_bytes():
    buf = BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, "PNG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class PrescriptionPrivacyTests(TestCase):
    """Accès aux ordonnances : patient, pharmacie choisie, accès exceptionnel, purge."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA, ignore_errors=True)

    def setUp(self):
        self.patient = User.objects.create_user("rx_patient", password="x", role=User.Role.CLIENT)
        self.pharmacist = User.objects.create_user("rx_pharma", password="x", role=User.Role.PHARMACIST)
        self.other_pharmacist = User.objects.create_user("rx_other", password="x", role=User.Role.PHARMACIST)
        self.admin = User.objects.create_user("rx_admin", password="x", role=User.Role.ADMIN)
        self.pharmacy = Pharmacy.objects.create(
            owner=self.pharmacist, code="PH-RX1", name="Pharmacie Rx", slug="pharmacie-rx",
            phone="0", address="x", status=Pharmacy.Status.ACTIVE,
        )
        Pharmacy.objects.create(
            owner=self.other_pharmacist, code="PH-RX2", name="Autre", slug="autre",
            phone="0", address="x", status=Pharmacy.Status.ACTIVE,
        )
        self.png = _png_bytes()
        self.rx = Prescription.objects.create(
            client=self.patient,
            pharmacy=self.pharmacy,
            file=encrypt_uploaded_file(SimpleUploadedFile("rx.png", self.png, "image/png")),
            status=Prescription.Status.PENDING,
        )
        self.url = reverse("bo_prescription_file", args=[self.rx.pk])

    def _get(self, user, url=None):
        self.client.force_login(user)
        return self.client.get(url or self.url)

    def test_patient_sees_original_without_log(self):
        resp = self._get(self.patient)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), self.png)
        self.assertFalse(PrescriptionAccessLog.objects.exists())

    def test_pharmacy_sees_watermarked_copy_and_is_logged_once(self):
        resp = self._get(self.pharmacist)
        self.assertEqual(resp.status_code, 200)
        self.assertNotEqual(b"".join(resp.streaming_content), self.png)
        self._get(self.pharmacist)
        logs = PrescriptionAccessLog.objects.filter(prescription=self.rx)
        self.assertEqual(logs.count(), 1)
        self.assertIn("Pharmacie Rx", logs.first().actor_label)

    def test_other_pharmacy_gets_404(self):
        self.assertEqual(self._get(self.other_pharmacist).status_code, 404)

    def test_admin_needs_break_glass(self):
        resp = self._get(self.admin)
        self.assertEqual(resp.status_code, 302)
        bg_url = reverse("bo_prescription_break_glass", args=["rx", self.rx.pk])
        self.assertTrue(resp["Location"].startswith(bg_url))

        resp = self.client.post(bg_url, {"reason": "court", "next": self.url})
        self.assertEqual(resp.status_code, 200)  # motif trop court
        self.assertEqual(self._get(self.admin).status_code, 302)

        resp = self.client.post(
            bg_url, {"reason": "Réclamation du patient — ticket #42", "next": self.url}
        )
        self.assertRedirects(resp, self.url, fetch_redirect_response=False)
        self.assertEqual(self._get(self.admin).status_code, 200)
        self.assertTrue(
            Notification.objects.filter(user=self.patient, title__icontains="exceptionnel").exists()
        )

    def test_break_glass_expires(self):
        PrescriptionAccessLog.objects.create(
            prescription=self.rx, user=self.admin,
            action=PrescriptionAccessLog.Action.BREAK_GLASS, reason="Ancien accès expiré",
        )
        PrescriptionAccessLog.objects.update(created_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(self._get(self.admin).status_code, 302)

    def test_purge_after_delivery_keeps_fingerprint(self):
        order = Order.objects.create(
            client=self.patient, pharmacy=self.pharmacy,
            status=Order.Status.DELIVERED, linked_prescription=self.rx,
            prescription=self.rx.file.name,
        )
        name = self.rx.file.name
        storage = self.rx.file.storage

        self.assertEqual(run_purge()["prescriptions"], 0)  # livrée trop récemment

        Order.objects.filter(pk=order.pk).update(updated_at=timezone.now() - timedelta(days=8))
        result = run_purge()
        self.assertEqual(result["prescriptions"], 1)
        self.assertEqual(result["order_copies"], 1)

        self.rx.refresh_from_db()
        self.assertTrue(self.rx.is_purged)
        self.assertEqual(len(self.rx.file_sha256), 64)
        self.assertFalse(storage.exists(name))
        self.assertEqual(self._get(self.patient).status_code, 404)

    def test_active_order_is_never_purged(self):
        order = Order.objects.create(
            client=self.patient, pharmacy=self.pharmacy,
            status=Order.Status.PREPARING, linked_prescription=self.rx,
        )
        Order.objects.filter(pk=order.pk).update(updated_at=timezone.now() - timedelta(days=60))
        self.assertEqual(run_purge()["prescriptions"], 0)

    def test_closed_coverage_prescription_is_purged(self):
        provider = InsuranceProvider.objects.create(name="CNAMGS", code="CN-RX")
        req = CoverageRequest.objects.create(
            reference="PC-TEST01", client=self.patient, provider=provider,
            pharmacy=self.pharmacy, insured_number="A1",
            prescription_file=encrypt_uploaded_file(SimpleUploadedFile("pc.png", self.png, "image/png")),
            status=CoverageRequest.Status.COMPLETED,
        )
        CoverageRequest.objects.filter(pk=req.pk).update(updated_at=timezone.now() - timedelta(days=31))
        self.assertEqual(run_purge()["coverage"], 1)
        req.refresh_from_db()
        self.assertFalse(req.prescription_file)
        self.assertIsNotNone(req.prescription_purged_at)
        self.assertEqual(len(req.prescription_sha256), 64)
