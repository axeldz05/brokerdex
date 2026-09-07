from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import call, patch

from django.test import SimpleTestCase

from creature.models import Creature

from .models import PriceHistory
from .tasks import (
    calculate_market_indices,
    check_pending_limit_orders,
    record_daily_price_snapshots,
    record_hourly_price_snapshots,
    record_weekly_price_snapshots,
    update_creature_prices,
    update_price_after_battle,
)


class PricingTaskTests(SimpleTestCase):
    @patch('trading.tasks.PricingEngine.update_all_prices')
    def test_pricing_task_returns_service_counts_on_redelivery(
        self,
        update_all_prices,
    ):
        update_all_prices.side_effect = [3, 0]

        first = update_creature_prices.run()
        second = update_creature_prices.run()

        self.assertEqual(first, 'Updated prices for 3 creatures.')
        self.assertEqual(second, 'Updated prices for 0 creatures.')
        self.assertEqual(update_all_prices.call_count, 2)

    @patch('trading.tasks.TradingEngine.check_limit_orders')
    @patch('trading.tasks.Creature.objects.all')
    def test_pending_limit_task_aggregates_idempotent_service_results(
        self,
        all_creatures,
        check_limit_orders,
    ):
        first_creature = object()
        second_creature = object()
        all_creatures.return_value = [first_creature, second_creature]
        check_limit_orders.side_effect = [2, 1, 0, 0]

        first = check_pending_limit_orders.run()
        second = check_pending_limit_orders.run()

        self.assertEqual(first, 'Executed 3 limit orders.')
        self.assertEqual(second, 'Executed 0 limit orders.')
        self.assertEqual(
            check_limit_orders.call_args_list,
            [
                call(first_creature),
                call(second_creature),
                call(first_creature),
                call(second_creature),
            ],
        )


class PriceSnapshotTaskTests(SimpleTestCase):
    @patch('trading.tasks.PricingEngine.record_price_snapshot')
    @patch('trading.tasks.Creature.objects.all')
    def test_snapshot_tasks_delegate_each_interval_and_return_counts(
        self,
        all_creatures,
        record_snapshot,
    ):
        first_creature = object()
        second_creature = object()
        all_creatures.return_value = [first_creature, second_creature]

        hourly = record_hourly_price_snapshots.run()
        daily = record_daily_price_snapshots.run()
        weekly = record_weekly_price_snapshots.run()
        weekly_redelivery = record_weekly_price_snapshots.run()

        self.assertEqual(
            hourly,
            'Recorded hourly snapshots for 2 creatures.',
        )
        self.assertEqual(
            daily,
            'Recorded daily snapshots for 2 creatures.',
        )
        self.assertEqual(
            weekly,
            'Recorded weekly snapshots for 2 creatures.',
        )
        self.assertEqual(weekly_redelivery, weekly)
        self.assertEqual(
            record_snapshot.call_args_list,
            [
                call(first_creature, PriceHistory.Interval.ONE_HOUR),
                call(second_creature, PriceHistory.Interval.ONE_HOUR),
                call(first_creature, PriceHistory.Interval.ONE_DAY),
                call(second_creature, PriceHistory.Interval.ONE_DAY),
                call(first_creature, PriceHistory.Interval.ONE_WEEK),
                call(second_creature, PriceHistory.Interval.ONE_WEEK),
                call(first_creature, PriceHistory.Interval.ONE_WEEK),
                call(second_creature, PriceHistory.Interval.ONE_WEEK),
            ],
        )


class MarketIndexTaskTests(SimpleTestCase):
    @patch(
        'trading.services.MarketIndicesService.calculate_all_indices'
    )
    def test_index_task_returns_created_count_on_redelivery(
        self,
        calculate_all_indices,
    ):
        calculate_all_indices.side_effect = [[object(), object()], []]

        first = calculate_market_indices.run()
        second = calculate_market_indices.run()

        self.assertEqual(first, 'Recorded 2 market indices.')
        self.assertEqual(second, 'Recorded 0 market indices.')
        self.assertEqual(calculate_all_indices.call_count, 2)


class BattlePriceTaskTests(SimpleTestCase):
    @patch('trading.tasks.PricingEngine.update_creature_price')
    @patch('trading.tasks.Creature.objects.get')
    def test_missing_creature_is_stable_on_redelivery(
        self,
        get_creature,
        update_creature_price,
    ):
        get_creature.side_effect = Creature.DoesNotExist

        first = update_price_after_battle.run('missing-id', True)
        second = update_price_after_battle.run('missing-id', True)

        self.assertEqual(first, 'Creature missing-id not found.')
        self.assertEqual(second, first)
        update_creature_price.assert_not_called()

    @patch('trading.tasks.PricingEngine.update_creature_price')
    @patch('trading.tasks.Creature.objects.get')
    def test_battle_price_task_reports_win_and_loss(
        self,
        get_creature,
        update_creature_price,
    ):
        get_creature.side_effect = [
            SimpleNamespace(name='Winner'),
            SimpleNamespace(name='Loser'),
        ]
        update_creature_price.side_effect = [
            Decimal('123.45'),
            Decimal('98.76'),
        ]

        won = update_price_after_battle.run('winner-id', True)
        lost = update_price_after_battle.run('loser-id', False)

        self.assertEqual(won, 'Winner won — new price: $123.45')
        self.assertEqual(lost, 'Loser lost — new price: $98.76')
        self.assertEqual(
            get_creature.call_args_list,
            [call(pk='winner-id'), call(pk='loser-id')],
        )
