from django.conf import settings
from django.contrib.auth.forms import PasswordChangeForm
from django.test import TestCase
from django.urls import reverse

from .forms import AccountUpdateForm
from .models import Account


class AccountSettingsHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "CurrentPass123!"
        cls.account = Account.objects.create_user(
            username="settings_user",
            email="settings@example.com",
            password=cls.password,
        )

    def setUp(self):
        self.client.force_login(self.account)
        self.url = reverse("account:settings")

    def test_settings_require_authentication(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertRedirects(
            response,
            f"{settings.LOGIN_URL}?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_general_settings_render_the_bound_account_form(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["section"], "general")
        self.assertIsInstance(response.context["acc_form"], AccountUpdateForm)
        self.assertEqual(
            response.context["acc_form"].instance,
            self.account,
        )
        self.assertNotIn("user_form", response.context)
        self.assertContains(response, "General Settings")

    def test_general_settings_update_editable_profile_fields_only(self):
        original_cvu = self.account.cvu
        original_alias = self.account.alias
        response = self.client.post(
            f"{self.url}?section=general",
            {
                "username": "attacker",
                "email": "attacker@example.com",
                "cvu": "9" * 22,
                "alias": "attacker.alias",
                "phone": "+54 11 5555 1234",
                "country": "Argentina",
                "city": "Buenos Aires",
                "address": "Calle Segura 123",
                "postal_or_zip_code": "C1000",
            },
        )

        self.assertRedirects(
            response,
            f"{self.url}?section=general",
            fetch_redirect_response=False,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.username, "settings_user")
        self.assertEqual(self.account.email, "settings@example.com")
        self.assertEqual(self.account.cvu, original_cvu)
        self.assertEqual(self.account.alias, original_alias)
        self.assertEqual(self.account.phone, "+54 11 5555 1234")
        self.assertEqual(self.account.country, "Argentina")
        self.assertEqual(self.account.city, "Buenos Aires")
        self.assertEqual(self.account.address, "Calle Segura 123")
        self.assertEqual(self.account.postal_or_zip_code, "C1000")

    def test_password_change_preserves_the_authenticated_session(self):
        response = self.client.post(
            f"{self.url}?section=password",
            {
                "old_password": self.password,
                "new_password1": "UpdatedPass456!",
                "new_password2": "UpdatedPass456!",
            },
        )

        self.assertRedirects(
            response,
            f"{self.url}?section=password",
            fetch_redirect_response=False,
        )
        self.account.refresh_from_db()
        self.assertTrue(self.account.check_password("UpdatedPass456!"))
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            self.account.pk,
        )
        password_page = self.client.get(f"{self.url}?section=password")
        self.assertEqual(password_page.status_code, 200)
        self.assertIsInstance(
            password_page.context["password_form"],
            PasswordChangeForm,
        )

    def test_bank_identifiers_are_rendered_read_only(self):
        response = self.client.get(
            f"{self.url}?section=bank_identifiers",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["section"],
            "bank_identifiers",
        )
        self.assertContains(response, self.account.cvu)
        self.assertContains(response, self.account.alias)
        self.assertContains(response, "readonly", count=2)
        self.assertNotContains(response, "Add New Bank Account")
        self.assertNotContains(response, "Privacy Settings")

    def test_invalid_section_falls_back_to_general_without_mutation(self):
        response = self.client.post(
            f"{self.url}?section=not-a-real-section",
            {
                "phone": "should-not-save",
                "country": "should-not-save",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["section"], "general")
        self.assertIsInstance(response.context["acc_form"], AccountUpdateForm)
        self.account.refresh_from_db()
        self.assertEqual(self.account.phone, "")
        self.assertEqual(self.account.country, "")

    def test_invalid_get_section_renders_the_general_fallback(self):
        response = self.client.get(
            f"{self.url}?section=not-a-real-section",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["section"], "general")
        self.assertIsInstance(response.context["acc_form"], AccountUpdateForm)

    def test_theme_is_a_browser_local_preference(self):
        response = self.client.get(f"{self.url}?section=theme")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["section"], "theme")
        self.assertContains(response, "localStorage.getItem")
        self.assertContains(response, "localStorage.setItem")
        self.assertContains(response, "stored only in this browser")

    def test_navigation_exposes_only_real_sections_and_marks_active(self):
        response = self.client.get(f"{self.url}?section=bank_identifiers")

        self.assertContains(
            response,
            (
                'href="'
                f"{self.url}?section=bank_identifiers"
                '" class="list-group-item list-group-item-action active"'
            ),
            html=False,
        )
        self.assertNotContains(response, "?section=privacy")
        self.assertNotContains(response, "?section=bank_accounts")
