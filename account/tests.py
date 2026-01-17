from django.db import IntegrityError
from django.test import TestCase

from .models import Account

class AccountIdentifierTests(TestCase):
    def test_account_has_cvu_and_alias(self):
        acc = Account.objects.create(username="testuser", cvu="1234567890123456789012", alias="mi.alias.mp")
        self.assertEqual(acc.cvu, "1234567890123456789012")
        self.assertEqual(acc.alias, "mi.alias.mp")

    def test_cvu_must_be_unique(self):
        Account.objects.create(username="u1", cvu="1111", alias="alias.one")
        with self.assertRaises(IntegrityError):
            Account.objects.create(username="u2", cvu="1111", alias="alias.two")
