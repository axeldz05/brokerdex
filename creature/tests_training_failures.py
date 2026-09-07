from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Thread
from unittest.mock import patch

from celery.exceptions import Retry
from django.db import OperationalError, close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from account.models import Account
from trading.models import Notification, Portfolio

from .models import Creature, TrainingSession
from .services import TrainingService


class TrainingFailureFixtureMixin:
    original_balance_cents = 100_000
    cost_cents = 1_000

    def create_pending_training(self, *, ready=True):
        user = Account.objects.create_user(
            username='p22-trainer',
            email='p22-trainer@example.com',
            password='testpass123',
        )
        user.balance_cents = (
            self.original_balance_cents - self.cost_cents
        )
        user.save(update_fields=['balance_cents'])
        creature = Creature.objects.create(
            name='P22 Training Creature',
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
        )
        portfolio = Portfolio.objects.create(
            owner=user,
            creature=creature,
            quantity=Decimal('1'),
            average_cost=creature.current_price,
        )
        execute_at = timezone.now() + (
            -timedelta(seconds=1) if ready else timedelta(minutes=1)
        )
        session = TrainingSession.objects.create(
            user=user,
            creature=creature,
            portfolio=portfolio,
            cost_cents=self.cost_cents,
            execute_at=execute_at,
        )
        return user, creature, session


class TrainingFailureServiceTests(TrainingFailureFixtureMixin, TestCase):
    def test_fail_training_refunds_and_marks_terminal_exactly_once(self):
        user, _, session = self.create_pending_training()

        first, first_refunded = TrainingService.fail_training(
            session.pk,
            'permanent training failure',
        )
        second, second_refunded = TrainingService.fail_training(
            session.pk,
            'duplicate delivery',
        )

        user.refresh_from_db()
        session.refresh_from_db()
        self.assertTrue(first_refunded)
        self.assertFalse(second_refunded)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(user.balance_cents, self.original_balance_cents)
        self.assertEqual(session.status, TrainingSession.Status.FAILED)
        self.assertIsNotNone(session.failed_at)
        self.assertEqual(
            session.failure_reason,
            'permanent training failure',
        )
        self.assertEqual(session.refunded_at, session.failed_at)
        self.assertEqual(
            session.scheduling_status,
            TrainingSession.SchedulingStatus.FAILED,
        )

    def test_fail_training_rolls_back_refund_if_terminal_write_fails(self):
        user, _, session = self.create_pending_training()

        with (
            patch.object(
                TrainingSession,
                'save',
                autospec=True,
                side_effect=RuntimeError('terminal write failed'),
            ),
            self.assertRaisesMessage(
                RuntimeError,
                'terminal write failed',
            ),
        ):
            TrainingService.fail_training(session.pk, 'permanent failure')

        user.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(
            user.balance_cents,
            self.original_balance_cents - self.cost_cents,
        )
        self.assertEqual(session.status, TrainingSession.Status.PENDING)
        self.assertIsNone(session.failed_at)
        self.assertIsNone(session.refunded_at)
        self.assertEqual(session.failure_reason, '')


class TrainingFailureConcurrencyTests(
    TrainingFailureFixtureMixin,
    TransactionTestCase,
):
    def test_concurrent_failures_refund_training_once(self):
        user, _, session = self.create_pending_training()
        barrier = Barrier(2)
        outcomes = []
        errors = []

        def fail_in_worker(reason):
            close_old_connections()
            try:
                barrier.wait()
                _, refunded = TrainingService.fail_training(
                    session.pk,
                    reason,
                )
                outcomes.append(refunded)
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [
            Thread(target=fail_in_worker, args=(f'failure {index}',))
            for index in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertCountEqual(outcomes, [True, False])
        user.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(user.balance_cents, self.original_balance_cents)
        self.assertEqual(session.status, TrainingSession.Status.FAILED)
        self.assertIsNotNone(session.refunded_at)


class TrainingFailureTaskTests(TrainingFailureFixtureMixin, TestCase):
    def test_permanent_error_refunds_without_retrying(self):
        from .tasks import complete_training

        user, _, session = self.create_pending_training()

        with (
            patch.object(
                TrainingService,
                'complete_training',
                side_effect=RuntimeError('invalid training state'),
            ),
            patch.object(complete_training, 'retry') as retry,
        ):
            result = complete_training.run(str(session.pk))

        retry.assert_not_called()
        user.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(user.balance_cents, self.original_balance_cents)
        self.assertEqual(session.status, TrainingSession.Status.FAILED)
        self.assertIn('invalid training state', session.failure_reason)
        self.assertIn('refunded', result)

    def test_transient_error_retries_without_refunding(self):
        from .tasks import complete_training

        user, _, session = self.create_pending_training()

        with (
            patch.object(
                TrainingService,
                'complete_training',
                side_effect=OperationalError('database unavailable'),
            ),
            patch.object(
                complete_training,
                'retry',
                side_effect=Retry(),
            ) as retry,
            self.assertRaises(Retry),
        ):
            complete_training.run(str(session.pk))

        retry.assert_called_once()
        user.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(
            user.balance_cents,
            self.original_balance_cents - self.cost_cents,
        )
        self.assertEqual(session.status, TrainingSession.Status.PENDING)
        self.assertIsNone(session.refunded_at)

    def test_final_transient_error_rolls_back_effects_and_refunds(self):
        from .tasks import complete_training

        user, creature, session = self.create_pending_training()
        initial_attack = creature.attack

        with (
            patch.object(
                TrainingService,
                'get_possible_stat_boosts',
                return_value=3,
            ),
            patch('creature.services.random.choice', return_value='attack'),
            patch(
                'trading.services.PricingEngine.update_creature_price',
                side_effect=OperationalError('pricing database unavailable'),
            ),
            patch.object(complete_training, 'max_retries', 0),
            patch.object(complete_training, 'retry') as retry,
        ):
            result = complete_training.run(str(session.pk))

        retry.assert_not_called()
        user.refresh_from_db()
        creature.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(user.balance_cents, self.original_balance_cents)
        self.assertEqual(creature.attack, initial_attack)
        self.assertEqual(session.status, TrainingSession.Status.FAILED)
        self.assertIn(
            'pricing database unavailable',
            session.failure_reason,
        )
        self.assertFalse(
            Notification.objects.filter(
                user=user,
                notification_type=Notification.Type.TRAINING_COMPLETE,
            ).exists()
        )
        self.assertIn('refunded', result)

    def test_premature_execution_retries_without_refunding(self):
        from .tasks import complete_training

        user, _, session = self.create_pending_training(ready=False)

        with (
            patch.object(
                complete_training,
                'retry',
                side_effect=Retry(),
            ) as retry,
            self.assertRaises(Retry),
        ):
            complete_training.run(str(session.pk))

        retry.assert_called_once()
        self.assertGreaterEqual(
            retry.call_args.kwargs['countdown'],
            1,
        )
        user.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(
            user.balance_cents,
            self.original_balance_cents - self.cost_cents,
        )
        self.assertEqual(session.status, TrainingSession.Status.PENDING)
        self.assertIsNone(session.refunded_at)

    def test_double_task_delivery_completes_training_once(self):
        from .tasks import complete_training

        _, creature, session = self.create_pending_training()
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
            ) as update_price,
        ):
            first_result = complete_training.run(str(session.pk))
            second_result = complete_training.run(str(session.pk))

        creature.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(creature.attack, initial_attack + 2)
        self.assertEqual(session.status, TrainingSession.Status.COMPLETED)
        self.assertEqual(update_price.call_count, 1)
        self.assertIn('trained', first_result)
        self.assertIn('already resolved', second_result)

    def test_legacy_two_argument_task_signature_still_completes(self):
        from .tasks import complete_training

        _, creature, pending_session = self.create_pending_training()
        portfolio = pending_session.portfolio
        pending_session.delete()
        initial_attack = creature.attack

        with (
            patch.object(
                TrainingService,
                'get_possible_stat_boosts',
                return_value=1,
            ),
            patch('creature.services.random.choice', return_value='attack'),
            patch(
                'trading.services.PricingEngine.update_creature_price'
            ),
        ):
            result = complete_training.run(
                str(creature.pk),
                portfolio.pk,
            )

        creature.refresh_from_db()
        legacy_session = TrainingSession.objects.get(portfolio=portfolio)
        self.assertEqual(creature.attack, initial_attack + 1)
        self.assertEqual(
            legacy_session.status,
            TrainingSession.Status.COMPLETED,
        )
        self.assertEqual(legacy_session.cost_cents, 0)
        self.assertIn('trained', result)

    @patch('creature.tasks.complete_training.apply_async')
    def test_recovery_does_not_reschedule_failed_training(self, apply_async):
        _, _, session = self.create_pending_training()
        TrainingService.fail_training(session.pk, 'permanent failure')

        recovered = TrainingService.recover_scheduling()

        self.assertEqual(recovered, 0)
        apply_async.assert_not_called()
