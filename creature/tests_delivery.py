from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from threading import Barrier, Thread
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from account.models import Account
from trading.models import Notification, Portfolio

from .models import (
    Ability,
    Battle,
    Creature,
    EggTemplate,
    Incubation,
    TrainingSession,
)
from .services import BattleService, IncubationService, TrainingService


class DeliveryFixtureMixin:
    def make_user(self, username='delivery-user'):
        user = Account.objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password='testpass123',
        )
        user.balance_cents = 100_000
        user.save(update_fields=['balance_cents'])
        return user

    def make_creature(self, name='Deliverymon', with_ability=True):
        creature = Creature.objects.create(
            name=name,
            type='normal',
            current_price=Decimal('100.00'),
            previous_close=Decimal('100.00'),
            hp=100,
            attack=50,
            defense=50,
            special_attack=50,
            special_defense=50,
            speed=50,
            battle_cooldown=timedelta(hours=1),
            cooldown_expires_at=timezone.now() - timedelta(hours=1),
        )
        if with_ability:
            ability, _ = Ability.objects.get_or_create(
                name='Delivery Strike',
                defaults={
                    'ability_type': 'normal',
                    'damage_class': 'physical',
                    'power': 10,
                },
            )
            creature.abilities.add(ability)
        return creature

    def make_egg(self, creature):
        egg = EggTemplate.objects.create(
            name=f'{creature.name} Egg',
            price=Decimal('50.00'),
            hatch_duration=timedelta(minutes=5),
        )
        egg.creature_pool.add(creature)
        return egg


class AsyncMigrationCompatibilityTests(TestCase):
    def test_existing_processed_battles_receive_idempotency_marker(self):
        processed = Battle.objects.create(
            status='finished',
            creature_1_elo_after=1016,
            creature_2_elo_after=984,
        )
        incomplete = Battle.objects.create(status='finished')
        migration = import_module(
            'creature.migrations.'
            '0007_trainingsession_alter_battleparticipant_options_and_more'
        )

        migration.mark_existing_battle_results_processed(
            import_module('django.apps').apps,
            None,
        )

        processed.refresh_from_db()
        incomplete.refresh_from_db()
        self.assertIsNotNone(processed.result_processed_at)
        self.assertIsNone(incomplete.result_processed_at)


class SchedulingDeliveryTests(DeliveryFixtureMixin, TransactionTestCase):
    reset_sequences = True

    @patch('creature.tasks.complete_training.apply_async')
    @patch('creature.tasks.hatch_egg.apply_async')
    def test_subsecond_deadlines_are_not_scheduled_early(
        self,
        hatch_async,
        training_async,
    ):
        now = timezone.now()
        execute_at = now + timedelta(milliseconds=500)
        user = self.make_user()
        creature = self.make_creature()
        egg = self.make_egg(creature)
        portfolio = Portfolio.objects.create(
            owner=user,
            creature=creature,
            quantity=Decimal('1'),
            average_cost=creature.current_price,
        )
        incubation = Incubation.objects.create(
            user=user,
            egg_template=egg,
            hatches_at=execute_at,
        )
        session = TrainingSession.objects.create(
            user=user,
            creature=creature,
            portfolio=portfolio,
            cost_cents=1_000,
            execute_at=execute_at,
        )
        hatch_async.return_value = MagicMock(id='hatch-task')
        training_async.return_value = MagicMock(id='training-task')

        with patch('creature.services.timezone.now', return_value=now):
            IncubationService.schedule_hatch(incubation.pk)
            TrainingService.schedule_training(session.pk)

        self.assertEqual(hatch_async.call_args.kwargs['countdown'], 1)
        self.assertEqual(training_async.call_args.kwargs['countdown'], 1)

    @patch('creature.tasks.hatch_egg.apply_async')
    def test_incubation_survives_scheduling_failure(self, apply_async):
        apply_async.side_effect = ConnectionError('broker unavailable')
        user = self.make_user()
        creature = self.make_creature()
        egg = self.make_egg(creature)

        incubation = IncubationService.purchase_egg(user, egg)

        user.refresh_from_db()
        incubation.refresh_from_db()
        self.assertEqual(user.balance_cents, 95_000)
        self.assertEqual(
            incubation.scheduling_status,
            Incubation.SchedulingStatus.FAILED,
        )
        self.assertEqual(incubation.scheduling_attempts, 1)
        self.assertIn('broker unavailable', incubation.scheduling_error)

    @patch('creature.tasks.complete_training.apply_async')
    def test_training_survives_scheduling_failure(self, apply_async):
        from .models import TrainingSession

        apply_async.side_effect = ConnectionError('broker unavailable')
        user = self.make_user()
        creature = self.make_creature()
        portfolio = Portfolio.objects.create(
            owner=user,
            creature=creature,
            quantity=Decimal('1'),
            average_cost=creature.current_price,
        )

        session = TrainingService.train_creature(user, creature, portfolio)

        user.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(user.balance_cents, 99_000)
        self.assertEqual(session.status, TrainingSession.Status.PENDING)
        self.assertEqual(
            session.scheduling_status,
            TrainingSession.SchedulingStatus.FAILED,
        )
        self.assertEqual(session.scheduling_attempts, 1)
        self.assertIn('broker unavailable', session.scheduling_error)


class IncubationIdempotencyTests(DeliveryFixtureMixin, TestCase):
    def test_empty_egg_pool_is_rejected_before_debit(self):
        user = self.make_user()
        original_balance = user.balance_cents
        egg = EggTemplate.objects.create(
            name='Empty Delivery Egg',
            price=Decimal('50.00'),
            hatch_duration=timedelta(minutes=5),
        )

        with self.assertRaises(ValidationError):
            IncubationService.purchase_egg(user, egg)

        user.refresh_from_db()
        self.assertEqual(user.balance_cents, original_balance)
        self.assertFalse(Incubation.objects.filter(user=user).exists())

    def test_double_hatch_updates_existing_portfolio_once(self):
        user = self.make_user()
        creature = self.make_creature()
        egg = self.make_egg(creature)
        portfolio = Portfolio.objects.create(
            owner=user,
            creature=creature,
            quantity=Decimal('2'),
            average_cost=Decimal('80.00'),
        )
        incubation = Incubation.objects.create(
            user=user,
            egg_template=egg,
            hatches_at=timezone.now() - timedelta(minutes=1),
        )

        IncubationService.hatch_egg(incubation.pk)
        IncubationService.hatch_egg(incubation.pk)

        incubation.refresh_from_db()
        portfolio.refresh_from_db()
        self.assertEqual(incubation.status, Incubation.Status.HATCHED)
        self.assertEqual(portfolio.quantity, Decimal('3'))
        self.assertEqual(
            Notification.objects.filter(
                user=user,
                notification_type=Notification.Type.INCUBATION_COMPLETE,
            ).count(),
            1,
        )


class TrainingIdempotencyTests(DeliveryFixtureMixin, TestCase):
    def test_training_cost_rounds_half_up_to_one_cent(self):
        creature = self.make_creature()
        creature.current_price = Decimal('0.05')

        self.assertEqual(
            TrainingService.get_training_cost(creature),
            Decimal('0.01'),
        )

    @patch('creature.tasks.complete_training.apply_async')
    def test_double_completion_applies_boost_price_and_notification_once(
        self,
        apply_async,
    ):
        from .models import TrainingSession

        apply_async.return_value = MagicMock(id='training-task')
        user = self.make_user()
        creature = self.make_creature()
        portfolio = Portfolio.objects.create(
            owner=user,
            creature=creature,
            quantity=Decimal('1'),
            average_cost=creature.current_price,
        )
        with self.captureOnCommitCallbacks(execute=True):
            session = TrainingService.train_creature(
                user,
                creature,
                portfolio,
            )
        session.execute_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=['execute_at'])
        initial_attack = creature.attack

        with (
            patch.object(
                TrainingService,
                'get_possible_stat_boosts',
                return_value=3,
            ),
            patch('creature.services.random.choice', return_value='attack'),
            patch(
                'trading.services.PricingEngine.update_creature_price'
            ) as update_price,
        ):
            TrainingService.complete_training(session.pk)
            TrainingService.complete_training(session.pk)

        creature.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(creature.attack, initial_attack + 3)
        self.assertEqual(session.status, TrainingSession.Status.COMPLETED)
        self.assertEqual(session.result_stat, 'attack')
        self.assertEqual(session.result_boost, 3)
        self.assertEqual(update_price.call_count, 1)
        self.assertEqual(
            Notification.objects.filter(
                user=user,
                notification_type=Notification.Type.TRAINING_COMPLETE,
            ).count(),
            1,
        )

    def test_legacy_two_argument_training_message_is_idempotent(self):
        user = self.make_user()
        creature = self.make_creature()
        portfolio = Portfolio.objects.create(
            owner=user,
            creature=creature,
            quantity=Decimal('1'),
            average_cost=creature.current_price,
        )
        initial_attack = creature.attack

        with (
            patch.object(
                TrainingService,
                'get_possible_stat_boosts',
                return_value=2,
            ),
            patch('creature.services.random.choice', return_value='attack'),
            patch(
                'trading.services.PricingEngine.update_creature_price'
            ),
        ):
            first = TrainingService.complete_legacy_training(
                creature.pk,
                portfolio.pk,
                task_id='legacy-training-task',
            )
            second = TrainingService.complete_legacy_training(
                creature.pk,
                portfolio.pk,
                task_id='legacy-training-task',
            )

        creature.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(creature.attack, initial_attack + 2)
        self.assertEqual(
            TrainingSession.objects.filter(
                celery_task_id='legacy-training-task'
            ).count(),
            1,
        )


class BattleIdempotencyTests(DeliveryFixtureMixin, TestCase):
    def setUp(self):
        self.creature_1 = self.make_creature('Battle Delivery One')
        self.creature_2 = self.make_creature('Battle Delivery Two')

    def test_same_expected_turn_is_processed_once(self):
        battle = BattleService.start_battle(
            self.creature_1,
            self.creature_2,
        )

        with (
            patch('creature.services.random.choice') as choose,
            patch('creature.services.random.randint', return_value=100),
        ):
            choose.side_effect = lambda values: list(values)[0]
            BattleService.process_turn(battle.pk, expected_turn=1)
            BattleService.process_turn(battle.pk, expected_turn=1)

        battle.refresh_from_db()
        self.assertEqual(battle.actions.filter(turn_number=1).count(), 1)
        self.assertEqual(battle.current_turn, 2)

    def test_double_result_is_applied_once(self):
        user = self.make_user()
        Portfolio.objects.create(
            owner=user,
            creature=self.creature_1,
            quantity=Decimal('1'),
            average_cost=self.creature_1.current_price,
        )
        battle = BattleService.start_battle(
            self.creature_1,
            self.creature_2,
        )
        participant_1, participant_2 = battle.participants.all()
        battle.record_action(
            participant_1,
            participant_2,
            participant_1.creature.abilities.first(),
            participant_2.current_hp,
        )

        with patch(
            'trading.services.PricingEngine.update_creature_price'
        ) as update_price:
            BattleService.process_battle_result(battle.pk)
            BattleService.process_battle_result(battle.pk)

        self.creature_1.refresh_from_db()
        self.creature_2.refresh_from_db()
        battle.refresh_from_db()
        self.assertEqual(self.creature_1.wins, 1)
        self.assertEqual(self.creature_2.losses, 1)
        self.assertIsNotNone(battle.result_processed_at)
        self.assertEqual(update_price.call_count, 2)
        self.assertEqual(
            Notification.objects.filter(
                user=user,
                notification_type=Notification.Type.BATTLE_RESULT,
            ).count(),
            1,
        )

    def test_creature_without_abilities_does_not_remain_reserved(self):
        self.creature_1.abilities.clear()
        battle = BattleService.start_battle(
            self.creature_1,
            self.creature_2,
        )

        BattleService.process_turn(battle.pk, expected_turn=1)

        battle.refresh_from_db()
        self.creature_1.refresh_from_db()
        self.creature_2.refresh_from_db()
        self.assertEqual(battle.status, 'finished')
        self.assertFalse(self.creature_1.currently_in_battle)
        self.assertFalse(self.creature_2.currently_in_battle)

    def test_repeated_matchmaking_does_not_reuse_reserved_creatures(self):
        first = BattleService.matchmake_random_battle()
        second = BattleService.matchmake_random_battle()

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(Battle.objects.filter(status='active').count(), 1)


class RecoveryTests(DeliveryFixtureMixin, TestCase):
    @patch.object(BattleService, 'process_turn')
    def test_battle_recovery_can_run_without_celery(
        self,
        process_turn,
    ):
        creature_1 = self.make_creature('Sync Recovery One')
        creature_2 = self.make_creature('Sync Recovery Two')
        battle = BattleService.start_battle(creature_1, creature_2)
        battle.next_turn_at = timezone.now() - timedelta(seconds=1)
        battle.save(update_fields=['next_turn_at'])

        recovered = BattleService.recover_battles()

        self.assertEqual(recovered, 1)
        process_turn.assert_called_once_with(
            battle.pk,
            battle.current_turn,
        )

    @patch('creature.tasks.complete_training.apply_async')
    @patch('creature.tasks.hatch_egg.apply_async')
    def test_recovery_does_not_duplicate_recent_scheduled_tasks(
        self,
        hatch_async,
        training_async,
    ):
        user = self.make_user()
        creature = self.make_creature()
        egg = self.make_egg(creature)
        recent_attempt = timezone.now() - timedelta(minutes=1)
        Incubation.objects.create(
            user=user,
            egg_template=egg,
            hatches_at=timezone.now() - timedelta(seconds=1),
            scheduling_status=Incubation.SchedulingStatus.SCHEDULED,
            last_scheduling_attempt_at=recent_attempt,
            celery_task_id='hatch-already-scheduled',
        )
        portfolio = Portfolio.objects.create(
            owner=user,
            creature=creature,
            quantity=Decimal('1'),
            average_cost=creature.current_price,
        )
        TrainingSession.objects.create(
            user=user,
            creature=creature,
            portfolio=portfolio,
            cost_cents=1_000,
            execute_at=timezone.now() - timedelta(seconds=1),
            scheduling_status=TrainingSession.SchedulingStatus.SCHEDULED,
            last_scheduling_attempt_at=recent_attempt,
            celery_task_id='training-already-scheduled',
        )

        incubation_count = IncubationService.recover_scheduling()
        training_count = TrainingService.recover_scheduling()

        self.assertEqual(incubation_count, 0)
        self.assertEqual(training_count, 0)
        hatch_async.assert_not_called()
        training_async.assert_not_called()

    def test_permanent_hatch_failure_refunds_user_once(self):
        from .tasks import hatch_egg

        user = self.make_user()
        original_balance = user.balance_cents
        creature = self.make_creature()
        egg = self.make_egg(creature)
        incubation = IncubationService.purchase_egg(user, egg)
        Incubation.objects.filter(pk=incubation.pk).update(
            hatches_at=timezone.now() - timedelta(seconds=1)
        )
        egg.creature_pool.clear()

        result = hatch_egg.run(str(incubation.pk))
        second_result = hatch_egg.run(str(incubation.pk))

        incubation.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(incubation.status, Incubation.Status.FAILED)
        self.assertIsNotNone(incubation.failed_at)
        self.assertIn('No creatures in pool', incubation.failure_reason)
        self.assertEqual(user.balance_cents, original_balance)
        self.assertIn('refunded', result)
        self.assertIn('already resolved', second_result)

    @patch('creature.tasks.process_battle_turn.apply_async')
    @patch('creature.tasks.complete_training.apply_async')
    @patch('creature.tasks.hatch_egg.apply_async')
    def test_recovery_reschedules_due_operations_and_finishes_results(
        self,
        hatch_async,
        training_async,
        turn_async,
    ):
        from .models import TrainingSession
        from .tasks import recover_creature_operations

        hatch_async.return_value = MagicMock(id='hatch-recovery')
        training_async.return_value = MagicMock(id='training-recovery')
        turn_async.return_value = MagicMock(id='turn-recovery')
        user = self.make_user()
        creature_1 = self.make_creature('Recovery One')
        creature_2 = self.make_creature('Recovery Two')
        egg = self.make_egg(creature_1)
        incubation = Incubation.objects.create(
            user=user,
            egg_template=egg,
            hatches_at=timezone.now() - timedelta(minutes=1),
        )
        portfolio = Portfolio.objects.create(
            owner=user,
            creature=creature_1,
            quantity=Decimal('1'),
            average_cost=creature_1.current_price,
        )
        session = TrainingSession.objects.create(
            user=user,
            creature=creature_1,
            portfolio=portfolio,
            cost_cents=1_000,
            execute_at=timezone.now() - timedelta(seconds=1),
        )
        active_battle = BattleService.start_battle(creature_1, creature_2)
        active_battle.next_turn_at = timezone.now() - timedelta(seconds=1)
        active_battle.save(update_fields=['next_turn_at'])
        finished_creature_1 = self.make_creature('Recovery Finished One')
        finished_creature_2 = self.make_creature('Recovery Finished Two')
        finished_battle = BattleService.start_battle(
            finished_creature_1,
            finished_creature_2,
        )
        participant_1, participant_2 = finished_battle.participants.all()
        finished_battle.record_action(
            participant_1,
            participant_2,
            participant_1.creature.abilities.first(),
            participant_2.current_hp,
        )

        result = recover_creature_operations()

        incubation.refresh_from_db()
        session.refresh_from_db()
        finished_battle.refresh_from_db()
        self.assertEqual(
            incubation.scheduling_status,
            Incubation.SchedulingStatus.SCHEDULED,
        )
        self.assertEqual(
            session.scheduling_status,
            TrainingSession.SchedulingStatus.SCHEDULED,
        )
        self.assertEqual(result['incubations'], 1)
        self.assertEqual(result['trainings'], 1)
        self.assertEqual(result['battles'], 2)
        self.assertIsNotNone(finished_battle.result_processed_at)
        hatch_async.assert_called_once()
        training_async.assert_called_once()
        turn_async.assert_called_once_with(
            args=[active_battle.pk, active_battle.current_turn],
            countdown=0,
        )


class ConcurrentMatchmakingTests(
    DeliveryFixtureMixin,
    TransactionTestCase,
):
    reset_sequences = True

    def test_concurrent_matchmakers_never_share_a_creature(self):
        for number in range(4):
            self.make_creature(f'Concurrent {number}')
        barrier = Barrier(2)
        battle_ids = []
        errors = []

        def matchmake():
            close_old_connections()
            try:
                barrier.wait()
                battle = BattleService.matchmake_random_battle()
                if battle:
                    battle_ids.append(battle.pk)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=matchmake) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        creature_ids = list(
            Battle.objects.filter(pk__in=battle_ids)
            .values_list('participants__creature_id', flat=True)
        )
        self.assertEqual(len(creature_ids), len(set(creature_ids)))
