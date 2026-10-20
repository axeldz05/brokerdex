from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase

from account.models import Account
from creature.models import Creature
from trading.models import Order, Portfolio, SystemTreasury, Trade
from trading.services import TradingEngine


class FinancialSolvencyAndTreasuryTestCase(TestCase):
    def setUp(self):
        self.buyer = Account.objects.create_user(
            username="solvency_buyer",
            email="buyer@solvency.io",
            password="testpass123",
        )
        self.buyer.balance_cents = 100_000_00  # $100,000.00
        self.buyer.save(update_fields=["balance_cents"])

        self.seller = Account.objects.create_user(
            username="solvency_seller",
            email="seller@solvency.io",
            password="testpass123",
        )
        self.seller.balance_cents = 50_000_00  # $50,000.00
        self.seller.save(update_fields=["balance_cents"])

        self.creature = Creature.objects.create(
            name="SolvencyMon",
            type="electric",
            current_price=Decimal("100.00"),
            previous_close=Decimal("100.00"),
            reserve_cents=50_000_00,  # $50,000.00 reserve
            pool_supply=Decimal("500.00000000"),
            hp=60,
            attack=60,
            defense=60,
            description="Solvent test creature",
        )

    def _total_system_cash(self):
        """Total cash in circulation: user balances + creature reserves + treasury pools."""
        treasury = SystemTreasury.get_instance()
        users_cash = sum(
            Account.objects.filter(
                pk__in=[self.buyer.pk, self.seller.pk]
            ).values_list("balance_cents", flat=True)
        )
        creature_reserves = sum(
            Creature.objects.filter(pk=self.creature.pk).values_list(
                "reserve_cents", flat=True
            )
        )
        return (
            users_cash
            + creature_reserves
            + treasury.dividend_pool_cents
            + treasury.company_equity_cents
        )

    def test_system_treasury_singleton_and_defaults(self):
        treasury1 = SystemTreasury.get_instance()
        self.assertEqual(treasury1.pk, 1)
        self.assertEqual(treasury1.dividend_pool_cents, 0)
        self.assertEqual(treasury1.company_equity_cents, 0)

        treasury2 = SystemTreasury.get_instance()
        self.assertEqual(treasury1.pk, treasury2.pk)

    def test_market_buy_credits_creature_reserve_and_splits_commission(self):
        # Initial state
        initial_reserve = self.creature.reserve_cents
        initial_supply = self.creature.pool_supply
        cash_before = self._total_system_cash()

        # Buyer buys 2 units @ $100.00.
        # Subtotal: $200.00 (20,000 cents)
        # Commission (1.5%): $3.00 (300 cents)
        # Buyer total cost: $203.00 (20,300 cents)
        trade = TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal("2")
        )
        self.assertIsNotNone(trade)

        self.buyer.refresh_from_db()
        self.creature.refresh_from_db()
        treasury = SystemTreasury.get_instance()

        # Buyer debited: $203.00
        self.assertEqual(self.buyer.balance_cents, 100_000_00 - 20_300)

        # Creature reserve credited with net payment: $200.00
        self.assertEqual(
            self.creature.reserve_cents, initial_reserve + 20_000
        )
        # Units exited pool_supply: 2
        self.assertEqual(
            self.creature.pool_supply, initial_supply - Decimal("2")
        )

        # Commission (300 cents) split:
        # 65% to dividend pool = 195 cents
        # 35% to company equity = 105 cents
        self.assertEqual(treasury.dividend_pool_cents, 195)
        self.assertEqual(treasury.company_equity_cents, 105)
        self.assertEqual(
            treasury.dividend_pool_cents + treasury.company_equity_cents, 300
        )

        # Closed system invariant: total cash is strictly conserved
        cash_after = self._total_system_cash()
        self.assertEqual(cash_before, cash_after)

    def test_market_sell_debits_creature_reserve_and_splits_commission(self):
        # Give seller 5 units
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal("5"),
            average_cost=Decimal("100.00"),
        )
        initial_reserve = self.creature.reserve_cents
        initial_supply = self.creature.pool_supply
        initial_seller_balance = self.seller.balance_cents
        cash_before = self._total_system_cash()

        # Seller sells 3 units @ $100.00 to MM
        # Subtotal: $300.00 (30,000 cents)
        # Commission (1.5%): $4.50 (450 cents)
        # Net proceeds to seller: $295.50 (29,550 cents)
        trade = TradingEngine.execute_market_sell(
            self.seller, self.creature, Decimal("3")
        )
        self.assertIsNotNone(trade)

        self.seller.refresh_from_db()
        self.creature.refresh_from_db()
        treasury = SystemTreasury.get_instance()

        # Seller credited: $295.50
        self.assertEqual(
            self.seller.balance_cents, initial_seller_balance + 29_550
        )

        # Creature reserve debited with gross subtotal: 30,000 cents
        self.assertEqual(
            self.creature.reserve_cents, initial_reserve - 30_000
        )
        # Units entered pool_supply: 3
        self.assertEqual(
            self.creature.pool_supply, initial_supply + Decimal("3")
        )

        # Commission (450 cents) split:
        # 65% of 450 = 293 cents (rounded)
        # 35% = 157 cents
        self.assertEqual(treasury.dividend_pool_cents, 293)
        self.assertEqual(treasury.company_equity_cents, 157)
        self.assertEqual(
            treasury.dividend_pool_cents + treasury.company_equity_cents, 450
        )

        # Closed system invariant: total cash is strictly conserved
        cash_after = self._total_system_cash()
        self.assertEqual(cash_before, cash_after)

    def test_market_sell_fails_when_market_maker_reserve_insufficient(self):
        # Creature has only 50 cents in reserve ($0.50)
        self.creature.reserve_cents = 50
        self.creature.save(update_fields=["reserve_cents"])

        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal("2"),
            average_cost=Decimal("100.00"),
        )
        initial_seller_balance = self.seller.balance_cents

        # Trying to sell 1 unit @ $100.00 requires 10,000 cents from reserve
        with self.assertRaises(ValidationError) as ctx:
            TradingEngine.execute_market_sell(
                self.seller, self.creature, Decimal("1")
            )
        self.assertIn("reserves", str(ctx.exception).lower())

        # Balances and reserves remain completely untouched (atomic rollback)
        self.seller.refresh_from_db()
        self.creature.refresh_from_db()
        self.assertEqual(self.seller.balance_cents, initial_seller_balance)
        self.assertEqual(self.creature.reserve_cents, 50)

    def test_p2p_trade_splits_both_commissions_and_preserves_creature_reserves(self):
        # Seller places limit sell @ $100.00 for 2 units
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal("2"),
            average_cost=Decimal("100.00"),
        )
        sell_order = TradingEngine.place_limit_order(
            self.seller,
            self.creature,
            Order.OrderType.SELL,
            Decimal("2"),
            Decimal("100.00"),
        )

        initial_reserve = self.creature.reserve_cents
        initial_supply = self.creature.pool_supply
        cash_before = self._total_system_cash()

        # Buyer executes market buy for 2 units - matches resting limit order P2P
        trade = TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal("2")
        )
        self.assertEqual(trade.sell_order_id, sell_order.pk)
        self.assertEqual(trade.seller_id, self.seller.pk)

        self.creature.refresh_from_db()
        treasury = SystemTreasury.get_instance()

        # Creature reserve and pool supply must remain completely untouched in P2P trade
        self.assertEqual(self.creature.reserve_cents, initial_reserve)
        self.assertEqual(self.creature.pool_supply, initial_supply)

        # Both parties paid 1.5% commission on $200 = $3.00 each = $6.00 total (600 cents)
        # 65% of 600 = 390 cents
        # 35% of 600 = 210 cents
        self.assertEqual(treasury.dividend_pool_cents, 390)
        self.assertEqual(treasury.company_equity_cents, 210)

        # Closed system invariant
        cash_after = self._total_system_cash()
        self.assertEqual(cash_before, cash_after)
