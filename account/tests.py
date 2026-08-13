from decimal import Decimal

from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase

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
