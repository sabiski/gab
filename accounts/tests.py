import time

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import LoginAttempt, PlatformSettings, User
from core.security_middleware import SESSION_ACTIVITY_KEY


class LoginProtectionTests(TestCase):
    """Anti force brute, mots de passe de démo, expiration de session, en-têtes."""

    def setUp(self):
        ps = PlatformSettings.load()
        ps.two_factor_required = False
        ps.login_attempt_limit = 5
        ps.save()
        self.user = User.objects.create_user(
            "sec_user", email="sec@example.com", password="Correct-Horse-42", role=User.Role.CLIENT,
            status=User.Status.ACTIVE,
        )
        self.url = reverse("login")

    def _login(self, password, username="sec_user", **extra):
        return self.client.post(self.url, {"username": username, "password": password}, **extra)

    def test_account_locked_after_five_failures_even_with_right_password(self):
        for _ in range(5):
            self.assertContains(self._login("mauvais"), "incorrect")
        resp = self._login("Correct-Horse-42")
        self.assertContains(resp, "temporairement bloqué")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(self.user.notifications.filter(title__icontains="suspectes").exists())

    def test_success_resets_failure_counter(self):
        for _ in range(4):
            self._login("mauvais")
        self.assertEqual(self._login("Correct-Horse-42").status_code, 302)
        self.client.logout()
        for _ in range(4):
            self._login("mauvais")
        self.assertEqual(self._login("Correct-Horse-42").status_code, 302)

    def test_email_login_counts_against_same_account(self):
        for _ in range(5):
            self._login("mauvais", username="sec@example.com")
        self.assertContains(self._login("Correct-Horse-42"), "temporairement bloqué")

    def test_ip_blocked_after_many_failures_across_accounts(self):
        for i in range(40):
            LoginAttempt.objects.create(username=f"inconnu{i}", ip_address="203.0.113.9", success=False)
        resp = self._login("Correct-Horse-42", REMOTE_ADDR="203.0.113.9")
        self.assertContains(resp, "Trop de tentatives")

    def test_forged_forwarded_for_is_ignored(self):
        # Le client ajoute une fausse IP à gauche ; seule l'entrée du proxy compte.
        for i in range(40):
            LoginAttempt.objects.create(username=f"x{i}", ip_address="198.51.100.7", success=False)
        resp = self._login("Correct-Horse-42", HTTP_X_FORWARDED_FOR="203.0.113.50, 198.51.100.7")
        self.assertContains(resp, "Trop de tentatives")

    @override_settings(DEBUG=False)
    def test_demo_password_refused_in_production(self):
        demo = User.objects.create_user("demo_admin", password="admin123", role=User.Role.ADMIN)
        resp = self._login("admin123", username=demo.username)
        self.assertContains(resp, "désactivé en production")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_policy(self):
        for weak in ["Court1A", "toutminuscule1234", "admin123", "SansChiffreIci"]:
            with self.assertRaises(ValidationError):
                validate_password(weak)
        validate_password("Pharmacie-Libreville-2026")

    def test_sensitive_session_expires_after_inactivity(self):
        admin = User.objects.create_user("sec_admin", password="x", role=User.Role.ADMIN)
        self.client.force_login(admin)
        session = self.client.session
        session[SESSION_ACTIVITY_KEY] = int(time.time()) - 31 * 60
        session.save()
        resp = self.client.get(reverse("bo_admin_dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp["Location"])
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_client_session_has_no_idle_timeout(self):
        self.client.force_login(self.user)
        session = self.client.session
        session[SESSION_ACTIVITY_KEY] = int(time.time()) - 5 * 3600
        session.save()
        self.assertEqual(self.client.get(reverse("bo_client_dashboard")).status_code, 200)

    def test_security_headers(self):
        resp = self.client.get(self.url)
        self.assertIn("object-src 'none'", resp["Content-Security-Policy"])
        self.assertIn("geolocation=(self)", resp["Permissions-Policy"])
        self.assertEqual(resp["Cache-Control"], "no-store, private")

    @override_settings(MAX_UPLOAD_BYTES=1024)
    def test_oversized_request_rejected(self):
        resp = self.client.post(self.url, {"username": "x", "password": "y" * 4000})
        self.assertEqual(resp.status_code, 413)

    def test_api_login_locked_too(self):
        for _ in range(5):
            self.client.post(reverse("api-login"), {"username": "sec_user", "password": "mauvais"})
        resp = self.client.post(reverse("api-login"), {"username": "sec_user", "password": "Correct-Horse-42"})
        self.assertEqual(resp.status_code, 429)
