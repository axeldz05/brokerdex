from django.core.exceptions import ValidationError
from django.test import TestCase
from .models import Transfer
from account.models import Account
from unittest.mock import patch

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
        self.assertEqual(Account.objects.get(pk=account1.pk).balance_cents, 15000)
        self.assertEqual(Account.objects.get(pk=account2.pk).balance_cents, 15000)
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
        self.assertEqual(Account.objects.get(pk=account2.pk).balance_cents, 30000)

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
        self.assertEqual(Account.objects.get(pk=account2.pk).balance_cents, 30000)

    def test_04_can_not_transfer_between_same_accounts(self):
        account2 = Account.objects.get(username="account2")
        try:
            Transfer.objects.create_transaction(amount=15000,
                                    sender=account2,
                                    receiver=account2)
        except ValidationError as e:
            self.assertEqual(e.message, "Cannot transfer between same accounts")
        self.assertEqual(Account.objects.get(pk=account2.pk).balance_cents, 30000)

    def test_05_transfering_money_can_have_a_description(self):
        account1 = Account.objects.get(username="account1")
        account2 = Account.objects.get(username="account2")
        description_to_send = "A gift!"
        transfer = Transfer.objects.create_transaction(amount=15000,
                                sender=account2,
                                receiver=account1,
                                description=description_to_send)
        self.assertEqual(Account.objects.get(pk=account1.pk).balance_cents, 15000)
        self.assertEqual(Account.objects.get(pk=account2.pk).balance_cents, 15000)
        self.assertEqual(transfer.description, description_to_send)
        self.assertEqual(transfer.status, Transfer.TransactionStatus.COMPLETED)

class Deposit(TestCase):
    def setUp(self):
        self.acc = Account.objects.create(username="user1", cvu="0000000001", balance_cents=0)

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
        self.acc = Account.objects.create(username="rich_user", balance_cents=10000, cvu="ORIGIN_CVU")

    @patch('banking.models.ExternalBankService') 
    def test_withdraw_to_external_cvu_success(self, MockBankService):
        service_instance = MockBankService.return_value
        service_instance.send_money.return_value = True
        Transfer.objects.process_external_withdrawal(
            sender=self.acc,
            target_cvu="DESTINATION_CVU_123",
            amount=5000
        )
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.balance_cents, 5000)
        service_instance.send_money.assert_called_once_with(
            source_cvu="ORIGIN_CVU",
            target_cvu="DESTINATION_CVU_123",
            amount=5000
        )
