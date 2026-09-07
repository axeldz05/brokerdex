from decimal import Decimal
from threading import Barrier, Lock, Thread

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase

from account.models import Account
from creature.models import Creature
from trading.models import Portfolio, Trade
from trading.services import TradingEngine


class MarketBuyConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    initial_balance_cents = 10_150
    purchase_quantity = Decimal("1")

    def setUp(self):
        self.buyer = Account.objects.create_user(
            username="concurrent_buyer",
            email="concurrent-market-buyer@example.com",
            password="testpass123",
        )
        self.buyer.balance_cents = self.initial_balance_cents
        self.buyer.save(update_fields=["balance_cents"])
        self.creature = Creature.objects.create(
            name="ConcurrentMarketBuyCreature",
            type="fire",
            current_price=Decimal("100.00"),
            previous_close=Decimal("100.00"),
            hp=50,
            attack=50,
            defense=50,
            description="A creature for the concurrent market-buy test.",
        )

    def test_two_market_buys_cannot_spend_the_same_balance(self):
        start = Barrier(2, timeout=5)
        outcome_lock = Lock()
        outcomes = []

        def buy():
            close_old_connections()
            try:
                buyer = Account.objects.get(pk=self.buyer.pk)
                creature = Creature.objects.get(pk=self.creature.pk)
                start.wait()
                trade = TradingEngine.execute_market_buy(
                    buyer,
                    creature,
                    self.purchase_quantity,
                )
            except ValidationError as error:
                outcome = ("rejected", error)
            except Exception as error:
                outcome = ("unexpected", error)
            else:
                outcome = ("bought", trade.pk)
            finally:
                with outcome_lock:
                    outcomes.append(outcome)
                close_old_connections()

        threads = [Thread(target=buy) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(
            [outcome for outcome in outcomes if outcome[0] == "unexpected"],
            [],
        )
        self.assertEqual(
            len([outcome for outcome in outcomes if outcome[0] == "bought"]),
            1,
        )
        self.assertEqual(
            len([outcome for outcome in outcomes if outcome[0] == "rejected"]),
            1,
        )

        self.buyer.refresh_from_db()
        trades = list(Trade.objects.filter(buyer=self.buyer))
        financed_cents = sum(
            int((trade.total_amount + trade.commission) * Decimal("100"))
            for trade in trades
        )

        self.assertGreaterEqual(self.buyer.balance_cents, 0)
        self.assertLessEqual(financed_cents, self.initial_balance_cents)
        self.assertEqual(len(trades), 1)
        self.assertEqual(
            self.buyer.balance_cents,
            self.initial_balance_cents - financed_cents,
        )
        portfolio = Portfolio.objects.get(
            owner=self.buyer,
            creature=self.creature,
        )
        self.assertEqual(portfolio.quantity, self.purchase_quantity)
