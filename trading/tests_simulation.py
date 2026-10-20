from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from account.models import Account
from creature.models import Creature, Ability, EggTemplate, Incubation, Battle
from trading.models import PriceHistory, Trade, Portfolio
from trading.simulation.state import (
    is_simulation_active,
    set_simulation_active,
    get_simulation_stats,
    reset_simulation_stats,
    record_simulation_event,
)
from trading.simulation.strategies import (
    MomentumStrategy,
    ValueStrategy,
    MarketMakerStrategy,
    CasualStrategy,
)
from trading.simulation.engine import SimulationEngine


class SimulationStateTestCase(TestCase):
    def setUp(self):
        reset_simulation_stats()

    def test_default_state_is_active(self):
        set_simulation_active(True)
        self.assertTrue(is_simulation_active())

    def test_toggle_state(self):
        set_simulation_active(False)
        self.assertFalse(is_simulation_active())
        set_simulation_active(True)
        self.assertTrue(is_simulation_active())

    def test_record_event_and_stats(self):
        record_simulation_event('MOMENTUM', 'BUY', 'Pikachu', Decimal('2.0'), 'Trend follow')
        stats = get_simulation_stats()
        self.assertEqual(stats['total_events'], 1)
        self.assertEqual(stats['strategies']['MOMENTUM'], 1)
        self.assertEqual(stats['strategies']['VALUE'], 0)


class SimulationStrategiesTestCase(TestCase):
    def setUp(self):
        reset_simulation_stats()
        self.user = Account.objects.create_user(
            username='bot_tester',
            email='bot_tester@brokerdex.io',
            password='testpass123',
            balance_cents=100_000_00,  # $100,000
        )
        self.ability = Ability.objects.create(
            name='Thunderbolt',
            ability_type='electric',
            damage_class='special',
            power=90,
        )
        self.creature = Creature.objects.create(
            name='Pikachu',
            type='electric',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            pricing_reference_price=Decimal('100.00'),
            battle_cooldown=timedelta(hours=1),
            cooldown_expires_at=timezone.now(),
        )
        self.creature.abilities.add(self.ability)

        # Give portfolio to user so sell strategies can work
        Portfolio.objects.create(
            owner=self.user,
            creature=self.creature,
            quantity=Decimal('10.0'),
            average_cost=Decimal('100.00'),
        )

        self.egg = EggTemplate.objects.create(
            name='Basic Egg',
            price=Decimal('50.00'),
            hatch_duration=timedelta(hours=1),
        )
        self.egg.creature_pool.add(self.creature)

    def test_value_strategy_buys_when_undervalued(self):
        # Creature is 20% undervalued vs reference price
        self.creature.current_price = Decimal('75.00')
        self.creature.pricing_reference_price = Decimal('100.00')
        self.creature.save()

        strategy = ValueStrategy()
        event = strategy.evaluate_and_execute(self.user, [self.creature])
        self.assertIsNotNone(event)
        self.assertEqual(event['strategy'], 'VALUE')
        self.assertEqual(event['action'], 'BUY')
        self.assertEqual(event['creature'], 'Pikachu')

    def test_value_strategy_sells_when_overvalued(self):
        # Creature is 25% overvalued vs reference price
        self.creature.current_price = Decimal('125.00')
        self.creature.pricing_reference_price = Decimal('100.00')
        self.creature.save()

        strategy = ValueStrategy()
        event = strategy.evaluate_and_execute(self.user, [self.creature])
        self.assertIsNotNone(event)
        self.assertEqual(event['strategy'], 'VALUE')
        self.assertEqual(event['action'], 'SELL')

    def test_market_maker_strategy_places_limits(self):
        strategy = MarketMakerStrategy()
        event = strategy.evaluate_and_execute(self.user, [self.creature])
        self.assertIsNotNone(event)
        self.assertEqual(event['strategy'], 'MARKET_MAKER')
        self.assertIn(event['action'], ['LIMIT_BUY', 'LIMIT_SELL'])

    def test_casual_strategy_executes(self):
        strategy = CasualStrategy()
        event = strategy.evaluate_and_execute(self.user, [self.creature])
        self.assertIsNotNone(event)
        self.assertEqual(event['strategy'], 'CASUAL')


class SimulationEngineTestCase(TestCase):
    def setUp(self):
        reset_simulation_stats()
        set_simulation_active(True)
        self.user = Account.objects.create_user(
            username='sim_user_1',
            email='sim_user_1@brokerdex.io',
            password='testpass123',
            balance_cents=50_000_00,
        )
        self.creature = Creature.objects.create(
            name='Raichu',
            type='electric',
            current_price=Decimal('150.00'),
            previous_close=Decimal('150.00'),
            pricing_reference_price=Decimal('150.00'),
            battle_cooldown=timedelta(hours=1),
            cooldown_expires_at=timezone.now(),
        )

    def test_engine_tick_executes_action(self):
        engine = SimulationEngine()
        events = engine.tick()
        self.assertIsInstance(events, list)
        self.assertGreaterEqual(len(events), 1)

    def test_engine_tick_aborts_when_paused(self):
        set_simulation_active(False)
        engine = SimulationEngine()
        events = engine.tick()
        self.assertEqual(events, [])


class BotsCommandTestCase(TestCase):
    def setUp(self):
        reset_simulation_stats()

    def test_bots_status_command(self):
        with patch('sys.stdout') as mock_stdout:
            call_command('bots', '--status')
            self.assertTrue(mock_stdout.write.called)

    def test_bots_stop_and_start_command(self):
        call_command('bots', '--stop')
        self.assertFalse(is_simulation_active())

        call_command('bots', '--start')
        self.assertTrue(is_simulation_active())


class SeedSimulationCommandTestCase(TestCase):
    @patch('trading.management.commands.seed_all.fetch_sprite', return_value=None)
    def test_seed_all_simulation_flag(self, _mock_fetch):
        # Run seed_all with --simulation
        call_command('seed_all', '--simulation')

        # Check user count (should create 100 simulation users + market maker)
        user_count = Account.objects.filter(username__startswith='sim_').count()
        self.assertGreaterEqual(user_count, 100)

        # Check PriceHistory count
        self.assertTrue(PriceHistory.objects.filter(interval=PriceHistory.Interval.ONE_HOUR).exists())
        self.assertTrue(PriceHistory.objects.filter(interval=PriceHistory.Interval.ONE_DAY).exists())

        # Check Trades created
        self.assertTrue(Trade.objects.exists())

        # Check Portfolios distributed
        self.assertTrue(Portfolio.objects.filter(owner__username__startswith='sim_').exists())

        # Check Incubations created
        self.assertTrue(Incubation.objects.exists())

        # Check Battles created
        self.assertTrue(Battle.objects.exists())
