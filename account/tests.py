from importlib import import_module
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread, current_thread, local
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.conf import settings
from django.db import IntegrityError, close_old_connections, connection
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
)
from django.urls import reverse

from .forms import AccountAuthenticationForm
from .identifiers import generate_account_identifiers
from .money import cents_to_dollars, dollars_to_cents, quantize_money
from .models import Account


class MoneyConversionTests(SimpleTestCase):
    def test_money_conversions_share_explicit_half_up_rounding(self):
        self.assertEqual(quantize_money('0.015'), Decimal('0.02'))
        self.assertEqual(dollars_to_cents('12.345'), 1235)
        self.assertEqual(cents_to_dollars(1235), Decimal('12.35'))


class AccountIdentifierTests(TestCase):
    def test_account_has_cvu_and_alias(self):
        acc = Account.objects.create(username="testuser", cvu="1234567890123456789012", alias="mi.alias.mp")
        self.assertEqual(acc.cvu, "1234567890123456789012")
        self.assertEqual(acc.alias, "mi.alias.mp")

    def test_cvu_must_be_unique(self):
        Account.objects.create(username="u1", cvu="1111", alias="alias.one")
        with self.assertRaises(IntegrityError):
            Account.objects.create(username="u2", cvu="1111", alias="alias.two")

    def test_negative_balance_cents_raises_integrity_error(self):
        with self.assertRaises(IntegrityError):
            Account.objects.create_user(
                username="negbalance",
                email="neg@example.com",
                password="password123",
                balance_cents=-100,
            )

    def test_generated_identifiers_have_the_expected_shape(self):
        cvu, alias = generate_account_identifiers()

        self.assertEqual(len(cvu), 22)
        self.assertTrue(cvu.isdigit())
        self.assertRegex(
            alias,
            r"^brokerdex\.[0-9a-f]{8}\.[0-9a-f]{8}$",
        )

    def test_create_user_assigns_unique_cvu_and_alias(self):
        first = Account.objects.create_user(
            username="first",
            email="first@example.com",
            password="testpass123",
        )
        second = Account.objects.create_user(
            username="second",
            email="second@example.com",
            password="testpass123",
        )

        self.assertEqual(len(first.cvu), 22)
        self.assertTrue(first.cvu.isdigit())
        self.assertNotEqual(first.cvu, second.cvu)
        self.assertNotEqual(first.alias, second.alias)

    def test_create_user_retries_an_identifier_collision(self):
        shared = (
            "1" * 22,
            "brokerdex.11111111.11111111",
        )
        replacement = (
            "2" * 22,
            "brokerdex.22222222.22222222",
        )
        Account.objects.create(
            username="legacy",
            email="legacy@example.com",
            cvu=shared[0],
            alias=shared[1],
        )

        with patch(
            "account.managers.generate_account_identifiers",
            side_effect=[shared, replacement],
        ):
            account = Account.objects.create_user(
                username="retry",
                email="retry@example.com",
                password="testpass123",
            )

        self.assertEqual(account.cvu, replacement[0])
        self.assertEqual(account.alias, replacement[1])

    def test_identifier_migration_backfills_legacy_accounts(self):
        legacy_accounts = [
            Account.objects.create(
                username=f"legacy_{index}",
                email=f"legacy-{index}@example.com",
            )
            for index in range(2)
        ]
        migration = import_module(
            "account.migrations.0002_backfill_account_identifiers"
        )

        migration.backfill_account_identifiers(
            apps,
            SimpleNamespace(connection=connection),
        )

        for account in legacy_accounts:
            account.refresh_from_db()
            self.assertEqual(len(account.cvu), 22)
            self.assertTrue(account.cvu.isdigit())
            self.assertTrue(account.alias)
        self.assertNotEqual(
            legacy_accounts[0].cvu,
            legacy_accounts[1].cvu,
        )
        self.assertNotEqual(
            legacy_accounts[0].alias,
            legacy_accounts[1].alias,
        )


class AccountAuthenticationHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "StrongPass123!"
        cls.account = Account.objects.create_user(
            username="login_user",
            email="login@example.com",
            password=cls.password,
        )

    def assert_client_authenticated(self):
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            self.account.pk,
        )

    def test_login_page_uses_account_authentication_form(self):
        response = self.client.get(reverse("account:login"))

        self.assertIsInstance(
            response.context["form"],
            AccountAuthenticationForm,
        )

    def test_login_accepts_username(self):
        response = self.client.post(
            reverse("account:login"),
            {
                "username": self.account.username,
                "password": self.password,
            },
        )

        self.assertRedirects(
            response,
            reverse("index"),
            fetch_redirect_response=False,
        )
        self.assert_client_authenticated()

    def test_login_accepts_email(self):
        response = self.client.post(
            reverse("account:login"),
            {
                "username": self.account.email.upper(),
                "password": self.password,
            },
        )

        self.assertRedirects(
            response,
            reverse("index"),
            fetch_redirect_response=False,
        )
        self.assert_client_authenticated()

    def test_login_does_not_reveal_whether_account_exists(self):
        wrong_password = self.client.post(
            reverse("account:login"),
            {
                "username": self.account.email,
                "password": "incorrect",
            },
        )
        with patch.object(
            Account,
            "set_password",
            autospec=True,
        ) as dummy_hasher:
            unknown_account = self.client.post(
                reverse("account:login"),
                {
                    "username": "unknown@example.com",
                    "password": "incorrect",
                },
            )

        self.assertEqual(
            list(wrong_password.context["form"].non_field_errors()),
            list(unknown_account.context["form"].non_field_errors()),
        )
        dummy_hasher.assert_called_once()
        self.assertNotContains(
            unknown_account,
            "No account found",
        )

    def test_register_username_max_length_validation(self):
        from .forms import AccountRegistrationForm
        form = AccountRegistrationForm(data={
            "username": "a" * 21,
            "email": "toolong@example.com",
            "password1": "RegistrationPass123!",
            "password2": "RegistrationPass123!",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_register_case_insensitive_email_validation(self):
        from .forms import AccountRegistrationForm
        Account.objects.create_user(
            username="existinguser",
            email="TestUser@example.com",
            password="testpass123",
        )
        form = AccountRegistrationForm(data={
            "username": "newuser",
            "email": "testuser@example.com",
            "password1": "RegistrationPass123!",
            "password2": "RegistrationPass123!",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_register_assigns_identifiers_and_authenticates_account(self):
        response = self.client.post(
            reverse("account:register"),
            {
                "username": "registered",
                "email": "registered@example.com",
                "password1": "RegistrationPass123!",
                "password2": "RegistrationPass123!",
            },
        )

        account = Account.objects.get(username="registered")
        self.assertRedirects(
            response,
            reverse("index"),
            fetch_redirect_response=False,
        )
        self.assertEqual(len(account.cvu), 22)
        self.assertTrue(account.cvu.isdigit())
        self.assertTrue(account.alias)
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            account.pk,
        )

    def test_account_contract_keeps_username_field_and_urls(self):
        self.assertEqual(Account.USERNAME_FIELD, "username")
        self.assertEqual(
            reverse("account:login"),
            "/account/login/",
        )
        self.assertEqual(
            reverse("account:register"),
            "/account/register/",
        )
        self.assertEqual(
            settings.LOGIN_URL,
            "/account/login/",
        )

    def test_account_admin_registered_as_user_admin(self):
        from django.contrib import admin
        from django.contrib.auth.admin import UserAdmin
        from .admin import AccountAdmin
        admin_instance = admin.site._registry.get(Account)
        self.assertIsInstance(admin_instance, AccountAdmin)
        self.assertIsInstance(admin_instance, UserAdmin)
        self.assertIn("balance_cents", admin_instance.list_display)
        self.assertIn("cvu", admin_instance.readonly_fields)
        self.assertIn("alias", admin_instance.readonly_fields)

    def test_balance_audit_aggregates_only_completed_transfers(self):
        from banking.models import Transfer
        account = Account.objects.create_user(
            username="audit_user",
            email="audit@example.com",
            password="testpass123",
        )
        other = Account.objects.create_user(
            username="other_audit",
            email="other_audit@example.com",
            password="testpass123",
        )
        Transfer.objects.create(
            sender=other,
            receiver=account,
            amount=5000,
            status=Transfer.TransactionStatus.COMPLETED,
            type=Transfer.TransactionType.TRANSFER,
        )
        Transfer.objects.create(
            sender=account,
            receiver=other,
            amount=2000,
            status=Transfer.TransactionStatus.COMPLETED,
            type=Transfer.TransactionType.TRANSFER,
        )
        Transfer.objects.create(
            sender=other,
            receiver=account,
            amount=10000,
            status=Transfer.TransactionStatus.PENDING,
            type=Transfer.TransactionType.TRANSFER,
        )
        Transfer.objects.create(
            sender=account,
            receiver=other,
            amount=3000,
            status=Transfer.TransactionStatus.FAILED,
            type=Transfer.TransactionType.TRANSFER,
        )

        with self.assertNumQueries(2):
            self.assertEqual(account.balance_audit, 3000)

    def test_registration_rejects_username_with_at_sign(self):
        from .forms import AccountRegistrationForm
        form = AccountRegistrationForm(data={
            "username": "user@evil.com",
            "email": "user@evil.com",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)
        self.assertIn("cannot contain the '@' character", form.errors["username"][0])

    def test_registration_rejects_username_colliding_with_existing_email(self):
        from .forms import AccountRegistrationForm
        Account.objects.create_user(
            username="legituser",
            email="target@example.com",
            password="StrongPassword1!",
        )
        form = AccountRegistrationForm(data={
            "username": "target@example.com",
            "email": "other@example.com",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_authentication_backend_disambiguates_email_and_username(self):
        from .backends import EmailOrUsernameModelBackend
        backend = EmailOrUsernameModelBackend()
        user1 = Account.objects.create_user(
            username="victim",
            email="victim@example.com",
            password="VictimPassword123!",
        )
        user2 = Account.objects.create_user(
            username="victim@example.com",
            email="attacker@example.com",
            password="AttackerPassword123!",
        )
        auth_user1 = backend.authenticate(
            None, username="victim@example.com", password="VictimPassword123!"
        )
        self.assertEqual(auth_user1, user1)

        auth_user2 = backend.authenticate(
            None, username="victim@example.com", password="AttackerPassword123!"
        )
        self.assertEqual(auth_user2, user2)


class AccountIdentifierConcurrencyTests(TransactionTestCase):
    def test_concurrent_create_user_retries_database_collision(self):
        barrier = Barrier(2)
        thread_state = local()
        errors = Queue()
        created_ids = Queue()
        shared = (
            "3" * 22,
            "brokerdex.33333333.33333333",
        )

        def generate_identifiers():
            if not getattr(thread_state, "generated", False):
                thread_state.generated = True
                barrier.wait(timeout=5)
                return shared
            suffix = current_thread().name.rsplit("-", 1)[1]
            return (
                suffix * 22,
                f"brokerdex.{suffix * 8}.{suffix * 8}",
            )

        def create_account(index):
            close_old_connections()
            try:
                account = Account.objects.create_user(
                    username=f"concurrent_{index}",
                    email=f"concurrent-{index}@example.com",
                    password="testpass123",
                )
                created_ids.put(account.pk)
            except Exception as exc:
                errors.put(exc)
            finally:
                close_old_connections()

        with patch(
            "account.managers.generate_account_identifiers",
            side_effect=generate_identifiers,
        ):
            threads = [
                Thread(
                    target=create_account,
                    args=(index,),
                    name=f"creator-{index}",
                )
                for index in (1, 2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertTrue(errors.empty(), list(errors.queue))
        self.assertEqual(created_ids.qsize(), 2)
        self.assertEqual(
            Account.objects.filter(
                username__startswith="concurrent_",
            ).count(),
            2,
        )
        self.assertEqual(
            Account.objects.filter(
                username__startswith="concurrent_",
            ).values("cvu").distinct().count(),
            2,
        )
        self.assertEqual(
            Account.objects.filter(
                username__startswith="concurrent_",
            ).values("alias").distinct().count(),
            2,
        )


class LogoutViewTests(TestCase):
    def setUp(self):
        self.user = Account.objects.create_user(
            username="logoutuser",
            email="logout@example.com",
            password="testpass123",
        )
        self.logout_url = reverse("account:logout")

    def test_get_logout_returns_method_not_allowed(self):
        self.client.force_login(self.user)
        response = self.client.get(self.logout_url)
        self.assertEqual(response.status_code, 405)

    def test_post_logout_unauthenticated_redirects_to_login(self):
        response = self.client.post(self.logout_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account:login"), response.headers["Location"])

    def test_post_logout_authenticated_logs_out_and_redirects_to_index(self):
        self.client.force_login(self.user)
        response = self.client.post(self.logout_url)
        self.assertRedirects(response, reverse("index"))
        # Verify session is flushed/logged out
        self.assertNotIn("_auth_user_id", self.client.session)
