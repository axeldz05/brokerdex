from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Thread
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from account.models import Account

from .forms import TransferForm, WithdrawForm
from .models import Transfer
from .services import WithdrawalService


class TransferTestCase(TestCase):
    def setUp(self):
        Account.objects.create(email="account1@account1.com",
                               username="account1")
        Account.objects.create(email="account2@account2.com",
                               username="account2",
                               balance_cents=30000)

    def test_01_transfering_money_is_reflected_on_both_accounts(self):
        """Transactions are correctly displayed on both accounts"""
        account1 = Account.objects.get(username="account1")
        account2 = Account.objects.get(username="account2")
        transfer = Transfer.objects.create_transaction(amount=15000,
                                                       sender=account2,
                                                       receiver=account1)
        self.assertEqual(
            Account.objects.get(
                pk=account1.pk).balance_cents,
            15000)
        self.assertEqual(
            Account.objects.get(
                pk=account2.pk).balance_cents,
            15000)
        self.assertEqual(transfer.status, Transfer.TransactionStatus.COMPLETED)

    def test_02_cannot_transfer_with_less_money_than_balance(self):
        """Transactions with an amount less than balance should throw error"""
        account1 = Account.objects.get(username="account1")
        account2 = Account.objects.get(username="account2")
        try:
            Transfer.objects.create_transaction(amount=50000,
                                                sender=account2,
                                                receiver=account1)
        except ValidationError as e:
            self.assertEqual(e.message, "Insufficient funds")
        self.assertEqual(Account.objects.get(pk=account1.pk).balance_cents, 0)
        self.assertEqual(
            Account.objects.get(
                pk=account2.pk).balance_cents,
            30000)

    def test_03_can_transfer_strictly_positive_values_only(self):
        """Transactions with a non positive amount should throw error"""
        account1 = Account.objects.get(username="account1")
        account2 = Account.objects.get(username="account2")
        try:
            Transfer.objects.create_transaction(amount=0,
                                                sender=account2,
                                                receiver=account1)
        except ValidationError as e:
            self.assertEqual(e.message, "Amount must be higher than zero")
        self.assertEqual(Account.objects.get(pk=account1.pk).balance_cents, 0)
        self.assertEqual(
            Account.objects.get(
                pk=account2.pk).balance_cents,
            30000)

    def test_04_can_not_transfer_between_same_accounts(self):
        account2 = Account.objects.get(username="account2")
        try:
            Transfer.objects.create_transaction(amount=15000,
                                                sender=account2,
                                                receiver=account2)
        except ValidationError as e:
            self.assertEqual(
                e.message, "Cannot transfer between same accounts")
        self.assertEqual(
            Account.objects.get(
                pk=account2.pk).balance_cents,
            30000)

    def test_05_transfering_money_can_have_a_description(self):
        account1 = Account.objects.get(username="account1")
        account2 = Account.objects.get(username="account2")
        description_to_send = "A gift!"
        transfer = Transfer.objects.create_transaction(amount=15000,
                                                       sender=account2,
                                                       receiver=account1,
                                                       description=(
                                                           description_to_send
                                                       ))
        self.assertEqual(
            Account.objects.get(
                pk=account1.pk).balance_cents,
            15000)
        self.assertEqual(
            Account.objects.get(
                pk=account2.pk).balance_cents,
            15000)
        self.assertEqual(transfer.description, description_to_send)
        self.assertEqual(transfer.status, Transfer.TransactionStatus.COMPLETED)

    def test_completed_transfer_cannot_execute_twice(self):
        receiver = Account.objects.get(username="account1")
        sender = Account.objects.get(username="account2")
        transfer = Transfer.objects.create_transaction(
            amount=10_000,
            sender=sender,
            receiver=receiver,
        )

        transfer.execute_transaction()

        sender.refresh_from_db()
        receiver.refresh_from_db()
        self.assertEqual(sender.balance_cents, 20_000)
        self.assertEqual(receiver.balance_cents, 10_000)


class Deposit(TestCase):
    def setUp(self):
        self.acc = Account.objects.create(
            username="user1", cvu="0000000001", balance_cents=0)

    def test_deposit_via_cvu_success(self):
        transfer = Transfer.objects.process_external_deposit(
            target_identifier="0000000001",
            amount=5000,
            identifier_type='CVU'
        )

        self.acc.refresh_from_db()

        self.assertEqual(self.acc.balance_cents, 5000)
        self.assertEqual(transfer.status, 'completed')
        self.assertIsNone(transfer.sender)
        self.assertEqual(transfer.receiver, self.acc)

    def test_deposit_via_alias_success(self):
        self.acc.alias = "mi.alias.banco"
        self.acc.save()

        Transfer.objects.process_external_deposit(
            target_identifier="mi.alias.banco",
            amount=1000,
            identifier_type='ALIAS'
        )
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.balance_cents, 1000)


class Withdraw(TestCase):
    def setUp(self):
        self.acc = Account.objects.create(
            username="rich_user",
            balance_cents=10000,
            cvu="ORIGIN_CVU")

    @patch('banking.services.ExternalBankService')
    def test_withdraw_to_external_cvu_success(self, MockBankService):
        service_instance = MockBankService.return_value
        service_instance.send_money.return_value = True
        transfer = Transfer.objects.process_external_withdrawal(
            sender=self.acc,
            target_cvu="DESTINATION_CVU_123",
            amount=5000
        )
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.balance_cents, 5000)
        service_instance.send_money.assert_called_once_with(
            source_cvu="ORIGIN_CVU",
            target_cvu="DESTINATION_CVU_123",
            amount=5000,
            idempotency_key=transfer.exchange_id,
        )


class ExternalWithdrawalSafetyTests(TransactionTestCase):
    def setUp(self):
        self.account = Account.objects.create(
            username='safe_withdrawal',
            balance_cents=10_000,
            cvu='SAFE_ORIGIN_CVU',
        )

    @patch('banking.services.ExternalBankService')
    def test_provider_call_runs_without_database_transaction(self, bank_class):
        atomic_states = []

        def send_money(**kwargs):
            atomic_states.append(connection.in_atomic_block)
            return True

        bank_class.return_value.send_money.side_effect = send_money

        transfer = Transfer.objects.process_external_withdrawal(
            sender=self.account,
            target_cvu='SAFE_DESTINATION',
            amount=5_000,
        )

        self.account.refresh_from_db()
        transfer.refresh_from_db()
        self.assertEqual(atomic_states, [False])
        self.assertEqual(self.account.balance_cents, 5_000)
        self.assertEqual(
            transfer.status,
            Transfer.TransactionStatus.COMPLETED,
        )
        self.assertEqual(transfer.provider_attempts, 1)
        self.assertEqual(transfer.target_identifier, 'SAFE_DESTINATION')

    @patch('banking.services.ExternalBankService')
    def test_timeout_stays_reserved_and_retries_idempotently(
        self,
        bank_class,
    ):
        bank = bank_class.return_value
        bank.send_money.side_effect = TimeoutError('provider timeout')

        transfer = Transfer.objects.process_external_withdrawal(
            sender=self.account,
            target_cvu='SAFE_DESTINATION',
            amount=5_000,
        )

        self.account.refresh_from_db()
        transfer.refresh_from_db()
        self.assertEqual(self.account.balance_cents, 5_000)
        self.assertEqual(
            transfer.status,
            Transfer.TransactionStatus.PENDING,
        )
        self.assertIn('provider timeout', transfer.provider_error)

        bank.send_money.side_effect = None
        bank.send_money.return_value = True
        WithdrawalService.process_pending(
            transfer.pk,
            bank_service=bank,
        )

        self.account.refresh_from_db()
        transfer.refresh_from_db()
        self.assertEqual(self.account.balance_cents, 5_000)
        self.assertEqual(
            transfer.status,
            Transfer.TransactionStatus.COMPLETED,
        )
        self.assertEqual(transfer.provider_attempts, 2)
        self.assertEqual(
            {
                call.kwargs['idempotency_key']
                for call in bank.send_money.call_args_list
            },
            {transfer.exchange_id},
        )

    @patch('banking.services.ExternalBankService')
    def test_rejection_persists_failure_and_refunds(self, bank_class):
        bank_class.return_value.send_money.return_value = False

        with self.assertRaises(ValidationError):
            Transfer.objects.process_external_withdrawal(
                sender=self.account,
                target_cvu='SAFE_DESTINATION',
                amount=5_000,
            )

        self.account.refresh_from_db()
        transfer = Transfer.objects.get(
            sender=self.account,
            type=Transfer.TransactionType.WITHDRAW,
        )
        self.assertEqual(self.account.balance_cents, 10_000)
        self.assertEqual(
            transfer.status,
            Transfer.TransactionStatus.FAILED,
        )
        self.assertIsNotNone(transfer.processed_at)

    @patch('banking.services.ExternalBankService')
    def test_recovery_reclaims_stale_processing_attempt(self, bank_class):
        bank = bank_class.return_value
        bank.send_money.side_effect = TimeoutError('provider timeout')
        transfer = Transfer.objects.process_external_withdrawal(
            sender=self.account,
            target_cvu='SAFE_DESTINATION',
            amount=5_000,
        )
        Transfer.objects.filter(pk=transfer.pk).update(
            status=Transfer.TransactionStatus.PROCESSING,
            processing_started_at=timezone.now() - timedelta(minutes=10),
        )
        bank.send_money.side_effect = None
        bank.send_money.return_value = True

        recovered = WithdrawalService.recover_pending(bank_service=bank)

        transfer.refresh_from_db()
        self.assertEqual(recovered, 1)
        self.assertEqual(
            transfer.status,
            Transfer.TransactionStatus.COMPLETED,
        )


class WithdrawalTaskTests(TestCase):
    @patch('banking.tasks.WithdrawalService.recover_pending')
    def test_recovery_task_is_a_thin_service_adapter(self, recover_pending):
        from .tasks import recover_pending_withdrawals

        recover_pending.return_value = 2

        result = recover_pending_withdrawals.run()

        self.assertEqual(result, 2)
        recover_pending.assert_called_once_with()


class BankingFormTestCase(TestCase):
    def setUp(self):
        self.account = Account.objects.create_user(
            username="form_user",
            email="form@example.com",
            password="testpass123",
        )
        self.account.balance_cents = 10_000
        self.account.save(update_fields=["balance_cents"])

    def test_transfer_form_accepts_dollar_cents(self):
        Account.objects.create_user(
            username="recipient",
            email="recipient@example.com",
            password="testpass123",
        )

        form = TransferForm({
            "receiver_username": "recipient",
            "amount": "12.34",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["amount"], Decimal("12.34"))

    def test_withdraw_form_compares_dollars_to_balance_cents(self):
        valid_form = WithdrawForm(
            {"target_identifier": "DESTINATION", "amount": "100.00"},
            account=self.account,
        )
        excessive_form = WithdrawForm(
            {"target_identifier": "DESTINATION", "amount": "100.01"},
            account=self.account,
        )

        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        self.assertFalse(excessive_form.is_valid())
        self.assertIn("amount", excessive_form.errors)


class BankingViewTestCase(TestCase):
    def setUp(self):
        self.sender = Account.objects.create_user(
            username="http_sender",
            email="http-sender@example.com",
            password="testpass123",
        )
        self.sender.balance_cents = 10_000
        self.sender.cvu = "ORIGIN_CVU_HTTP"
        self.sender.save(update_fields=["balance_cents", "cvu"])
        self.receiver = Account.objects.create_user(
            username="http_receiver",
            email="http-receiver@example.com",
            password="testpass123",
        )
        self.client.force_login(self.sender)

    def test_transfer_get_exposes_balance_in_dollars(self):
        response = self.client.get(reverse("banking:transfer"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["user_balance"], Decimal("100.00"))
        self.assertEqual(
            response.context["user_balance_float"],
            Decimal("100.00"))

    def test_transfer_post_converts_dollars_to_cents(self):
        response = self.client.post(
            reverse("banking:transfer"),
            {"receiver_username": self.receiver.username, "amount": "12.34"},
        )

        self.assertRedirects(response, reverse("dashboard:dashboard"))
        self.sender.refresh_from_db()
        self.receiver.refresh_from_db()
        transfer = Transfer.objects.get(
            sender=self.sender,
            receiver=self.receiver,
        )
        self.assertEqual(transfer.amount, 1234)
        self.assertEqual(self.sender.balance_cents, 8766)
        self.assertEqual(self.receiver.balance_cents, 1234)
        self.assertIn(
            "Transaction of $12.34 completed successfully.",
            [str(message) for message in get_messages(response.wsgi_request)],
        )

    @patch("banking.services.ExternalBankService")
    def test_withdraw_post_converts_dollars_to_cents(self, bank_service):
        bank_service.return_value.send_money.return_value = True

        response = self.client.post(
            reverse("banking:withdraw"),
            {"target_identifier": "DESTINATION", "amount": "12.34"},
        )

        self.assertRedirects(response, reverse("dashboard:dashboard"))
        self.sender.refresh_from_db()
        transfer = Transfer.objects.get(
            sender=self.sender,
            type=Transfer.TransactionType.WITHDRAW,
        )
        self.assertEqual(self.sender.balance_cents, 8766)
        bank_service.return_value.send_money.assert_called_once_with(
            source_cvu="ORIGIN_CVU_HTTP",
            target_cvu="DESTINATION",
            amount=1234,
            idempotency_key=transfer.exchange_id,
        )
        self.assertIn(
            "Withdrawal of $12.34 completed successfully.",
            [str(message) for message in get_messages(response.wsgi_request)],
        )

    @patch("banking.services.ExternalBankService")
    def test_withdraw_timeout_reports_pending_without_refund(
        self,
        bank_service,
    ):
        bank_service.return_value.send_money.side_effect = TimeoutError(
            'provider timeout'
        )

        response = self.client.post(
            reverse("banking:withdraw"),
            {"target_identifier": "DESTINATION", "amount": "12.34"},
        )

        self.assertRedirects(response, reverse("dashboard:dashboard"))
        self.sender.refresh_from_db()
        transfer = Transfer.objects.get(
            sender=self.sender,
            type=Transfer.TransactionType.WITHDRAW,
        )
        self.assertEqual(self.sender.balance_cents, 8766)
        self.assertEqual(
            transfer.status,
            Transfer.TransactionStatus.PENDING,
        )
        self.assertIn(
            "Withdrawal of $12.34 is pending provider confirmation.",
            [str(message) for message in get_messages(response.wsgi_request)],
        )

    def test_withdraw_post_exposes_form_errors_as_messages(self):
        response = self.client.post(
            reverse("banking:withdraw"),
            {"target_identifier": "DESTINATION", "amount": "100.01"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "amount: Insufficient funds.",
            [str(message) for message in get_messages(response.wsgi_request)],
        )

    def test_transfer_post_handles_validation_error_list(self):
        with patch("banking.models.Transfer.objects.create_transaction", side_effect=ValidationError(["Transfer rejected."])):
            response = self.client.post(
                reverse("banking:transfer"),
                {"receiver_username": self.receiver.username, "amount": "10.00"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(
                "Transfer rejected.",
                [str(m) for m in get_messages(response.wsgi_request)],
            )

    def test_withdraw_post_handles_validation_error_list(self):
        with patch("banking.models.Transfer.objects.process_external_withdrawal", side_effect=ValidationError(["Withdrawal rejected."])):
            response = self.client.post(
                reverse("banking:withdraw"),
                {"target_identifier": "DESTINATION", "amount": "10.00"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(
                "Withdrawal rejected.",
                [str(m) for m in get_messages(response.wsgi_request)],
            )


class ConcurrentTransferTestCase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.sender = Account.objects.create_user(
            username="concurrent_sender",
            email="concurrent-sender@example.com",
            password="testpass123",
        )
        self.sender.balance_cents = 10_000
        self.sender.save(update_fields=["balance_cents"])
        self.receivers = [
            Account.objects.create_user(
                username=f"receiver_{index}",
                email=f"receiver-{index}@example.com",
                password="testpass123",
            )
            for index in range(2)
        ]

    def test_concurrent_transfers_revalidate_balance_under_lock(self):
        barrier = Barrier(2)
        errors = []

        def transfer_to(receiver_id):
            close_old_connections()
            try:
                sender = Account.objects.get(pk=self.sender.pk)
                receiver = Account.objects.get(pk=receiver_id)
                barrier.wait()
                Transfer.objects.create_transaction(
                    sender=sender,
                    receiver=receiver,
                    amount=8000,
                )
            except ValidationError as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [
            Thread(target=transfer_to, args=(receiver.pk,))
            for receiver in self.receivers
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.sender.refresh_from_db()
        receiver_total = sum(
            Account.objects.get(pk=receiver.pk).balance_cents
            for receiver in self.receivers
        )
        self.assertEqual(self.sender.balance_cents, 2000)
        self.assertEqual(receiver_total, 8000)
        self.assertEqual(
            Transfer.objects.filter(
                status=Transfer.TransactionStatus.COMPLETED
            ).count(),
            1,
        )
        self.assertEqual(len(errors), 1)
