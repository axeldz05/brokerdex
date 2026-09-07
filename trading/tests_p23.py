from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import call, patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from account.models import Account
from creature.models import Creature

from .models import PriceHistory, Trade
from .services import PricingEngine
from .tasks import record_weekly_price_snapshots


UTC = datetime_timezone.utc


class P23PriceSnapshotTests(TestCase):
    def setUp(self):
        self.creature = Creature.objects.create(
            name='P23CandleMon',
            description='P23 price history creature.',
            type='fire',
            current_price=Decimal('100.00'),
            previous_close=Decimal('95.00'),
            battle_cooldown=timedelta(hours=1),
        )
        self.trader = Account.objects.create_user(
            username='p23_trader',
            email='p23-trader@example.com',
            password='testpass123',
        )

    def create_trade(self, price, quantity, executed_at):
        trade = Trade.objects.create(
            buyer=self.trader,
            seller=None,
            creature=self.creature,
            quantity=Decimal(quantity),
            price_per_unit=Decimal(price),
            commission=Decimal('0.01'),
        )
        Trade.objects.filter(pk=trade.pk).update(executed_at=executed_at)
        return trade

    def test_uses_start_of_last_completed_utc_bucket_for_each_interval(self):
        now = datetime(2026, 8, 13, 14, 37, 42, tzinfo=UTC)
        expected_starts = {
            PriceHistory.Interval.ONE_HOUR: datetime(
                2026, 8, 13, 13, 0, tzinfo=UTC
            ),
            PriceHistory.Interval.ONE_DAY: datetime(
                2026, 8, 12, 0, 0, tzinfo=UTC
            ),
            PriceHistory.Interval.ONE_WEEK: datetime(
                2026, 8, 3, 0, 0, tzinfo=UTC
            ),
        }

        with patch('trading.services.timezone.now', return_value=now):
            for interval, expected_start in expected_starts.items():
                with self.subTest(interval=interval):
                    snapshot = PricingEngine.record_price_snapshot(
                        self.creature,
                        interval,
                    )
                    self.assertEqual(snapshot.timestamp, expected_start)

    def test_records_ohlcv_from_trades_in_completed_bucket_only(self):
        now = datetime(2026, 8, 13, 14, 37, tzinfo=UTC)
        self.create_trade(
            '90.00',
            '9',
            datetime(2026, 8, 13, 12, 59, tzinfo=UTC),
        )
        self.create_trade(
            '101.00',
            '1.25',
            datetime(2026, 8, 13, 13, 5, tzinfo=UTC),
        )
        self.create_trade(
            '109.00',
            '2.00',
            datetime(2026, 8, 13, 13, 25, tzinfo=UTC),
        )
        self.create_trade(
            '99.00',
            '0.75',
            datetime(2026, 8, 13, 13, 59, 59, tzinfo=UTC),
        )
        self.create_trade(
            '120.00',
            '7',
            datetime(2026, 8, 13, 14, 0, tzinfo=UTC),
        )

        with patch('trading.services.timezone.now', return_value=now):
            snapshot = PricingEngine.record_price_snapshot(
                self.creature,
                PriceHistory.Interval.ONE_HOUR,
            )

        self.assertEqual(snapshot.open_price, Decimal('101.00'))
        self.assertEqual(snapshot.high_price, Decimal('109.00'))
        self.assertEqual(snapshot.low_price, Decimal('99.00'))
        self.assertEqual(snapshot.close_price, Decimal('99.00'))
        self.assertEqual(snapshot.volume, Decimal('4.00000000'))

    def test_rerun_updates_same_bucket_instead_of_creating_duplicate(self):
        now = datetime(2026, 8, 13, 14, 37, tzinfo=UTC)
        with patch('trading.services.timezone.now', return_value=now):
            first = PricingEngine.record_price_snapshot(
                self.creature,
                PriceHistory.Interval.ONE_HOUR,
            )
            self.creature.current_price = Decimal('115.00')
            second = PricingEngine.record_price_snapshot(
                self.creature,
                PriceHistory.Interval.ONE_HOUR,
            )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            PriceHistory.objects.filter(
                creature=self.creature,
                interval=PriceHistory.Interval.ONE_HOUR,
            ).count(),
            1,
        )
        self.assertEqual(second.close_price, Decimal('115.00'))

    def test_empty_bucket_carries_previous_close_or_current_price(self):
        prior = PriceHistory.objects.create(
            creature=self.creature,
            interval=PriceHistory.Interval.ONE_HOUR,
            timestamp=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
            open_price=Decimal('91.00'),
            high_price=Decimal('91.00'),
            low_price=Decimal('91.00'),
            close_price=Decimal('91.00'),
            volume=Decimal('0'),
        )
        now = datetime(2026, 8, 13, 14, 37, tzinfo=UTC)

        with patch('trading.services.timezone.now', return_value=now):
            carried = PricingEngine.record_price_snapshot(
                self.creature,
                PriceHistory.Interval.ONE_HOUR,
            )

        self.assertEqual(
            (
                carried.open_price,
                carried.high_price,
                carried.low_price,
                carried.close_price,
            ),
            (prior.close_price,) * 4,
        )

        prior.delete()
        carried.delete()
        with patch('trading.services.timezone.now', return_value=now):
            initial = PricingEngine.record_price_snapshot(
                self.creature,
                PriceHistory.Interval.ONE_HOUR,
            )
        self.assertEqual(
            (
                initial.open_price,
                initial.high_price,
                initial.low_price,
                initial.close_price,
            ),
            (self.creature.current_price,) * 4,
        )


class P23WeeklyTaskTests(TestCase):
    def create_creature(self, name):
        return Creature.objects.create(
            name=name,
            description='P23 weekly task creature.',
            type='water',
            current_price=Decimal('75.00'),
            previous_close=Decimal('75.00'),
            battle_cooldown=timedelta(hours=1),
        )

    @patch('trading.tasks.PricingEngine.record_price_snapshot')
    def test_weekly_task_records_every_creature(self, record_snapshot):
        first = self.create_creature('P23WeeklyOne')
        second = self.create_creature('P23WeeklyTwo')

        result = record_weekly_price_snapshots()

        self.assertCountEqual(
            record_snapshot.call_args_list,
            [
                call(first, PriceHistory.Interval.ONE_WEEK),
                call(second, PriceHistory.Interval.ONE_WEEK),
            ],
        )
        self.assertEqual(result, 'Recorded weekly snapshots for 2 creatures.')

    def test_weekly_task_is_scheduled_for_monday_midnight(self):
        entry = settings.CELERY_BEAT_SCHEDULE['snapshot-precios-semanal']

        self.assertEqual(
            entry['task'],
            'trading.tasks.record_weekly_price_snapshots',
        )
        self.assertEqual(entry['schedule']._orig_minute, 0)
        self.assertEqual(entry['schedule']._orig_hour, 0)
        self.assertEqual(entry['schedule']._orig_day_of_week, 1)


class P23PriceHistoryApiTests(TestCase):
    def setUp(self):
        self.user = Account.objects.create_user(
            username='p23_api_user',
            email='p23-api@example.com',
            password='testpass123',
        )
        self.client.force_login(self.user)
        self.creature = Creature.objects.create(
            name='P23ApiMon',
            description='P23 API creature.',
            type='grass',
            current_price=Decimal('80.00'),
            previous_close=Decimal('80.00'),
            battle_cooldown=timedelta(hours=1),
        )
        self.url = reverse(
            'trading:price_history_api',
            args=[self.creature.pk],
        )

    def create_history(self, interval, count):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        step = {
            PriceHistory.Interval.ONE_HOUR: timedelta(hours=1),
            PriceHistory.Interval.ONE_DAY: timedelta(days=1),
            PriceHistory.Interval.ONE_WEEK: timedelta(weeks=1),
        }[interval]
        PriceHistory.objects.bulk_create([
            PriceHistory(
                creature=self.creature,
                interval=interval,
                timestamp=start + (step * index),
                open_price=Decimal(index),
                high_price=Decimal(index + 2),
                low_price=Decimal(index),
                close_price=Decimal(index + 1),
                volume=Decimal(index),
            )
            for index in range(count)
        ])

    def assert_limited_history(self, interval, requested, expected_limit):
        self.create_history(interval, requested)

        response = self.client.get(self.url, {'interval': interval})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['interval'], interval)
        self.assertEqual(len(payload['data']), expected_limit)
        self.assertEqual(
            set(payload['data'][0]),
            {'timestamp', 'open', 'high', 'low', 'close', 'volume'},
        )
        retained_closes = [entry['close'] for entry in payload['data']]
        self.assertEqual(
            retained_closes,
            sorted(retained_closes),
        )
        self.assertEqual(
            retained_closes[0],
            float(requested - expected_limit + 1),
        )
        self.assertEqual(retained_closes[-1], float(requested))

    def test_weekly_api_returns_latest_52_ohlcv_candles(self):
        self.assert_limited_history(
            PriceHistory.Interval.ONE_WEEK,
            requested=60,
            expected_limit=52,
        )

    def test_hourly_and_daily_api_limits_do_not_regress(self):
        for interval, requested, expected_limit in (
            (PriceHistory.Interval.ONE_HOUR, 170, 168),
            (PriceHistory.Interval.ONE_DAY, 367, 365),
        ):
            with self.subTest(interval=interval):
                self.assert_limited_history(
                    interval,
                    requested,
                    expected_limit,
                )

    def test_detail_renders_candlesticks_volume_and_accessible_fallback(self):
        response = self.client.get(
            reverse('trading:creature_detail', args=[self.creature.pk])
        )

        self.assertContains(response, 'chartjs-chart-financial@0.2.1')
        self.assertContains(response, "type: 'candlestick'")
        self.assertContains(response, "label: 'Volume'")
        self.assertContains(response, 'aria-describedby="priceChartStatus"')
        self.assertContains(response, 'id="priceHistoryTableBody"')
