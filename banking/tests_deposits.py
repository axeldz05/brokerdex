import hashlib
import hmac
import json
from io import StringIO
from threading import Barrier, Thread

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from account.models import Account

from .models import Transfer
from .services import DepositService


WEBHOOK_SECRET = "deposit-webhook-test-secret"


@override_settings(DEPOSIT_WEBHOOK_SECRET=WEBHOOK_SECRET)
class DepositWebhookTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            username="deposit_webhook_user",
            cvu="0000000000000000000001",
            alias="deposit.webhook.user",
        )

    def _post(self, payload, secret=WEBHOOK_SECRET):
        body = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            reverse("banking:deposit_webhook"),
            data=body,
            content_type="application/json",
            headers={"X-Webhook-Signature": signature},
        )

    def _payload(self, **overrides):
        payload = {
            "status": "COMPLETED",
            "provider_reference": "provider-deposit-001",
            "target_identifier": self.account.cvu,
            "identifier_type": "CVU",
            "amount_cents": 2500,
        }
        payload.update(overrides)
        return payload

    def test_rejects_an_invalid_signature_without_leaking_secret(self):
        response = self._post(self._payload(), secret="wrong-secret")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"status": "error", "code": "invalid_signature"},
        )
        self.assertNotContains(
            response,
            WEBHOOK_SECRET,
            status_code=401,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance_cents, 0)
        self.assertFalse(Transfer.objects.exists())

    def test_duplicate_signed_webhook_credits_exactly_once(self):
        first_response = self._post(self._payload())
        second_response = self._post(self._payload())

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(first_response.json(), {"status": "ok"})
        self.assertEqual(second_response.json(), {"status": "ok"})
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance_cents, 2500)
        transfer = Transfer.objects.get()
        self.assertEqual(
            transfer.provider_reference,
            "provider-deposit-001",
        )
        self.assertEqual(transfer.type, Transfer.TransactionType.DEPOSIT)
        self.assertEqual(
            transfer.status,
            Transfer.TransactionStatus.COMPLETED,
        )

    def test_unknown_cvu_returns_stable_error_without_creating_transfer(self):
        response = self._post(
            self._payload(target_identifier="unknown-cvu")
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"status": "error", "code": "account_not_found"},
        )
        self.assertFalse(Transfer.objects.exists())

    def test_non_positive_amount_returns_stable_validation_error(self):
        response = self._post(self._payload(amount_cents=0))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"status": "error", "code": "invalid_payload"},
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance_cents, 0)
        self.assertFalse(Transfer.objects.exists())


class DepositServiceTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            username="deposit_service_user",
            cvu="0000000000000000000002",
            alias="deposit.service.user",
        )

    def test_alias_deposit_is_persistently_idempotent(self):
        first = DepositService.credit_completed(
            provider_reference="provider-alias-001",
            target_identifier=self.account.alias,
            identifier_type="ALIAS",
            amount_cents=1200,
        )
        second = DepositService.credit_completed(
            provider_reference="provider-alias-001",
            target_identifier=self.account.alias,
            identifier_type="ALIAS",
            amount_cents=1200,
        )

        self.account.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.account.balance_cents, 1200)
        self.assertEqual(
            Transfer.objects.filter(
                provider_reference="provider-alias-001",
                type=Transfer.TransactionType.DEPOSIT,
            ).count(),
            1,
        )

    def test_amount_must_be_a_strictly_positive_integer(self):
        for invalid_amount in (0, -1, "100", True):
            with self.subTest(amount=invalid_amount):
                with self.assertRaisesMessage(
                    ValidationError,
                    "Amount must be higher than zero",
                ):
                    DepositService.credit_completed(
                        provider_reference=f"invalid-{invalid_amount}",
                        target_identifier=self.account.cvu,
                        identifier_type="CVU",
                        amount_cents=invalid_amount,
                    )

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance_cents, 0)
        self.assertFalse(Transfer.objects.exists())

    def test_legacy_manager_delegates_with_a_unique_manual_reference(self):
        first = Transfer.objects.process_external_deposit(
            target_identifier=self.account.cvu,
            amount=100,
            identifier_type="CVU",
        )
        second = Transfer.objects.process_external_deposit(
            target_identifier=self.account.cvu,
            amount=100,
            identifier_type="CVU",
        )

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance_cents, 200)
        self.assertNotEqual(
            first.provider_reference,
            second.provider_reference,
        )
        self.assertTrue(first.provider_reference.startswith("manual:"))
        self.assertTrue(second.provider_reference.startswith("manual:"))

    def test_database_rejects_a_duplicate_deposit_provider_reference(self):
        transfer_data = {
            "receiver": self.account,
            "type": Transfer.TransactionType.DEPOSIT,
            "status": Transfer.TransactionStatus.COMPLETED,
            "amount": 100,
            "provider_reference": "provider-constraint-001",
        }
        Transfer.objects.create(**transfer_data)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Transfer.objects.create(**transfer_data)


class AddFundsCommandTests(TestCase):
    def test_add_funds_uses_deposit_service_with_manual_reference(self):
        account = Account.objects.create(
            username="manual_funds_user",
            cvu="0000000000000000000003",
        )
        output = StringIO()

        call_command("add_funds", account.username, 750, stdout=output)

        account.refresh_from_db()
        transfer = Transfer.objects.get(receiver=account)
        self.assertEqual(account.balance_cents, 750)
        self.assertEqual(transfer.amount, 750)
        self.assertTrue(
            transfer.provider_reference.startswith("manual:add-funds:")
        )


class ConcurrentDepositTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.account = Account.objects.create(
            username="concurrent_deposit",
            cvu="0000000000000000000004",
        )

    def test_concurrent_duplicate_confirmation_credits_exactly_once(self):
        barrier = Barrier(2)
        errors = []

        def credit():
            close_old_connections()
            try:
                barrier.wait()
                DepositService.credit_completed(
                    provider_reference="provider-concurrent-001",
                    target_identifier=self.account.cvu,
                    identifier_type="CVU",
                    amount_cents=3000,
                )
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [Thread(target=credit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance_cents, 3000)
        self.assertEqual(
            Transfer.objects.filter(
                provider_reference="provider-concurrent-001",
                type=Transfer.TransactionType.DEPOSIT,
            ).count(),
            1,
        )
