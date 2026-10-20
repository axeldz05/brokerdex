from decimal import Decimal
from datetime import timedelta
from threading import Barrier, Thread

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
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
        portfolio = Portfolio.objects.get(
            owner=self.buyer, creature=self.creature)

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
        portfolio = Portfolio.objects.get(
            owner=self.seller, creature=self.creature)

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
            Portfolio.objects.filter(
                owner=self.buyer,
                creature=self.creature).exists()
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

        portfolio = Portfolio.objects.get(
            owner=self.buyer, creature=self.creature)
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
        # The 3% reservation cap covers 1.5% commissions rounded per fill.
        reserved = int(Decimal('247.20') * 100)
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

    def test_market_orders_reject_non_positive_quantity(self):
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal('1'),
            average_cost=Decimal('100.00'),
        )

        for quantity in (Decimal('0'), Decimal('-1')):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValidationError):
                    TradingEngine.execute_market_buy(
                        self.buyer, self.creature, quantity
                    )
                with self.assertRaises(ValidationError):
                    TradingEngine.execute_market_sell(
                        self.seller, self.creature, quantity
                    )

    def test_orders_reject_notional_below_one_cent(self):
        self.creature.current_price = Decimal('0.01')
        self.creature.save(update_fields=['current_price'])
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal('1'),
            average_cost=Decimal('0.01'),
        )

        with self.assertRaises(ValidationError):
            TradingEngine.execute_market_buy(
                self.buyer, self.creature, Decimal('0.5')
            )
        with self.assertRaises(ValidationError):
            TradingEngine.execute_market_sell(
                self.seller, self.creature, Decimal('0.5')
            )
        with self.assertRaises(ValidationError):
            TradingEngine.place_limit_order(
                self.buyer,
                self.creature,
                Order.OrderType.BUY,
                Decimal('0.5'),
                Decimal('0.01'),
            )

    def test_money_conversions_round_to_nearest_cent(self):
        self.creature.current_price = Decimal('0.05')
        self.creature.save(update_fields=['current_price'])

        TradingEngine.execute_market_buy(
            self.buyer, self.creature, Decimal('0.3')
        )
        self.buyer.refresh_from_db()

        self.assertEqual(self.buyer.balance_cents, 100_000_00 - 2)

        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal('0.3'),
            average_cost=Decimal('0.05'),
        )
        initial_seller_balance = self.seller.balance_cents
        TradingEngine.execute_market_sell(
            self.seller, self.creature, Decimal('0.3')
        )
        self.seller.refresh_from_db()
        self.assertEqual(
            self.seller.balance_cents,
            initial_seller_balance + 2,
        )

    def test_limit_buy_execution_fills_order_and_refunds_difference(self):
        order = TradingEngine.place_limit_order(
            self.buyer,
            self.creature,
            Order.OrderType.BUY,
            Decimal('2'),
            Decimal('100.00'),
        )
        self.creature.current_price = Decimal('80.00')
        self.creature.save(update_fields=['current_price'])

        executed = TradingEngine.check_limit_orders(self.creature)

        order.refresh_from_db()
        self.buyer.refresh_from_db()
        portfolio = Portfolio.objects.get(
            owner=self.buyer,
            creature=self.creature,
        )
        trade = Trade.objects.get(buy_order=order)
        self.assertEqual(executed, 1)
        self.assertEqual(order.status, Order.Status.FILLED)
        self.assertEqual(order.filled_quantity, Decimal('2'))
        self.assertEqual(portfolio.quantity, Decimal('2'))
        self.assertEqual(trade.price_per_unit, Decimal('80.00'))
        self.assertEqual(trade.commission, Decimal('2.40'))
        self.assertEqual(self.buyer.balance_cents, 100_000_00 - 16_240)

    def test_limit_sell_execution_fills_order(self):
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal('5'),
            average_cost=Decimal('100.00'),
        )
        order = TradingEngine.place_limit_order(
            self.seller,
            self.creature,
            Order.OrderType.SELL,
            Decimal('3'),
            Decimal('120.00'),
        )
        initial_balance = self.seller.balance_cents
        self.creature.current_price = Decimal('130.00')
        self.creature.save(update_fields=['current_price'])

        executed = TradingEngine.check_limit_orders(self.creature)

        order.refresh_from_db()
        self.seller.refresh_from_db()
        portfolio = Portfolio.objects.get(
            owner=self.seller,
            creature=self.creature,
        )
        self.assertEqual(executed, 1)
        self.assertEqual(order.status, Order.Status.FILLED)
        self.assertEqual(portfolio.quantity, Decimal('2'))
        self.assertEqual(self.seller.balance_cents, initial_balance + 38_415)
        self.assertTrue(Trade.objects.filter(sell_order=order).exists())

    def test_limit_orders_respect_circuit_breaker(self):
        self.creature.circuit_breaker_expires_at = (
            timezone.now() + timedelta(minutes=15)
        )
        self.creature.save(update_fields=['circuit_breaker_expires_at'])

        with self.assertRaises(ValidationError):
            TradingEngine.place_limit_order(
                self.buyer,
                self.creature,
                Order.OrderType.BUY,
                Decimal('1'),
                Decimal('100.00'),
            )

    def test_market_order_rechecks_circuit_breaker_from_database(self):
        stale_creature = Creature.objects.get(pk=self.creature.pk)
        Creature.objects.filter(pk=self.creature.pk).update(
            circuit_breaker_expires_at=(
                timezone.now() + timedelta(minutes=15)
            )
        )

        with self.assertRaises(ValidationError):
            TradingEngine.execute_market_buy(
                self.buyer,
                stale_creature,
                Decimal('1'),
            )

    def test_pending_limit_order_does_not_execute_during_halt(self):
        order = TradingEngine.place_limit_order(
            self.buyer,
            self.creature,
            Order.OrderType.BUY,
            Decimal('1'),
            Decimal('100.00'),
        )
        self.creature.current_price = Decimal('80.00')
        self.creature.circuit_breaker_expires_at = (
            timezone.now() + timedelta(minutes=15)
        )
        self.creature.save(
            update_fields=['current_price', 'circuit_breaker_expires_at']
        )

        executed = TradingEngine.check_limit_orders(self.creature)

        order.refresh_from_db()
        self.assertEqual(executed, 0)
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertFalse(Trade.objects.filter(buy_order=order).exists())

    def test_limit_sells_cannot_overcommit_holdings(self):
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal('5'),
            average_cost=Decimal('100.00'),
        )
        TradingEngine.place_limit_order(
            self.seller,
            self.creature,
            Order.OrderType.SELL,
            Decimal('3'),
            Decimal('120.00'),
        )

        with self.assertRaises(ValidationError):
            TradingEngine.place_limit_order(
                self.seller,
                self.creature,
                Order.OrderType.SELL,
                Decimal('3'),
                Decimal('130.00'),
            )

    def test_partial_limit_sell_reserves_only_remaining_quantity(self):
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal('5'),
            average_cost=Decimal('100.00'),
        )
        Order.objects.create(
            account=self.seller,
            creature=self.creature,
            order_type=Order.OrderType.SELL,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('3'),
            filled_quantity=Decimal('1'),
            limit_price=Decimal('120.00'),
            reserved_quantity=Decimal('2'),
            status=Order.Status.PARTIALLY_FILLED,
        )

        order = TradingEngine.place_limit_order(
            self.seller,
            self.creature,
            Order.OrderType.SELL,
            Decimal('3'),
            Decimal('130.00'),
        )

        self.assertEqual(order.status, Order.Status.OPEN)

    def test_market_sell_cannot_use_holdings_reserved_by_limit_orders(self):
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal('5'),
            average_cost=Decimal('100.00'),
        )
        TradingEngine.place_limit_order(
            self.seller,
            self.creature,
            Order.OrderType.SELL,
            Decimal('4'),
            Decimal('120.00'),
        )

        with self.assertRaises(ValidationError):
            TradingEngine.execute_market_sell(
                self.seller,
                self.creature,
                Decimal('2'),
            )


class LimitOrderConcurrencyTestCase(TransactionTestCase):
    def setUp(self):
        self.account = Account.objects.create_user(
            username='limit_race',
            email='limit-race@example.com',
            password='testpass123',
        )
        self.account.balance_cents = 100_000
        self.account.save(update_fields=['balance_cents'])
        self.creature = Creature.objects.create(
            name='LimitRaceCreature',
            type='water',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=50,
            attack=50,
            defense=50,
            battle_cooldown=timedelta(hours=1),
            description='A creature for a limit order race.',
        )
        self.order = TradingEngine.place_limit_order(
            self.account,
            self.creature,
            Order.OrderType.BUY,
            Decimal('2'),
            Decimal('100.00'),
        )

    def test_fill_and_cancel_cannot_both_apply(self):
        barrier = Barrier(2)
        errors = []

        def execute():
            close_old_connections()
            try:
                order = Order.objects.get(pk=self.order.pk)
                barrier.wait()
                TradingEngine._execute_limit_buy(
                    order,
                    Decimal('80.00'),
                )
            except ValidationError as error:
                errors.append(error)
            finally:
                close_old_connections()

        def cancel():
            close_old_connections()
            try:
                order = Order.objects.get(pk=self.order.pk)
                barrier.wait()
                TradingEngine.cancel_order(order)
            except ValidationError as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [Thread(target=execute), Thread(target=cancel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.order.refresh_from_db()
        self.account.refresh_from_db()
        trade_exists = Trade.objects.filter(buy_order=self.order).exists()
        portfolio = Portfolio.objects.filter(
            owner=self.account,
            creature=self.creature,
        ).first()

        if self.order.status == Order.Status.FILLED:
            self.assertTrue(trade_exists)
            self.assertIsNotNone(portfolio)
            self.assertEqual(portfolio.quantity, Decimal('2'))
            self.assertEqual(self.account.balance_cents, 83_760)
        else:
            self.assertEqual(self.order.status, Order.Status.CANCELLED)
            self.assertFalse(trade_exists)
            self.assertIsNone(portfolio)
            self.assertEqual(self.account.balance_cents, 100_000)
        self.assertEqual(len(errors), 1)


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

    def test_price_without_market_pressure_uses_reference_price(self):
        self.creature.current_price = Decimal('150.00')

        price = PricingEngine.calculate_price(self.creature)

        self.assertEqual(price, Decimal('100.00'))

    def test_market_delta_no_trades_returns_zero(self):
        delta = PricingEngine.calculate_market_delta(self.creature)
        self.assertEqual(delta, Decimal('0'))

    def test_price_floor_at_one_cent(self):
        self.creature.current_price = Decimal('0.005')
        price = PricingEngine.calculate_price(self.creature)
        self.assertGreaterEqual(price, Decimal('0.01'))

    def test_seed_all_adjusts_reference_price_for_rarity(self):
        from django.core.management import call_command
        call_command('seed_all', clear=True)
        rayquaza = Creature.objects.get(name='Rayquaza')
        self.assertEqual(rayquaza.current_price, Decimal('3000.00'))
        self.assertEqual(rayquaza.pricing_reference_price, Decimal('2000.00'))
        self.assertEqual(PricingEngine.calculate_price(rayquaza), Decimal('3000.00'))

        mew = Creature.objects.get(name='Mew')
        self.assertEqual(mew.current_price, Decimal('5000.00'))
        self.assertEqual(mew.pricing_reference_price, Decimal('2500.00'))
        self.assertEqual(PricingEngine.calculate_price(mew), Decimal('5000.00'))

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

        fractional_trade = Trade(
            quantity=Decimal('0.33333333'),
            price_per_unit=Decimal('100.00'),
            commission=Decimal('0.50'),
        )
        self.assertEqual(fractional_trade.total_amount, Decimal('33.33'))

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

    def test_trading_order_forms_allow_fractional_quantities_in_widget(self):
        from .forms import MarketOrderForm, LimitOrderForm
        for form_cls in (MarketOrderForm, LimitOrderForm):
            form = form_cls()
            widget_attrs = form.fields['quantity'].widget.attrs
            self.assertEqual(widget_attrs.get('step'), 'any')
            self.assertEqual(Decimal(widget_attrs.get('min')), Decimal('0.00000001'))

    def test_trade_creature_protect_on_delete(self):
        """Historical trades must be protected from deletion if creature is deleted."""
        from django.db.models import ProtectedError
        Trade.objects.create(
            buyer=self.account,
            creature=self.creature,
            quantity=Decimal('1.00000000'),
            price_per_unit=Decimal('50.00'),
            commission=Decimal('0.75'),
        )
        with self.assertRaises(ProtectedError):
            self.creature.delete()
