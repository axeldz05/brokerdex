from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone

from account.models import Account
from creature.models import Creature, Ability
from .models import Portfolio, Order, Trade, PriceHistory
from .services import TradingEngine, PricingEngine


class TradingEngineTestCase(TestCase):
    """Tests for market/limit order execution and portfolio management."""

    def setUp(self):
        self.buyer = Account.objects.create_user(
            username='buyer', email='buyer@test.com', password='testpass123'
        )
        self.buyer.balance_cents = 100_000_00  # $100,000
        self.buyer.save()

        self.seller = Account.objects.create_user(
            username='seller', email='seller@test.com', password='testpass123'
        )
        self.seller.balance_cents = 50_000_00
        self.seller.save()

        self.ability = Ability.objects.create(
            name='TestMove', ability_type='fire',
            damage_class='physical', power=50
        )

        self.creature = Creature.objects.create(
            name='TestCreature',
            type='fire',
            current_price=Decimal('100.00'),
            previous_close=Decimal('95.00'),
            hp=50, attack=60, defense=50,
            battle_cooldown=timedelta(hours=1),
            description='A test creature.',
        )
        self.creature.abilities.add(self.ability)

    def test_market_buy_deducts_balance_and_creates_portfolio(self):
        trade = TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal('2')
        )

        self.buyer.refresh_from_db()
        portfolio = Portfolio.objects.get(owner=self.buyer, creature=self.creature)

        # Price: $100 × 2 = $200, Commission: $200 × 1.5% = $3
        self.assertEqual(trade.price_per_unit, Decimal('100.00'))
        self.assertEqual(trade.commission, Decimal('3.00'))
        self.assertEqual(portfolio.quantity, Decimal('2'))
        self.assertEqual(portfolio.average_cost, Decimal('100.00'))
        # Balance: $100,000 - $203 = $99,797
        self.assertEqual(self.buyer.balance_cents, 99_797_00)

    def test_market_buy_insufficient_funds_raises(self):
        self.buyer.balance_cents = 100  # only $1
        self.buyer.save()

        with self.assertRaises(ValidationError):
            TradingEngine.execute_market_buy(
                self.buyer, self.creature, Decimal('1')
            )

    def test_market_sell_credits_balance_and_reduces_portfolio(self):
        # First buy some
        TradingEngine.execute_market_buy(
            self.seller, self.creature, Decimal('5')
        )
        self.seller.refresh_from_db()
        balance_after_buy = self.seller.balance_cents

        # Then sell
        trade = TradingEngine.execute_market_sell(
            self.seller, self.creature, Decimal('3')
        )

        self.seller.refresh_from_db()
        portfolio = Portfolio.objects.get(owner=self.seller, creature=self.creature)

        self.assertEqual(trade.quantity, Decimal('3'))
        self.assertEqual(portfolio.quantity, Decimal('2'))
        # Net proceeds: $300 - $4.50 commission = $295.50
        expected_balance = balance_after_buy + 295_50
        self.assertEqual(self.seller.balance_cents, expected_balance)

    def test_market_sell_without_holdings_raises(self):
        with self.assertRaises(ValidationError):
            TradingEngine.execute_market_sell(
                self.buyer, self.creature, Decimal('1')
            )

    def test_market_sell_exceeding_holdings_raises(self):
        TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal('1')
        )
        with self.assertRaises(ValidationError):
            TradingEngine.execute_market_sell(
                self.buyer, self.creature, Decimal('5')
            )

    def test_market_sell_entire_position_deletes_portfolio(self):
        TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal('2')
        )
        TradingEngine.execute_market_sell(
            self.buyer, self.creature, Decimal('2')
        )
        self.assertFalse(
            Portfolio.objects.filter(owner=self.buyer, creature=self.creature).exists()
        )

    def test_multiple_buys_update_average_cost(self):
        # Buy 2 @ $100
        TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal('2')
        )
        # Change price
        self.creature.current_price = Decimal('150.00')
        self.creature.save()
        # Buy 2 more @ $150
        TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal('2')
        )

        portfolio = Portfolio.objects.get(owner=self.buyer, creature=self.creature)
        self.assertEqual(portfolio.quantity, Decimal('4'))
        # Weighted avg: (2*100 + 2*150) / 4 = 125
        self.assertEqual(portfolio.average_cost, Decimal('125.00'))

    def test_limit_buy_order_creation(self):
        order = TradingEngine.place_limit_order(
            self.buyer, self.creature,
            Order.OrderType.BUY, Decimal('3'), Decimal('80.00')
        )
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertEqual(order.execution_type, Order.ExecutionType.LIMIT)
        self.assertEqual(order.limit_price, Decimal('80.00'))

        # Funds should be reserved
        self.buyer.refresh_from_db()
        # Reserved: 3 × $80 + commission (3 × 80 × 0.015) = $240 + $3.60 = $243.60
        reserved = int(Decimal('243.60') * 100)
        expected_balance = 100_000_00 - reserved
        self.assertEqual(self.buyer.balance_cents, expected_balance)

    def test_cancel_limit_buy_refunds(self):
        order = TradingEngine.place_limit_order(
            self.buyer, self.creature,
            Order.OrderType.BUY, Decimal('3'), Decimal('80.00')
        )
        self.buyer.refresh_from_db()
        balance_after_place = self.buyer.balance_cents

        TradingEngine.cancel_order(order)

        self.buyer.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertGreater(self.buyer.balance_cents, balance_after_place)

    def test_cancel_filled_order_raises(self):
        trade = TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal('1')
        )
        order = trade.buy_order
        with self.assertRaises(ValidationError):
            TradingEngine.cancel_order(order)


class PricingEngineTestCase(TestCase):
    """Tests for the dynamic pricing engine."""

    def setUp(self):
        self.creature = Creature.objects.create(
            name='PriceTestCreature',
            type='water',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=50, attack=50, defense=50,
            battle_cooldown=timedelta(hours=1),
            description='A creature for pricing tests.',
        )

    def test_rarity_multiplier_normal(self):
        self.assertEqual(
            PricingEngine.get_rarity_multiplier(self.creature),
            Decimal('1.0')
        )

    def test_rarity_multiplier_legendary(self):
        self.creature.is_legendary = True
        self.assertEqual(
            PricingEngine.get_rarity_multiplier(self.creature),
            Decimal('1.5')
        )

    def test_rarity_multiplier_mythical(self):
        self.creature.is_mythical = True
        self.assertEqual(
            PricingEngine.get_rarity_multiplier(self.creature),
            Decimal('2.0')
        )

    def test_battle_delta_no_battles_returns_zero(self):
        delta = PricingEngine.calculate_battle_delta(self.creature)
        self.assertEqual(delta, Decimal('0'))

    def test_market_delta_no_trades_returns_zero(self):
        delta = PricingEngine.calculate_market_delta(self.creature)
        self.assertEqual(delta, Decimal('0'))

    def test_price_floor_at_one_cent(self):
        self.creature.current_price = Decimal('0.005')
        price = PricingEngine.calculate_price(self.creature)
        self.assertGreaterEqual(price, Decimal('0.01'))

    def test_update_creature_price_persists(self):
        old_price = self.creature.current_price
        new_price = PricingEngine.update_creature_price(self.creature)
        self.creature.refresh_from_db()
        self.assertEqual(self.creature.current_price, new_price)
        self.assertEqual(self.creature.previous_close, old_price)

    def test_record_price_snapshot(self):
        snapshot = PricingEngine.record_price_snapshot(
            self.creature, PriceHistory.Interval.ONE_HOUR
        )
        self.assertEqual(snapshot.creature, self.creature)
        self.assertEqual(snapshot.interval, '1H')
        self.assertEqual(snapshot.close_price, self.creature.current_price)


class ModelValidationTestCase(TestCase):
    """Tests for model-level validations and 3NF derived properties."""

    def setUp(self):
        self.account = Account.objects.create_user(
            username='model_test', email='mt@test.com', password='testpass123'
        )
        self.creature = Creature.objects.create(
            name='ModelTestCreature', type='electric',
            current_price=Decimal('50.00'), previous_close=Decimal('45.00'),
            hp=40, attack=50, defense=40,
            battle_cooldown=timedelta(hours=1),
            description='Testing models.',
        )

    def test_order_limit_requires_limit_price(self):
        order = Order(
            account=self.account,
            creature=self.creature,
            order_type=Order.OrderType.BUY,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('1'),
            limit_price=None,
        )
        with self.assertRaises(ValidationError):
            order.clean()

    def test_order_market_rejects_limit_price(self):
        order = Order(
            account=self.account,
            creature=self.creature,
            order_type=Order.OrderType.BUY,
            execution_type=Order.ExecutionType.MARKET,
            quantity=Decimal('1'),
            limit_price=Decimal('50.00'),
        )
        with self.assertRaises(ValidationError):
            order.clean()

    def test_trade_total_amount_is_derived_property(self):
        """Verify total_amount is a @property (3NF), not a DB column."""
        trade = Trade(
            quantity=Decimal('3'),
            price_per_unit=Decimal('100.00'),
            commission=Decimal('4.50'),
        )
        self.assertEqual(trade.total_amount, Decimal('300.00'))

    def test_portfolio_unrealized_pnl_is_derived(self):
        """Verify P&L is computed from current_price, not stored."""
        portfolio = Portfolio(
            owner=self.account,
            creature=self.creature,
            quantity=Decimal('10'),
            average_cost=Decimal('40.00'),
        )
        # creature.current_price = 50, avg_cost = 40
        # current_value = 10 × 50 = 500
        # cost_basis = 10 × 40 = 400
        # unrealized_pnl = 100
        self.assertEqual(portfolio.current_value, Decimal('500.00'))
        self.assertEqual(portfolio.cost_basis, Decimal('400.00'))
        self.assertEqual(portfolio.unrealized_pnl, Decimal('100.00'))
        self.assertEqual(portfolio.unrealized_pnl_pct, Decimal('25.0'))

    def test_order_remaining_quantity_is_derived(self):
        order = Order(
            quantity=Decimal('10'),
            filled_quantity=Decimal('3'),
        )
        self.assertEqual(order.remaining_quantity, Decimal('7'))

    def test_portfolio_unique_together(self):
        """Each user can only have one portfolio entry per creature."""
        Portfolio.objects.create(
            owner=self.account, creature=self.creature,
            quantity=Decimal('5'), average_cost=Decimal('50.00')
        )
        with self.assertRaises(Exception):
            Portfolio.objects.create(
                owner=self.account, creature=self.creature,
                quantity=Decimal('3'), average_cost=Decimal('60.00')
            )
