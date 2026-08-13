from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from account.models import Account
from banking.models import Transfer


class DashboardTransactionHistoryTestCase(TestCase):
    def setUp(self):
        self.user = Account.objects.create_user(
            username="dashboard_user",
            email="dashboard@example.com",
            password="testpass123",
        )
        self.receiver = Account.objects.create_user(
            username="dashboard_receiver",
            email="dashboard-receiver@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)

    def test_history_uses_authenticated_account_and_displays_dollars(self):
        Transfer.objects.create(
            sender=self.user,
            receiver=self.receiver,
            type=Transfer.TransactionType.TRANSFER,
            status=Transfer.TransactionStatus.COMPLETED,
            amount=1234,
        )

        response = self.client.get(reverse("dashboard:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sent to")
        self.assertContains(response, "-$12.34")
        self.assertNotContains(response, "$1234")

    def test_dashboard_balance_context_remains_decimal_dollars(self):
        self.user.balance_cents = 12_345
        self.user.save(update_fields=["balance_cents"])

        response = self.client.get(reverse("dashboard:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Decimal(str(response.context["user"].balance_dollars)),
            Decimal("123.45"),
        )
