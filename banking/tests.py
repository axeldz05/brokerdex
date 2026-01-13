from django.core.exceptions import ValidationError
from django.test import TestCase
from .models import Transfer
from account.models import Account

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
        Transfer.objects.create_transaction(amount=15000,
                                sender=account2,
                                receiver=account1)
        self.assertEqual(Account.objects.get(pk=account1.pk).balance_cents, 15000)
        self.assertEqual(Account.objects.get(pk=account2.pk).balance_cents, 15000)

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
