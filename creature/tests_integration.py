from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone

from account.models import Account
from .models import Creature, Ability, EggTemplate, Incubation, Battle, BattleInvestment
from .services import IncubationService, TrainingService, BattleService
from .tasks import matchmake_random_battle, process_battle_turn
from trading.models import Portfolio, Notification


class IncubationIntegrationTests(TestCase):
    """Integration tests for the incubation system."""

    def setUp(self):
        self.celery_patcher = patch('creature.tasks.hatch_egg.apply_async')
        self.mock_async = self.celery_patcher.start()
        self.mock_async.return_value = MagicMock(id='fake-task-id')
        self.addCleanup(self.celery_patcher.stop)

        self.client = Client()
        self.user = Account.objects.create_user(
            username='incubator',
            email='inc@test.com',
            password='testpass123',
        )
        self.user.balance_cents = 100_000_00
        self.user.save()

        self.ability = Ability.objects.create(
            name='Tackle', ability_type='normal',
            damage_class='physical', power=40,
        )

        self.creature = Creature.objects.create(
            name='Bulbasaur', type='grass',
            current_price=Decimal('120.00'),
            previous_close=Decimal('110.00'),
            hp=45, attack=49, defense=49,
            special_attack=65, special_defense=65, speed=45,
            battle_cooldown=timedelta(hours=1),
            description='A test creature.',
        )
        self.creature.abilities.add(self.ability)

        self.egg = EggTemplate.objects.create(
            name='Starter Egg',
            description='A mysterious egg that may contain a starter.',
            price=Decimal('500.00'),
            hatch_duration=timedelta(hours=1),
        )
        self.egg.creature_pool.add(self.creature)

    def _login(self):
        self.client.login(username='incubator', password='testpass123')

    def test_incubation_shop_requires_login(self):
        response = self.client.get(reverse('creature:incubation_shop'))
        self.assertEqual(response.status_code, 302)

    def test_incubation_shop_shows_eggs(self):
        self._login()
        response = self.client.get(reverse('creature:incubation_shop'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Starter Egg')
        self.assertContains(response, '$500.00')

    def test_purchase_egg_deducts_balance(self):
        self._login()
        response = self.client.post(
            reverse('creature:purchase_egg', args=[self.egg.pk])
        )
        self.assertRedirects(response, reverse('creature:incubation_shop'))

        self.user.refresh_from_db()
        expected_balance = 100_000_00 - 500_00
        self.assertEqual(self.user.balance_cents, expected_balance)

    def test_purchase_egg_creates_incubation(self):
        self._login()
        self.client.post(
            reverse('creature:purchase_egg', args=[self.egg.pk])
        )
        incubation = Incubation.objects.get(user=self.user)
        self.assertEqual(incubation.egg_template, self.egg)
        self.assertEqual(incubation.status, Incubation.Status.INCUBATING)

    def test_purchase_egg_insufficient_funds(self):
        self._login()
        self.user.balance_cents = 100
        self.user.save()

        response = self.client.post(
            reverse('creature:purchase_egg', args=[self.egg.pk]),
            follow=True,
        )
        self.assertContains(response, 'Insufficient funds')

    def test_incubation_status_page_shows_incubations(self):
        self._login()
        self.client.post(
            reverse('creature:purchase_egg', args=[self.egg.pk])
        )
        response = self.client.get(reverse('creature:incubation_status'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Starter Egg')
        self.assertContains(response, 'Incubating')

    @patch('celery.app.task.Task.apply_async')
    def test_hatch_egg_creates_portfolio(self, mock_async):
        mock_async.return_value = MagicMock(id='fake-task-id')

        incubation = Incubation.objects.create(
            user=self.user,
            egg_template=self.egg,
            hatches_at=timezone.now() - timedelta(minutes=1),
            status=Incubation.Status.INCUBATING,
        )

        result = IncubationService.hatch_egg(incubation)

        incubation.refresh_from_db()
        self.assertEqual(incubation.status, Incubation.Status.HATCHED)
        self.assertIsNotNone(incubation.hatched_creature)
        self.assertEqual(incubation.hatched_creature, self.creature)

        from trading.models import Portfolio
        portfolio = Portfolio.objects.get(
            owner=self.user,
            creature=self.creature,
        )
        self.assertEqual(portfolio.quantity, Decimal('1'))

    def test_incubation_status_shows_hatched(self):
        self._login()

        incubation = Incubation.objects.create(
            user=self.user,
            egg_template=self.egg,
            hatches_at=timezone.now() - timedelta(hours=2),
            status=Incubation.Status.HATCHED,
            hatched_at=timezone.now(),
            hatched_creature=self.creature,
        )

        response = self.client.get(reverse('creature:incubation_status'))
        self.assertContains(response, 'Hatched')
        self.assertContains(response, 'Bulbasaur')


class TrainingIntegrationTests(TestCase):
    """Integration tests for the training system."""

    def setUp(self):
        self.celery_patcher = patch('creature.tasks.complete_training.apply_async')
        self.mock_async = self.celery_patcher.start()
        self.mock_async.return_value = MagicMock(id='fake-task-id')
        self.addCleanup(self.celery_patcher.stop)

        self.client = Client()
        self.user = Account.objects.create_user(
            username='trainer',
            email='tr@test.com',
            password='testpass123',
        )
        self.user.balance_cents = 100_000_00
        self.user.save()

        self.ability = Ability.objects.create(
            name='Scratch', ability_type='normal',
            damage_class='physical', power=40,
        )

        self.creature = Creature.objects.create(
            name='Charmander', type='fire',
            current_price=Decimal('200.00'),
            previous_close=Decimal('190.00'),
            hp=39, attack=52, defense=43,
            special_attack=60, special_defense=50, speed=65,
            battle_cooldown=timedelta(hours=1),
            description='A fire lizard.',
        )
        self.creature.abilities.add(self.ability)

        self.portfolio = Portfolio.objects.create(
            owner=self.user,
            creature=self.creature,
            quantity=Decimal('3'),
            average_cost=Decimal('200.00'),
        )

    def _login(self):
        self.client.login(username='trainer', password='testpass123')

    def test_training_requires_login(self):
        response = self.client.post(
            reverse('creature:train_creature', args=[self.portfolio.pk])
        )
        self.assertEqual(response.status_code, 302)

    def test_training_deducts_balance(self):
        self._login()
        response = self.client.post(
            reverse('creature:train_creature', args=[self.portfolio.pk])
        )
        self.assertRedirects(response, reverse('trading:portfolio'))

        self.user.refresh_from_db()
        expected_cost = int((self.creature.current_price * Decimal('0.10')) * 100)
        expected_balance = 100_000_00 - expected_cost
        self.assertEqual(self.user.balance_cents, expected_balance)

    def test_training_insufficient_funds(self):
        self._login()
        self.user.balance_cents = 100
        self.user.save()

        response = self.client.post(
            reverse('creature:train_creature', args=[self.portfolio.pk]),
            follow=True,
        )
        self.assertContains(response, 'Insufficient funds')

    def test_training_calls_celery_task(self):
        self._login()
        self.client.post(
            reverse('creature:train_creature', args=[self.portfolio.pk])
        )
        self.mock_async.assert_called_once()

    def test_training_not_own_portfolio_404(self):
        other = Account.objects.create_user(
            username='other', email='other@test.com', password='testpass123',
        )
        self.client.login(username='other', password='testpass123')
        response = self.client.post(
            reverse('creature:train_creature', args=[self.portfolio.pk])
        )
        self.assertEqual(response.status_code, 404)


class BattleServiceIntegrationTests(TestCase):
    """Integration tests for the battle service."""

    def setUp(self):
        self.ability = Ability.objects.create(
            name='Tackle', ability_type='normal',
            damage_class='physical', power=40,
        )

        self.creature_1 = Creature.objects.create(
            name='Charmander', type='fire',
            current_price=Decimal('100.00'),
            hp=39, attack=52, defense=43,
            battle_cooldown=timedelta(hours=1),
            cooldown_expires_at=timezone.now() - timedelta(hours=1),
            description='A fire lizard.',
            elo_rating=1000,
        )
        self.creature_1.abilities.add(self.ability)

        self.creature_2 = Creature.objects.create(
            name='Squirtle', type='water',
            current_price=Decimal('100.00'),
            hp=44, attack=48, defense=65,
            battle_cooldown=timedelta(hours=1),
            cooldown_expires_at=timezone.now() - timedelta(hours=1),
            description='A water turtle.',
            elo_rating=1200,
        )
        self.creature_2.abilities.add(self.ability)

        self.user = Account.objects.create_user(
            username='battlefan',
            email='bf@test.com',
            password='testpass123',
        )
        self.user.balance_cents = 100_000_00
        self.user.save()

    def test_calculate_elo_change_equal_rating(self):
        """Equal ratings: expected score = 0.5, change = K * (1 - 0.5) = 16"""
        change = BattleService.calculate_elo_change(1000, 1000, 1)
        self.assertEqual(change, 16)

    def test_calculate_elo_change_stronger_wins(self):
        """Stronger (1200) beats weaker (1000): expected ≈ 0.76, change ≈ 32 * 0.24 = 7"""
        change = BattleService.calculate_elo_change(1200, 1000, 1)
        self.assertEqual(change, 8)  # 32 * (1 - 0.76) = 7.68 ≈ 8

    def test_calculate_elo_change_weaker_wins(self):
        """Weaker (1000) beats stronger (1200): expected ≈ 0.24, change ≈ 32 * 0.76 = 24"""
        change = BattleService.calculate_elo_change(1000, 1200, 1)
        self.assertEqual(change, 24)

    def test_calculate_potential_change(self):
        """Weaker creature has higher win potential (bigger upset = bigger gain)"""
        win_pct, lose_pct = BattleService.calculate_potential_change(1000, 1200)
        self.assertGreater(win_pct, 0)
        self.assertLess(lose_pct, 0)
        # Weaker creature's win potential should be > 2.5%
        self.assertGreater(win_pct, Decimal('2.50'))
        # Stronger creature's win potential should be < 2.5%
        strong_win, strong_lose = BattleService.calculate_potential_change(1200, 1000)
        self.assertLess(strong_win, Decimal('2.50'))

    def test_start_battle_sets_elo_fields(self):
        battle = BattleService.start_battle(self.creature_1, self.creature_2)
        self.assertEqual(battle.creature_1_elo_before, 1000)
        self.assertEqual(battle.creature_2_elo_before, 1200)
        self.assertIsNotNone(battle.creature_1_potential_change)
        self.assertIsNotNone(battle.creature_2_potential_change)
        expected_next = timezone.now() + timedelta(seconds=180)
        self.assertAlmostEqual(
            battle.next_turn_at.timestamp(),
            expected_next.timestamp(),
            delta=1
        )
        self.assertEqual(battle.status, 'active')

    def test_process_battle_result_updates_elo_and_wins(self):
        battle = Battle.objects.start_battle(self.creature_1, self.creature_2)
        p1 = battle.participants.get(creature=self.creature_1)
        p2 = battle.participants.get(creature=self.creature_2)

        battle.record_action(
            actor_participant=p1, target_participant=p2,
            ability=self.ability, damage=50
        )
        battle.refresh_from_db()
        self.assertEqual(battle.status, 'finished')

        c1_elo_before = self.creature_1.elo_rating
        c2_elo_before = self.creature_2.elo_rating

        BattleService.process_battle_result(battle)

        self.creature_1.refresh_from_db()
        self.creature_2.refresh_from_db()

        self.assertEqual(self.creature_1.wins, 1)
        self.assertEqual(self.creature_2.losses, 1)
        self.assertGreater(self.creature_1.elo_rating, c1_elo_before)
        self.assertLess(self.creature_2.elo_rating, c2_elo_before)

        battle.refresh_from_db()
        self.assertIsNotNone(battle.creature_1_elo_after)
        self.assertIsNotNone(battle.creature_2_elo_after)

    def test_process_battle_result_creates_notifications(self):
        portfolio = Portfolio.objects.create(
            owner=self.user,
            creature=self.creature_1,
            quantity=Decimal('5'),
            average_cost=Decimal('100.00'),
        )

        battle = Battle.objects.start_battle(self.creature_1, self.creature_2)
        p1 = battle.participants.get(creature=self.creature_1)
        p2 = battle.participants.get(creature=self.creature_2)
        battle.record_action(
            actor_participant=p1, target_participant=p2,
            ability=self.ability, damage=50
        )
        battle.refresh_from_db()

        BattleService.process_battle_result(battle)

        notif = Notification.objects.filter(
            user=self.user,
            notification_type=Notification.Type.BATTLE_RESULT,
        )
        self.assertEqual(notif.count(), 1)
        self.assertIn('won', notif.first().title)

    def test_record_turn_investments(self):
        battle = Battle.objects.start_battle(self.creature_1, self.creature_2)
        Portfolio.objects.create(
            owner=self.user,
            creature=self.creature_1,
            quantity=Decimal('3'),
            average_cost=Decimal('100.00'),
        )

        BattleService.record_turn_investments(battle, 1)

        inv = BattleInvestment.objects.filter(battle=battle, turn_number=1)
        self.assertEqual(inv.count(), 2)
        c1_inv = inv.get(creature=self.creature_1)
        self.assertGreaterEqual(c1_inv.investor_count, 1)

    def test_get_active_battles_returns_active_only(self):
        battle = BattleService.start_battle(self.creature_1, self.creature_2)
        active = BattleService.get_active_battles()
        self.assertIn(battle, active)
        self.assertEqual(len(active), 1)

    def test_get_leaderboard_empty(self):
        lb = BattleService.get_leaderboard()
        self.assertEqual(len(lb), 0)

    def test_get_leaderboard_with_data(self):
        self.creature_1.wins = 10
        self.creature_1.losses = 2
        self.creature_1.save()
        self.creature_2.wins = 5
        self.creature_2.losses = 8
        self.creature_2.save()

        lb = BattleService.get_leaderboard()
        self.assertEqual(len(lb), 2)
        self.assertEqual(lb[0].name, 'Charmander')

    def test_get_battle_detail_returns_none_for_missing(self):
        self.assertIsNone(BattleService.get_battle_detail(9999))

    def test_get_user_creature_battles(self):
        Portfolio.objects.create(
            owner=self.user, creature=self.creature_1,
            quantity=Decimal('5'), average_cost=Decimal('100'),
        )
        battle = BattleService.start_battle(self.creature_1, self.creature_2)
        user_battles = BattleService.get_user_creature_battles(self.user)
        self.assertIn(battle, user_battles)

    def test_get_creature_battle_history(self):
        battle = BattleService.start_battle(self.creature_1, self.creature_2)
        history = BattleService.get_creature_battle_history(self.creature_1)
        self.assertIn(battle, history)

    def test_get_historical_battles(self):
        battle = BattleService.start_battle(self.creature_1, self.creature_2)
        # Finish the battle
        p1 = battle.participants.get(creature=self.creature_1)
        p2 = battle.participants.get(creature=self.creature_2)
        battle.record_action(
            actor_participant=p1, target_participant=p2,
            ability=self.ability, damage=50
        )
        battle.refresh_from_db()

        historical = BattleService.get_historical_battles()
        self.assertIn(battle, historical)


class BattleTaskIntegrationTests(TestCase):
    """Integration tests for Celery battle tasks."""

    def setUp(self):
        self.ability = Ability.objects.create(
            name='Tackle', ability_type='normal',
            damage_class='physical', power=40,
        )
        self.creature_1 = Creature.objects.create(
            name='Charmander', type='fire',
            current_price=Decimal('100.00'), hp=39,
            attack=52, defense=43,
            battle_cooldown=timedelta(hours=1),
            cooldown_expires_at=timezone.now() - timedelta(hours=1),
            description='A fire lizard.',
        )
        self.creature_1.abilities.add(self.ability)
        self.creature_2 = Creature.objects.create(
            name='Squirtle', type='water',
            current_price=Decimal('100.00'), hp=44,
            attack=48, defense=65,
            battle_cooldown=timedelta(hours=1),
            cooldown_expires_at=timezone.now() - timedelta(hours=1),
            description='A water turtle.',
        )
        self.creature_2.abilities.add(self.ability)

    @patch('creature.tasks.process_battle_turn.apply_async')
    def test_matchmake_random_battle_starts_battle(self, mock_async):
        result = matchmake_random_battle()
        self.assertIn('initialized', result)
        self.assertTrue(Battle.objects.filter(status='active').exists())

    @patch('creature.tasks.process_battle_turn.apply_async')
    def test_matchmake_random_battle_not_enough(self, mock_async):
        self.creature_1.currently_in_battle = True
        self.creature_1.save()
        self.creature_2.currently_in_battle = True
        self.creature_2.save()
        result = matchmake_random_battle()
        self.assertIn('Not enough', result)

    @patch('creature.tasks.process_battle_turn.apply_async')
    def test_process_battle_turn_active(self, mock_async):
        battle = BattleService.start_battle(self.creature_1, self.creature_2)
        from django.conf import settings
        turn_interval = getattr(settings, 'BATTLE_TURN_INTERVAL', 180)

        with self.settings(CELERY_TASK_ALWAYS_EAGER=False):
            result = process_battle_turn(battle.id)

        self.assertIn('Next turn', result)
        mock_async.assert_called_once()
        args, kwargs = mock_async.call_args
        self.assertEqual(kwargs['countdown'], turn_interval)

    @patch('creature.tasks.process_battle_turn.apply_async')
    def test_process_battle_turn_finishes_battle(self, mock_async):
        battle = BattleService.start_battle(self.creature_1, self.creature_2)
        p1 = battle.participants.get(creature=self.creature_1)
        p2 = battle.participants.get(creature=self.creature_2)

        battle.record_action(
            actor_participant=p1, target_participant=p2,
            ability=self.ability, damage=50
        )
        battle.refresh_from_db()
        self.assertEqual(battle.status, 'finished')
