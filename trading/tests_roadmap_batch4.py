from importlib import import_module
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Lock, Thread

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from account.models import Account
from creature.models import Ability, Battle, BattleInvestment, Creature
from creature.services import BattleService

from .models import Order, Portfolio, Trade
from .services import TradingEngine


class ReservationMigrationTestCase(TestCase):
    @staticmethod
    def run_backfill():
        migration = import_module(
            'trading.migrations.0005_order_reservations_and_constraints'
        )
        migration.backfill_active_reservations(
            import_module('django.apps').apps,
            None,
        )

    def test_buy_backfill_cancels_and_refunds_legacy_reservation(self):
        creature = Creature.objects.create(
            name='MigrationReserveMon',
            type='fire',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            battle_cooldown=timedelta(hours=1),
        )
        buyer = Account.objects.create_user(
            username='migration_buyer',
            email='migration-buyer@example.com',
            password='testpass123',
        )
        buyer.balance_cents = 9_850
        buyer.save(update_fields=['balance_cents'])
        order = Order.objects.create(
            account=buyer,
            creature=creature,
            order_type=Order.OrderType.BUY,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('1'),
            limit_price=Decimal('100.00'),
        )

        self.run_backfill()

        buyer.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(order.reserved_cents, 0)
        self.assertEqual(buyer.balance_cents, 20_000)

    def test_backfill_cancels_orders_without_cash_or_holdings(self):
        creature = Creature.objects.create(
            name='MigrationCancelMon',
            type='water',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            battle_cooldown=timedelta(hours=1),
        )
        buyer = Account.objects.create_user(
            username='underfunded_buyer',
            email='underfunded-migration-buyer@example.com',
            password='testpass123',
        )
        buyer.balance_cents = 100
        buyer.save(update_fields=['balance_cents'])
        seller = Account.objects.create_user(
            username='overbooked_seller',
            email='overbooked-migration-seller@example.com',
            password='testpass123',
        )
        Portfolio.objects.create(
            owner=seller,
            creature=creature,
            quantity=Decimal('1'),
            average_cost=Decimal('50.00'),
        )
        buy = Order.objects.create(
            account=buyer,
            creature=creature,
            order_type=Order.OrderType.BUY,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('1'),
            limit_price=Decimal('100.00'),
        )
        first_sell = Order.objects.create(
            account=seller,
            creature=creature,
            order_type=Order.OrderType.SELL,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('1'),
            limit_price=Decimal('100.00'),
        )
        second_sell = Order.objects.create(
            account=seller,
            creature=creature,
            order_type=Order.OrderType.SELL,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('1'),
            limit_price=Decimal('100.00'),
        )

        self.run_backfill()

        buy.refresh_from_db()
        first_sell.refresh_from_db()
        second_sell.refresh_from_db()
        self.assertEqual(buy.status, Order.Status.CANCELLED)
        self.assertEqual(buy.reserved_cents, 0)
        self.assertEqual(first_sell.reserved_quantity, Decimal('1'))
        self.assertEqual(second_sell.status, Order.Status.CANCELLED)
        self.assertEqual(second_sell.reserved_quantity, Decimal('0'))

    def test_backfill_normalizes_active_orders_with_no_remainder(self):
        creature = Creature.objects.create(
            name='MigrationFilledMon',
            type='grass',
            current_price=Decimal('10.00'),
            previous_close=Decimal('10.00'),
            battle_cooldown=timedelta(hours=1),
        )
        buyer = Account.objects.create_user(
            username='filled_migration',
            email='filled-migration@example.com',
            password='testpass123',
        )
        order = Order.objects.create(
            account=buyer,
            creature=creature,
            order_type=Order.OrderType.BUY,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('1'),
            filled_quantity=Decimal('1'),
            limit_price=Decimal('10.00'),
            status=Order.Status.OPEN,
        )

        self.run_backfill()

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FILLED)
        self.assertEqual(order.reserved_cents, 0)

    def test_buy_backfill_reproduces_legacy_truncation_exactly(self):
        creature = Creature.objects.create(
            name='MigrationRoundingMon',
            type='electric',
            current_price=Decimal('0.05'),
            previous_close=Decimal('0.05'),
            battle_cooldown=timedelta(hours=1),
        )
        buyer = Account.objects.create_user(
            username='migration_rounding',
            email='migration-rounding@example.com',
            password='testpass123',
        )
        buyer.balance_cents = 99
        buyer.save(update_fields=['balance_cents'])
        order = Order.objects.create(
            account=buyer,
            creature=creature,
            order_type=Order.OrderType.BUY,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('0.3'),
            limit_price=Decimal('0.05'),
        )

        self.run_backfill()

        buyer.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(buyer.balance_cents, 100)
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_backfill_cancels_malformed_limit_without_price(self):
        creature = Creature.objects.create(
            name='MigrationMalformedMon',
            type='normal',
            current_price=Decimal('10.00'),
            previous_close=Decimal('10.00'),
            battle_cooldown=timedelta(hours=1),
        )
        buyer = Account.objects.create_user(
            username='migration_bad',
            email='migration-malformed@example.com',
            password='testpass123',
        )
        order = Order.objects.create(
            account=buyer,
            creature=creature,
            order_type=Order.OrderType.BUY,
            execution_type=Order.ExecutionType.LIMIT,
            quantity=Decimal('1'),
            limit_price=None,
        )

        self.run_backfill()

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)


class OrderBookTestCase(TestCase):
    def setUp(self):
        self.creature = Creature.objects.create(
            name='OrderBookMon',
            type='fire',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=50,
            attack=50,
            defense=50,
            battle_cooldown=timedelta(hours=1),
            description='Order book test creature.',
        )
        self.buyer = self._account('book_buyer', 100_000_00)
        self.seller_1 = self._account('book_seller_1', 0)
        self.seller_2 = self._account('book_seller_2', 0)

    @staticmethod
    def _account(username, balance_cents):
        account = Account.objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password='testpass123',
        )
        account.balance_cents = balance_cents
        account.save(update_fields=['balance_cents'])
        return account

    def _hold(self, owner, quantity, average_cost=Decimal('40.00')):
        return Portfolio.objects.create(
            owner=owner,
            creature=self.creature,
            quantity=quantity,
            average_cost=average_cost,
        )

    def test_limit_orders_persist_and_release_explicit_reservations(self):
        self._hold(self.seller_1, Decimal('5'))

        buy = TradingEngine.place_limit_order(
            self.buyer,
            self.creature,
            Order.OrderType.BUY,
            Decimal('2'),
            Decimal('80.00'),
        )
        sell = TradingEngine.place_limit_order(
            self.seller_1,
            self.creature,
            Order.OrderType.SELL,
            Decimal('3'),
            Decimal('120.00'),
        )

        self.assertEqual(buy.reserved_cents, 16_480)
        self.assertEqual(buy.reserved_quantity, Decimal('0'))
        self.assertEqual(sell.reserved_cents, 0)
        self.assertEqual(sell.reserved_quantity, Decimal('3'))

        TradingEngine.cancel_order(buy)
        TradingEngine.cancel_order(sell)
        buy.refresh_from_db()
        sell.refresh_from_db()
        self.buyer.refresh_from_db()

        self.assertEqual(buy.reserved_cents, 0)
        self.assertEqual(sell.reserved_quantity, Decimal('0'))
        self.assertEqual(self.buyer.balance_cents, 100_000_00)

    def test_p2p_matching_uses_price_then_time_priority(self):
        self._hold(self.seller_1, Decimal('1'))
        self._hold(self.seller_2, Decimal('2'))
        worse_older = TradingEngine.place_limit_order(
            self.seller_1,
            self.creature,
            Order.OrderType.SELL,
            Decimal('1'),
            Decimal('95.00'),
        )
        better_first = TradingEngine.place_limit_order(
            self.seller_2,
            self.creature,
            Order.OrderType.SELL,
            Decimal('1'),
            Decimal('90.00'),
        )
        better_second = TradingEngine.place_limit_order(
            self.seller_2,
            self.creature,
            Order.OrderType.SELL,
            Decimal('1'),
            Decimal('90.00'),
        )

        TradingEngine.execute_market_buy(
            self.buyer,
            self.creature,
            Decimal('2'),
        )

        trades = list(
            Trade.objects.filter(buyer=self.buyer).order_by(
                'executed_at',
                'pk',
            )
        )
        self.assertEqual(
            [trade.sell_order_id for trade in trades],
            [better_first.pk, better_second.pk],
        )
        self.assertEqual(
            [trade.price_per_unit for trade in trades],
            [Decimal('90.00'), Decimal('90.00')],
        )
        worse_older.refresh_from_db()
        self.assertEqual(worse_older.status, Order.Status.OPEN)

    def test_limit_p2p_partial_fill_refunds_price_improvement(self):
        self._hold(self.seller_1, Decimal('1'))
        sell = TradingEngine.place_limit_order(
            self.seller_1,
            self.creature,
            Order.OrderType.SELL,
            Decimal('1'),
            Decimal('80.00'),
        )
        buy = TradingEngine.place_limit_order(
            self.buyer,
            self.creature,
            Order.OrderType.BUY,
            Decimal('3'),
            Decimal('100.00'),
        )
        self.creature.current_price = Decimal('120.00')
        self.creature.save(update_fields=['current_price'])

        TradingEngine.check_limit_orders(self.creature)

        buy.refresh_from_db()
        sell.refresh_from_db()
        self.buyer.refresh_from_db()
        trade = Trade.objects.get(buy_order=buy, sell_order=sell)
        self.assertEqual(trade.price_per_unit, Decimal('80.00'))
        self.assertEqual(buy.status, Order.Status.PARTIALLY_FILLED)
        self.assertEqual(buy.filled_quantity, Decimal('1'))
        self.assertEqual(buy.reserved_cents, 20_750)
        self.assertEqual(sell.status, Order.Status.FILLED)
        self.assertEqual(sell.reserved_quantity, Decimal('0'))
        self.assertEqual(self.buyer.balance_cents, 100_000_00 - 28_870)

    def test_buy_reservation_covers_commission_rounding_across_partial_fills(
        self,
    ):
        self._hold(self.seller_1, Decimal('1'))
        self._hold(self.seller_2, Decimal('1'))
        for seller in (self.seller_1, self.seller_2):
            TradingEngine.place_limit_order(
                seller,
                self.creature,
                Order.OrderType.SELL,
                Decimal('1'),
                Decimal('0.34'),
            )
        buy = TradingEngine.place_limit_order(
            self.buyer,
            self.creature,
            Order.OrderType.BUY,
            Decimal('2'),
            Decimal('0.34'),
        )

        TradingEngine.check_limit_orders(self.creature)

        buy.refresh_from_db()
        self.assertEqual(buy.status, Order.Status.FILLED)
        self.assertEqual(Trade.objects.filter(buy_order=buy).count(), 2)

    def test_market_buy_consumes_p2p_then_market_maker_remainder(self):
        self._hold(self.seller_1, Decimal('1'), Decimal('55.00'))
        sell = TradingEngine.place_limit_order(
            self.seller_1,
            self.creature,
            Order.OrderType.SELL,
            Decimal('1'),
            Decimal('90.00'),
        )

        returned_trade = TradingEngine.execute_market_buy(
            self.buyer,
            self.creature,
            Decimal('3'),
        )

        market_order = returned_trade.buy_order
        trades = list(
            Trade.objects.filter(buy_order=market_order).order_by(
                'price_per_unit'
            )
        )
        self.assertEqual(len(trades), 2)
        self.assertEqual(
            [(trade.quantity, trade.price_per_unit) for trade in trades],
            [
                (Decimal('1'), Decimal('90.00')),
                (Decimal('2'), Decimal('100.00')),
            ],
        )
        self.assertEqual(trades[0].sell_order_id, sell.pk)
        self.assertIsNone(trades[1].seller_id)
        self.assertEqual(market_order.filled_quantity, Decimal('3'))

    def test_market_sell_consumes_multiple_bids_then_mm_remainder(self):
        buyer_2 = self._account('book_buyer_2', 100_000_00)
        self._hold(self.seller_1, Decimal('4'), Decimal('60.00'))
        best = TradingEngine.place_limit_order(
            self.buyer,
            self.creature,
            Order.OrderType.BUY,
            Decimal('1'),
            Decimal('110.00'),
        )
        second = TradingEngine.place_limit_order(
            buyer_2,
            self.creature,
            Order.OrderType.BUY,
            Decimal('1'),
            Decimal('105.00'),
        )

        returned_trade = TradingEngine.execute_market_sell(
            self.seller_1,
            self.creature,
            Decimal('3'),
        )

        market_order = returned_trade.sell_order
        trades = list(
            Trade.objects.filter(sell_order=market_order).order_by(
                '-price_per_unit'
            )
        )
        self.assertEqual(
            [(trade.buy_order_id, trade.price_per_unit) for trade in trades],
            [
                (best.pk, Decimal('110.00')),
                (second.pk, Decimal('105.00')),
                (None, Decimal('100.00')),
            ],
        )
        self.assertEqual(
            [trade.realized_cost_basis for trade in trades],
            [Decimal('60.00'), Decimal('60.00'), Decimal('60.00')],
        )

    def test_self_trade_is_skipped_without_blocking_other_liquidity(self):
        self._hold(self.buyer, Decimal('2'))
        self._hold(self.seller_1, Decimal('1'))
        own_sell = TradingEngine.place_limit_order(
            self.buyer,
            self.creature,
            Order.OrderType.SELL,
            Decimal('1'),
            Decimal('80.00'),
        )
        external_sell = TradingEngine.place_limit_order(
            self.seller_1,
            self.creature,
            Order.OrderType.SELL,
            Decimal('1'),
            Decimal('90.00'),
        )

        trade = TradingEngine.execute_market_buy(
            self.buyer,
            self.creature,
            Decimal('1'),
        )

        own_sell.refresh_from_db()
        self.assertEqual(trade.sell_order_id, external_sell.pk)
        self.assertEqual(trade.seller_id, self.seller_1.pk)
        self.assertNotEqual(trade.buyer_id, trade.seller_id)
        self.assertEqual(own_sell.status, Order.Status.OPEN)

    def test_p2p_sell_trade_snapshots_cost_basis_before_reduction(self):
        self._hold(self.seller_1, Decimal('2'), Decimal('37.50'))
        sell = TradingEngine.place_limit_order(
            self.seller_1,
            self.creature,
            Order.OrderType.SELL,
            Decimal('2'),
            Decimal('90.00'),
        )

        trade = TradingEngine.execute_market_buy(
            self.buyer,
            self.creature,
            Decimal('2'),
        )

        self.assertEqual(trade.sell_order_id, sell.pk)
        self.assertEqual(trade.realized_cost_basis, Decimal('75.00'))
        self.assertFalse(
            Portfolio.objects.filter(
                owner=self.seller_1,
                creature=self.creature,
            ).exists()
        )

    def test_weighted_average_cost_rounds_half_up_at_money_boundary(self):
        self._hold(self.buyer, Decimal('1'), Decimal('100.00'))
        self.creature.current_price = Decimal('100.01')
        self.creature.save(update_fields=['current_price'])

        TradingEngine.execute_market_buy(
            self.buyer,
            self.creature,
            Decimal('1'),
        )

        portfolio = Portfolio.objects.get(
            owner=self.buyer,
            creature=self.creature,
        )
        self.assertEqual(portfolio.average_cost, Decimal('100.01'))


class BattleTradeInvestmentTestCase(TestCase):
    def setUp(self):
        self.creature_1 = Creature.objects.create(
            name='InvestmentOne',
            type='water',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=50,
            attack=50,
            defense=50,
            battle_cooldown=timedelta(hours=1),
            description='First investment creature.',
        )
        self.creature_2 = Creature.objects.create(
            name='InvestmentTwo',
            type='grass',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=50,
            attack=50,
            defense=50,
            battle_cooldown=timedelta(hours=1),
            description='Second investment creature.',
        )
        self.buyer_1 = Account.objects.create_user(
            username='investment_buyer_1',
            email='investment1@example.com',
            password='testpass123',
        )
        self.buyer_2 = Account.objects.create_user(
            username='investment_buyer_2',
            email='investment2@example.com',
            password='testpass123',
        )

    def test_turn_investments_count_unique_trade_buyers_and_volume(self):
        battle = Battle.objects.start_battle(
            self.creature_1,
            self.creature_2,
        )
        for buyer, quantity in (
            (self.buyer_1, Decimal('1.25')),
            (self.buyer_1, Decimal('0.75')),
            (self.buyer_2, Decimal('2.00')),
        ):
            Trade.objects.create(
                buyer=buyer,
                seller=None,
                creature=self.creature_1,
                quantity=quantity,
                price_per_unit=Decimal('100.00'),
                commission=Decimal('1.50'),
            )

        BattleService.record_turn_investments(battle, 1)

        investment = BattleInvestment.objects.get(
            battle=battle,
            turn_number=1,
            creature=self.creature_1,
        )
        self.assertEqual(investment.investor_count, 2)
        self.assertEqual(investment.total_amount, Decimal('4.00'))

    def test_first_turn_investments_include_trades_before_its_action(self):
        battle = Battle.objects.start_battle(
            self.creature_1,
            self.creature_2,
        )
        Trade.objects.create(
            buyer=self.buyer_1,
            seller=None,
            creature=self.creature_1,
            quantity=Decimal('1.50'),
            price_per_unit=Decimal('100.00'),
            commission=Decimal('2.25'),
        )
        participant_1, participant_2 = battle.participants.all()
        ability = Ability.objects.create(
            name='Investment Finish',
            ability_type='normal',
            damage_class='physical',
            power=100,
        )
        battle.record_action(
            participant_1,
            participant_2,
            ability,
            participant_2.current_hp,
        )

        BattleService.record_turn_investments(battle, 1)

        investment = BattleInvestment.objects.get(
            battle=battle,
            turn_number=1,
            creature=self.creature_1,
        )
        self.assertEqual(investment.investor_count, 1)
        self.assertEqual(investment.total_amount, Decimal('1.50'))


class OrderBookConcurrencyTestCase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.creature = Creature.objects.create(
            name='ConcurrentBookMon',
            type='electric',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=50,
            attack=50,
            defense=50,
            battle_cooldown=timedelta(hours=1),
            description='Concurrent order book creature.',
        )
        self.buyer = Account.objects.create_user(
            username='concurrent_buyer',
            email='concurrent-buyer@example.com',
            password='testpass123',
        )
        self.buyer.balance_cents = 100_000
        self.buyer.save(update_fields=['balance_cents'])
        self.seller = Account.objects.create_user(
            username='concurrent_seller',
            email='concurrent-seller@example.com',
            password='testpass123',
        )
        Portfolio.objects.create(
            owner=self.seller,
            creature=self.creature,
            quantity=Decimal('1'),
            average_cost=Decimal('50.00'),
        )
        self.buy = TradingEngine.place_limit_order(
            self.buyer,
            self.creature,
            Order.OrderType.BUY,
            Decimal('1'),
            Decimal('100.00'),
        )
        self.sell = TradingEngine.place_limit_order(
            self.seller,
            self.creature,
            Order.OrderType.SELL,
            Decimal('1'),
            Decimal('100.00'),
        )

    def test_two_workers_cannot_execute_same_cross_twice(self):
        barrier = Barrier(2)
        result_lock = Lock()
        results = []
        errors = []

        def worker():
            close_old_connections()
            try:
                creature = Creature.objects.get(pk=self.creature.pk)
                barrier.wait()
                result = TradingEngine.check_limit_orders(creature)
                with result_lock:
                    results.append(result)
            except Exception as error:
                with result_lock:
                    errors.append(error)
            finally:
                close_old_connections()

        threads = [Thread(target=worker), Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sum(results), 1)
        self.assertEqual(Trade.objects.count(), 1)
        self.buy.refresh_from_db()
        self.sell.refresh_from_db()
        self.assertEqual(self.buy.status, Order.Status.FILLED)
        self.assertEqual(self.sell.status, Order.Status.FILLED)
