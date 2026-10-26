from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from catalog.models import Medicine, PharmacyStock, StockMovement
from core.counter_sales import CounterSaleError, SaleLine, cancel_counter_sale, create_counter_sale
from orders.models import CounterSale
from payments.models import InsuranceProvider
from pharmacies.models import Pharmacy


class TillFixture(TestCase):
    """Pharmacie, deux produits en stock (dont un sur ordonnance)."""

    def setUp(self):
        self.owner = User.objects.create_user("vc_owner", password="x", role=User.Role.PHARMACIST)
        self.pharmacy = Pharmacy.objects.create(
            owner=self.owner, code="PH-VC", name="Pharmacie Caisse", slug="pharmacie-caisse",
            phone="0", address="x", status=Pharmacy.Status.ACTIVE,
        )
        self.doliprane = Medicine.objects.create(name="Doliprane 1000", slug="doliprane-vc")
        self.amox = Medicine.objects.create(
            name="Amoxicilline 500", slug="amox-vc", requires_prescription=True
        )
        self.s1 = PharmacyStock.objects.create(
            pharmacy=self.pharmacy, medicine=self.doliprane, price=1350, quantity=10
        )
        self.s2 = PharmacyStock.objects.create(
            pharmacy=self.pharmacy, medicine=self.amox, price=4000, quantity=3
        )

    def _sale(self, lines, **kw):
        return create_counter_sale(pharmacy=self.pharmacy, user=self.owner, lines=lines, **kw)


class CounterSaleTests(TillFixture):
    """Caisse hors app : stock, ordonnance attestée, tiers payant, annulation."""

    def test_sale_decrements_stock_and_logs_movement(self):
        sale = self._sale([SaleLine(self.s1.pk, 2)])
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.quantity, 8)
        self.assertEqual(sale.total, 2700)
        self.assertTrue(
            StockMovement.objects.filter(stock=self.s1, quantity_delta=-2, note__contains=sale.code).exists()
        )

    def test_cannot_sell_more_than_stock(self):
        with self.assertRaises(CounterSaleError):
            self._sale([SaleLine(self.s2.pk, 4)])
        self.s2.refresh_from_db()
        self.assertEqual(self.s2.quantity, 3)

    def test_prescription_drug_requires_attestation(self):
        with self.assertRaises(CounterSaleError):
            self._sale([SaleLine(self.s2.pk, 1)])
        sale = self._sale([SaleLine(self.s2.pk, 1)], prescription_checked=True)
        self.assertTrue(sale.prescription_checked)

    def test_insurance_third_party_and_discount(self):
        provider = InsuranceProvider.objects.create(name="CNAMGS", code="CN-VC")
        sale = self._sale(
            [SaleLine(self.s1.pk, 4)],
            payment_method=CounterSale.PaymentMethod.INSURANCE,
            insurance_provider=provider,
            insurance_amount=4000,
            discount=400,
            customer_name="Awa N.",
            insured_number="cn-12345",
        )
        self.assertEqual(sale.insured_number, "CN-12345")
        self.assertEqual(sale.subtotal, 5400)
        self.assertEqual(sale.insurance_amount, 4000)
        self.assertEqual(sale.total, 1000)

    def test_credit_requires_customer(self):
        with self.assertRaises(CounterSaleError):
            self._sale([SaleLine(self.s1.pk, 1)], payment_method=CounterSale.PaymentMethod.CREDIT)

    def test_cancel_restores_stock(self):
        sale = self._sale([SaleLine(self.s1.pk, 3)])
        cancel_counter_sale(sale, user=self.owner, reason="Erreur de saisie")
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.quantity, 10)
        self.assertEqual(sale.status, CounterSale.Status.CANCELLED)
        with self.assertRaises(CounterSaleError):
            cancel_counter_sale(sale, user=self.owner, reason="encore")

    def test_counter_pages_and_post(self):
        self.client.force_login(self.owner)
        url = reverse("bo_pharmacy_counter")
        self.assertEqual(self.client.get(url).status_code, 200)
        resp = self.client.post(url, {
            "stock_id": [self.s1.pk], "quantity": [2],
            "channel": "phone", "payment_method": "cash",
        })
        sale = CounterSale.objects.get()
        self.assertRedirects(resp, f"{url}?done={sale.pk}", fetch_redirect_response=False)
        self.assertEqual(sale.channel, "phone")
        self.assertEqual(self.client.get(resp["Location"]).status_code, 200)
        self.assertEqual(self.client.get(reverse("bo_pharmacy_counter_history")).status_code, 200)
        csv_resp = self.client.get(reverse("bo_pharmacy_counter_history") + "?export=csv")
        self.assertIn(sale.code, csv_resp.content.decode("utf-8"))
        self.assertEqual(
            self.client.get(reverse("bo_pharmacy_counter_receipt", args=[sale.pk])).status_code, 200
        )

    def test_other_pharmacy_cannot_see_receipt(self):
        sale = self._sale([SaleLine(self.s1.pk, 1)])
        other = User.objects.create_user("vc_other", password="x", role=User.Role.PHARMACIST)
        Pharmacy.objects.create(
            owner=other, code="PH-VC2", name="Autre", slug="autre-vc",
            phone="0", address="x", status=Pharmacy.Status.ACTIVE,
        )
        self.client.force_login(other)
        resp = self.client.get(reverse("bo_pharmacy_counter_receipt", args=[sale.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_pharmacy_dashboard_requires_pharmacist(self):
        url = reverse("bo_pharmacy_dashboard")
        self.assertEqual(self.client.get(url).status_code, 302)
        patient = User.objects.create_user("vc_patient", password="x", role=User.Role.CLIENT)
        self.client.force_login(patient)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.owner)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CA hors app du jour")


class OfflineTillTests(TillFixture):
    """Caisse hors connexion : synchro JSON idempotente, vente déjà réalisée acceptée."""

    def _api(self, payload):
        import json

        self.client.force_login(self.owner)
        return self.client.post(
            reverse("bo_pharmacy_counter_api"), data=json.dumps(payload), content_type="application/json"
        )

    def _payload(self, **kw):
        import uuid

        data = {
            "uuid": str(uuid.uuid4()),
            "lines": [{"stock_id": self.s1.pk, "quantity": 2}],
            "channel": "counter",
            "payment_method": "cash",
        }
        data.update(kw)
        return data

    def test_same_uuid_creates_one_sale(self):
        payload = self._payload(offline=True, created_at="2026-10-04T08:15:00Z")
        r1 = self._api(payload)
        r2 = self._api(payload)  # renvoi après coupure réseau
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["sale_id"], r2.json()["sale_id"])
        self.assertEqual(CounterSale.objects.count(), 1)
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.quantity, 8)
        sale = CounterSale.objects.get()
        self.assertTrue(sale.recorded_offline)
        self.assertEqual(sale.sold_at.hour, 8)

    def test_offline_sale_beyond_stock_is_accepted_and_flagged(self):
        r = self._api(self._payload(offline=True, lines=[{"stock_id": self.s2.pk, "quantity": 5}],
                                    prescription_checked=True))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Stock insuffisant", r.json()["warning"])
        self.s2.refresh_from_db()
        self.assertEqual(self.s2.quantity, 0)

    def test_online_sale_beyond_stock_is_refused(self):
        r = self._api(self._payload(lines=[{"stock_id": self.s2.pk, "quantity": 5}], prescription_checked=True))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(CounterSale.objects.exists())

    def test_service_worker_is_scoped_to_till(self):
        self.client.force_login(self.owner)
        r = self.client.get(reverse("bo_pharmacy_counter_sw"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Service-Worker-Allowed"], reverse("bo_pharmacy_counter"))
        self.assertIn("javascript", r["Content-Type"])


class InsuranceStatementTests(TillFixture):
    """Relevé mensuel de tiers payant : sources, figeage à l'envoi, règlements."""

    def setUp(self):
        super().setUp()
        self.provider = InsuranceProvider.objects.create(name="CNAMGS", code="CNAMGS", coverage_rate=80)

    def _insured_sale(self, qty=2, amount=2000):
        return self._sale(
            [SaleLine(self.s1.pk, qty)],
            payment_method=CounterSale.PaymentMethod.INSURANCE,
            insurance_provider=self.provider,
            insurance_amount=amount,
            customer_name="Jean M.",
            insured_number="A-001",
        )

    def test_statement_includes_counter_and_app_orders(self):
        from django.utils import timezone

        from core.insurance_statements import generate_statement, statement_lines
        from orders.models import Order

        self._insured_sale()
        patient = User.objects.create_user("st_patient", password="x", role=User.Role.CLIENT)
        Order.objects.create(
            client=patient, pharmacy=self.pharmacy, status=Order.Status.DELIVERED,
            insurance_provider=self.provider, insurance_coverage=3000, subtotal=4000,
        )
        Order.objects.create(  # annulée : exclue
            client=patient, pharmacy=self.pharmacy, status=Order.Status.CANCELLED,
            insurance_provider=self.provider, insurance_coverage=9999,
        )
        period = timezone.localdate()
        self.assertEqual(len(statement_lines(self.pharmacy, self.provider, period)), 2)
        st = generate_statement(self.pharmacy, self.provider, period, user=self.owner)
        self.assertEqual((st.counter_total, st.order_total, st.total), (2000, 3000, 5000))

    def test_sent_statement_is_frozen_and_payments_tracked(self):
        from django.utils import timezone

        from core.insurance_statements import generate_statement, mark_sent, record_payment

        self._insured_sale()
        period = timezone.localdate()
        st = generate_statement(self.pharmacy, self.provider, period, user=self.owner)
        with self.assertRaises(ValueError):
            record_payment(st, 1000)  # pas encore envoyé
        mark_sent(st)
        self._insured_sale(qty=1, amount=1000)
        with self.assertRaises(ValueError):
            generate_statement(self.pharmacy, self.provider, period, user=self.owner)
        record_payment(st, 1500)
        self.assertEqual(st.status, st.Status.PARTIAL)
        record_payment(st, 9999)
        self.assertEqual(st.status, st.Status.PAID)
        self.assertEqual(st.paid_amount, 2000)

    def test_statement_pages_and_pdf(self):
        self._insured_sale()
        self.client.force_login(self.owner)
        url = reverse("bo_pharmacy_statements")
        r = self.client.get(url)
        self.assertContains(r, "CNAMGS")
        r = self.client.post(url, {"action": "generate", "provider_id": self.provider.pk})
        st = self.provider.statements.get()
        detail = reverse("bo_pharmacy_statement_detail", args=[st.pk])
        self.assertRedirects(r, detail, fetch_redirect_response=False)
        self.assertContains(self.client.get(detail), "A-001")
        pdf = self.client.get(detail + "?format=pdf")
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("Jean M.", self.client.get(detail + "?format=csv").content.decode("utf-8"))

    def test_insurance_sale_requires_insured_number(self):
        with self.assertRaises(CounterSaleError):
            self._sale(
                [SaleLine(self.s1.pk, 1)],
                payment_method=CounterSale.PaymentMethod.INSURANCE,
                insurance_provider=self.provider,
                insurance_amount=500,
                customer_name="Sans numéro",
            )


class InsurerDashboardTests(TillFixture):
    """Tableau de bord assureur : chiffres réels, tendances, file de travail, alertes."""

    def setUp(self):
        super().setUp()
        from datetime import timedelta

        from django.utils import timezone

        from accounts.models import PartnerProfile
        from orders.models import Order
        from payments.models import CoverageRequest, InsuranceClaim

        self.provider = InsuranceProvider.objects.create(name="NSIA", code="NSIA-T", coverage_rate=70, annual_cap=10000)
        self.partner_user = User.objects.create_user("ins_t", password="x", role=User.Role.PARTNER)
        self.partner = PartnerProfile.objects.create(
            user=self.partner_user, partner_type=PartnerProfile.PartnerType.INSURER,
            organization_name="NSIA", insurance_provider=self.provider, validated_at=timezone.now(),
        )
        patient = User.objects.create_user("ins_patient", password="x", role=User.Role.CLIENT)
        now = timezone.now()
        order = Order.objects.create(client=patient, pharmacy=self.pharmacy, status=Order.Status.DELIVERED)
        # Demande en attente depuis 3 jours (hors délai)
        late = InsuranceClaim.objects.create(client=patient, provider=self.provider, order=order, amount=5000)
        InsuranceClaim.objects.filter(pk=late.pk).update(created_at=now - timedelta(days=3))
        # PC acceptée en 2 h, PC refusée
        pc = CoverageRequest.objects.create(
            reference="PC-INS1", client=patient, provider=self.provider, pharmacy=self.pharmacy,
            insured_number="A1", status=CoverageRequest.Status.ACCEPTED, insurance_amount=9500,
        )
        CoverageRequest.objects.filter(pk=pc.pk).update(created_at=now - timedelta(hours=5), accepted_at=now - timedelta(hours=3))
        CoverageRequest.objects.create(
            reference="PC-INS2", client=patient, provider=self.provider, pharmacy=self.pharmacy,
            insured_number="A1", status=CoverageRequest.Status.REJECTED,
        )
        # Tiers payant au comptoir
        self._sale(
            [SaleLine(self.s1.pk, 2)], payment_method=CounterSale.PaymentMethod.INSURANCE,
            insurance_provider=self.provider, insurance_amount=1500, customer_name="B", insured_number="B2",
        )

    def _build(self, period="30"):
        from core.insurer_dashboard import InsurerDashboard
        from core.insurer_profile import InsurerPortalProfile

        return InsurerDashboard(InsurerPortalProfile.from_partner(self.partner), period).build()

    def test_kpis_use_all_sources(self):
        w = self._build()
        kpis = {k["label"]: k for k in w["kpis"]}
        self.assertEqual(kpis["Dossiers reçus"]["value"], "3")  # 1 demande + 2 PC
        self.assertEqual(kpis["À traiter"]["value"], "1")
        self.assertIn("1 hors délai", kpis["À traiter"]["hint"])
        self.assertEqual(kpis["Taux d'acceptation"]["value"], "50,0 %")
        self.assertEqual(kpis["Montant engagé"]["value"], "11 000 F")  # 9 500 PC + 1 500 comptoir
        self.assertEqual(w["engaged"]["counter"], 1500)

    def test_queue_alerts_and_rankings(self):
        w = self._build()
        self.assertTrue(w["queue"]["items"][0]["late"])
        texts = " ".join(a["text"] for a in w["alerts"])
        self.assertIn("plus de 48 h", texts)
        self.assertIn("plafond annuel", texts)  # 9 500 / 10 000 F
        self.assertEqual(w["top_pharmacies"][0]["name"], "Pharmacie Caisse")
        self.assertEqual(w["top_pharmacies"][0]["amount"], 11000)
        self.assertEqual(w["top_medicines"][0]["name"], "Doliprane 1000")
        steps = {s["label"]: s["done"] for s in (w["onboarding"] or [])}
        self.assertTrue(steps.get("Organisme rattaché", True))

    def test_trend_is_real_not_hardcoded(self):
        w = self._build("7")
        received = next(k for k in w["kpis"] if k["label"] == "Dossiers reçus")
        self.assertEqual(received["trend"]["label"], "Nouveau")  # rien sur la période précédente

    def test_pending_partner_sees_onboarding(self):
        self.partner.validated_at = None
        self.partner.save()
        steps = self._build()["onboarding"]
        self.assertFalse(next(s for s in steps if s["label"] == "Validation par Gab'Pharma")["done"])

    def test_dashboard_page_renders_each_period(self):
        from accounts.models import PartnerSubscription
        from core.partner_subscription import create_partner_subscription

        create_partner_subscription(self.partner, PartnerSubscription.Plan.PROFESSIONAL, activate=True,
                                    payment_reference="T", payment_method="transfer")
        self.client.force_login(self.partner_user)
        for p in ["7", "30", "90", "365"]:
            r = self.client.get(reverse("bo_insurer_dashboard") + f"?periode={p}")
            self.assertContains(r, "À traiter maintenant")
            self.assertContains(r, "PC-INS1")
