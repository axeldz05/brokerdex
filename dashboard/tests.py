from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from account.models import Account
from banking.models import Transfer
from creature.models import Creature
from trading.accounting import compute_income_expenses
from trading.models import Trade

from .ledger import build_dashboard_ledger


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


class DashboardLedgerTestCase(TestCase):
    def setUp(self):
        self.user = Account.objects.create_user(
            username='ledger_user',
            email='ledger@example.com',
            password='testpass123',
        )
        self.counterparty = Account.objects.create_user(
            username='ledger_counterparty',
            email='ledger-counterparty@example.com',
            password='testpass123',
        )
        self.other_user = Account.objects.create_user(
            username='ledger_other',
            email='ledger-other@example.com',
            password='testpass123',
        )
        self.creature = Creature.objects.create(
            name='LedgerMon',
            description='Creature used for dashboard ledger tests.',
            type='fire',
            current_price=Decimal('10.00'),
            previous_close=Decimal('10.00'),
        )

    def create_transfer(
            self, transaction_type, amount, sender=None, receiver=None,
            status=Transfer.TransactionStatus.COMPLETED):
        return Transfer.objects.create(
            sender=sender,
            receiver=receiver,
            type=transaction_type,
            status=status,
            amount=amount,
        )

    def create_trade(
            self, buyer, seller, quantity, price, commission):
        return Trade.objects.create(
            buyer=buyer,
            seller=seller,
            creature=self.creature,
            quantity=Decimal(quantity),
            price_per_unit=Decimal(price),
            commission=Decimal(commission),
        )

    def test_ledger_presents_every_cash_flow_with_historical_amounts(self):
        self.create_transfer(
            Transfer.TransactionType.DEPOSIT,
            10_000,
            receiver=self.user,
        )
        self.create_transfer(
            Transfer.TransactionType.WITHDRAW,
            2_500,
            sender=self.user,
        )
        self.create_transfer(
            Transfer.TransactionType.TRANSFER,
            1_200,
            sender=self.user,
            receiver=self.counterparty,
        )
        self.create_transfer(
            Transfer.TransactionType.TRANSFER,
            800,
            sender=self.counterparty,
            receiver=self.user,
        )
        self.create_trade(
            self.user, None, '2', '10.00', '0.30',
        )
        self.create_trade(
            self.user, self.counterparty, '1', '8.00', '0.12',
        )
        self.create_trade(
            None, self.user, '1', '15.00', '0.23',
        )
        self.create_trade(
            self.counterparty, self.user, '1', '12.00', '0.18',
        )

        ledger = build_dashboard_ledger(self.user)
        entries = {
            entry.label: entry.amount
            for entry in ledger
        }

        self.assertEqual(
            entries,
            {
                'Deposit': Decimal('100.00'),
                'Withdrawal': Decimal('-25.00'),
                'Sent to ledger_counterparty': Decimal('-12.00'),
                'Received from ledger_counterparty': Decimal('8.00'),
                'Market buy: LedgerMon': Decimal('-20.30'),
                (
                    'P2P buy: LedgerMon from ledger_counterparty'
                ): Decimal('-8.12'),
                'Market sell: LedgerMon': Decimal('14.77'),
                (
                    'P2P sell: LedgerMon to ledger_counterparty'
                ): Decimal('11.82'),
            },
        )
        self.assertTrue(
            all(isinstance(entry.amount, Decimal)
                for entry in ledger)
        )

    def test_ledger_is_ordered_descending_across_transfers_and_trades(self):
        transfer = self.create_transfer(
            Transfer.TransactionType.DEPOSIT,
            1_000,
            receiver=self.user,
        )
        trade = self.create_trade(
            self.user, None, '1', '10.00', '0.15',
        )
        now = timezone.now()
        Transfer.objects.filter(pk=transfer.pk).update(
            date=now - timedelta(hours=1),
        )
        Trade.objects.filter(pk=trade.pk).update(executed_at=now)

        ledger = build_dashboard_ledger(self.user)

        self.assertEqual(
            [entry.label for entry in ledger],
            ['Market buy: LedgerMon', 'Deposit'],
        )
        self.assertGreaterEqual(ledger[0].occurred_at, ledger[1].occurred_at)

    def test_ledger_excludes_incomplete_and_unrelated_transfers(self):
        self.create_transfer(
            Transfer.TransactionType.DEPOSIT,
            100,
            receiver=self.user,
        )
        self.create_transfer(
            Transfer.TransactionType.DEPOSIT,
            200,
            receiver=self.user,
            status=Transfer.TransactionStatus.PENDING,
        )
        self.create_transfer(
            Transfer.TransactionType.WITHDRAW,
            300,
            sender=self.user,
            status=Transfer.TransactionStatus.FAILED,
        )
        self.create_transfer(
            Transfer.TransactionType.TRANSFER,
            400,
            sender=self.counterparty,
            receiver=self.other_user,
        )

        ledger = build_dashboard_ledger(self.user)

        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0].amount, Decimal('1.00'))

    def test_ledger_net_matches_shared_accounting(self):
        self.create_transfer(
            Transfer.TransactionType.DEPOSIT,
            10_000,
            receiver=self.user,
        )
        self.create_transfer(
            Transfer.TransactionType.WITHDRAW,
            2_500,
            sender=self.user,
        )
        self.create_transfer(
            Transfer.TransactionType.TRANSFER,
            1_200,
            sender=self.user,
            receiver=self.counterparty,
        )
        self.create_transfer(
            Transfer.TransactionType.TRANSFER,
            800,
            sender=self.counterparty,
            receiver=self.user,
        )
        self.create_trade(
            self.user, None, '2', '10.00', '0.30',
        )
        self.create_trade(
            None, self.user, '1', '15.00', '0.23',
        )

        ledger_net = sum(
            (entry.amount for entry in build_dashboard_ledger(self.user)),
            Decimal('0'),
        )
        accounting_net = compute_income_expenses(self.user)['net_flow']

        self.assertEqual(ledger_net, accounting_net)

    def test_dashboard_renders_ledger_and_expense_periods(self):
        self.create_trade(
            self.user, None, '1', '10.00', '0.15',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['ledger'], response.context[
            'transactions'
        ])
        self.assertContains(response, 'Market buy: LedgerMon')
        self.assertContains(response, '-$10.15')
        self.assertContains(response, 'Expenses')
        self.assertContains(
            response,
            response.context['expenses_data'][0]['period'],
        )

    def test_ledger_includes_incubation_and_training_sessions(self):
        from creature.models import EggTemplate, Incubation, TrainingSession
        egg = EggTemplate.objects.create(
            name="Dragon Egg",
            price=Decimal("50.00"),
            hatch_duration=timezone.timedelta(hours=1),
        )
        inc = Incubation.objects.create(
            user=self.user,
            egg_template=egg,
            hatches_at=timezone.now() + timezone.timedelta(hours=1),
        )
        session = TrainingSession.objects.create(
            user=self.user,
            creature=self.creature,
            cost_cents=1000,
            execute_at=timezone.now() + timezone.timedelta(seconds=30),
        )

        ledger = build_dashboard_ledger(self.user)
        labels = [entry.label for entry in ledger]
        self.assertIn("Egg incubation: Dragon Egg", labels)
        self.assertIn(f"Training: {self.creature.name}", labels)

        accounting = compute_income_expenses(self.user)
        self.assertEqual(accounting["total_expenses"], Decimal("60.00"))
        self.assertEqual(accounting["net_flow"], Decimal("-60.00"))

        ledger_net = sum(
            (entry.amount for entry in build_dashboard_ledger(self.user)),
            Decimal('0'),
        )
        self.assertEqual(ledger_net, accounting["net_flow"])

        # Test refund
        inc.status = Incubation.Status.FAILED
        inc.refunded_at = timezone.now()
        inc.save(update_fields=['status', 'refunded_at'])

        ledger_refunded = build_dashboard_ledger(self.user)
        refund_labels = [entry.label for entry in ledger_refunded]
        self.assertIn("Egg incubation refund: Dragon Egg", refund_labels)

        accounting_refunded = compute_income_expenses(self.user)
        self.assertEqual(accounting_refunded["total_income"], Decimal("50.00"))
        self.assertEqual(accounting_refunded["total_expenses"], Decimal("60.00"))
        self.assertEqual(accounting_refunded["net_flow"], Decimal("-10.00"))

        ledger_net_refunded = sum(
            (entry.amount for entry in ledger_refunded),
            Decimal('0'),
        )
        self.assertEqual(ledger_net_refunded, accounting_refunded["net_flow"])
