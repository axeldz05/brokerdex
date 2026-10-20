from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from celery.exceptions import Retry
from django.db import OperationalError
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError

from .models import Incubation, TrainingSession
from .services import (
    HatchConfigurationError,
    TrainingNotReadyError,
)
from .tasks import (
    complete_training,
    hatch_egg,
    matchmake_random_battle,
    process_battle_turn,
    recover_creature_operations,
)


def _battle_with_participants():
    participants = MagicMock()
    participants.select_related.return_value.all.return_value = [
        SimpleNamespace(creature=SimpleNamespace(name='Alpha')),
        SimpleNamespace(creature=SimpleNamespace(name='Beta')),
    ]
    return SimpleNamespace(
        id=41,
        current_turn=1,
        participants=participants,
    )


class MatchmakingTaskTests(SimpleTestCase):
    @override_settings(BATTLE_TURN_INTERVAL=12)
    @patch('creature.tasks.process_battle_turn.apply_async')
    @patch('creature.tasks.BattleService.matchmake_random_battle')
    def test_matchmaking_chains_one_turn_and_handles_redelivery(
        self,
        matchmake,
        apply_async,
    ):
        battle = _battle_with_participants()
        matchmake.side_effect = [battle, None]

        first = matchmake_random_battle.run()
        second = matchmake_random_battle.run()

        self.assertEqual(
            first,
            'Battle 41 initialized: Alpha vs Beta',
        )
        self.assertEqual(
            second,
            'Not enough creatures to start a battle.',
        )
        apply_async.assert_called_once_with(
            args=[41, 1],
            countdown=12,
        )


class BattleTurnTaskTests(SimpleTestCase):
    @override_settings(BATTLE_TURN_INTERVAL=9)
    @patch('creature.tasks.process_battle_turn.apply_async')
    @patch('creature.tasks.BattleService.process_turn')
    def test_active_turn_chains_and_reschedules_if_already_processed(
        self,
        process_turn,
        apply_async,
    ):
        battle = SimpleNamespace(
            id=7,
            current_turn=2,
            status='active',
            winner=None,
            next_turn_at=None,
        )
        process_turn.side_effect = [
            {
                'battle': battle,
                'processed': True,
                'turn_number': 1,
            },
            {
                'battle': battle,
                'processed': False,
            },
        ]

        first = process_battle_turn.run(7, 1)
        second = process_battle_turn.run(7, 1)

        self.assertEqual(first, 'Turn 1 processed. Next turn in 9s.')
        self.assertEqual(
            second,
            'Battle 7 turn 1 was already processed.',
        )
        self.assertEqual(
            process_turn.call_args_list,
            [call(7, 1), call(7, 1)],
        )
        self.assertEqual(
            apply_async.call_args_list,
            [
                call(args=[7, 2], countdown=9),
                call(args=[7, 2], countdown=9),
            ],
        )

    @override_settings(BATTLE_TURN_INTERVAL=9)
    @patch('creature.tasks.process_battle_turn.apply_async')
    @patch('creature.tasks.BattleService.process_turn')
    def test_active_turn_already_processed_uses_remaining_countdown(
        self,
        process_turn,
        apply_async,
    ):
        battle = SimpleNamespace(
            id=7,
            current_turn=2,
            status='active',
            winner=None,
            next_turn_at=timezone.now() + timedelta(seconds=42),
        )
        process_turn.return_value = {
            'battle': battle,
            'processed': False,
        }

        result = process_battle_turn.run(7, 1)

        self.assertEqual(
            result,
            'Battle 7 turn 1 was already processed.',
        )
        self.assertEqual(apply_async.call_count, 1)
        args, kwargs = apply_async.call_args
        self.assertEqual(kwargs['args'], [7, 2])
        self.assertAlmostEqual(kwargs['countdown'], 42, delta=2)

    @patch('creature.tasks.process_battle_turn.apply_async')
    @patch('creature.tasks.BattleService.process_turn')
    def test_finished_turn_already_processed_does_not_reschedule(
        self,
        process_turn,
        apply_async,
    ):
        battle = SimpleNamespace(
            id=7,
            current_turn=3,
            status='finished',
            winner=None,
        )
        process_turn.return_value = {
            'battle': battle,
            'processed': False,
        }

        result = process_battle_turn.run(7, 2)

        self.assertEqual(
            result,
            'Battle 7 turn 2 was already processed.',
        )
        apply_async.assert_not_called()

    @patch('creature.tasks.process_battle_turn.apply_async')
    @patch('creature.tasks.BattleService.process_turn')
    def test_process_turn_retries_on_enqueue_transient_error(
        self,
        process_turn,
        apply_async,
    ):
        battle = SimpleNamespace(
            id=7,
            current_turn=2,
            status='active',
            winner=None,
            next_turn_at=None,
        )
        process_turn.return_value = {
            'battle': battle,
            'processed': True,
            'turn_number': 1,
        }
        apply_async.side_effect = RedisConnectionError('Redis down')

        with patch.object(process_battle_turn, 'retry') as mock_retry:
            mock_retry.side_effect = Retry()
            with self.assertRaises(Retry):
                process_battle_turn.run(7, 1)

            mock_retry.assert_called_once()
            self.assertIsInstance(
                mock_retry.call_args.kwargs['exc'],
                RedisConnectionError,
            )

    @patch('creature.tasks.process_battle_turn.apply_async')
    @patch('creature.tasks.BattleService.process_turn')
    def test_finished_turn_returns_winner_without_chaining(
        self,
        process_turn,
        apply_async,
    ):
        battle = SimpleNamespace(
            id=8,
            current_turn=4,
            status='finished',
            winner=SimpleNamespace(
                creature=SimpleNamespace(name='Champion')
            ),
        )
        process_turn.return_value = {
            'battle': battle,
            'processed': True,
            'turn_number': 3,
        }

        result = process_battle_turn.run(8, 3)

        self.assertEqual(
            result,
            'Battle 8 has finished! Winner: Champion',
        )
        apply_async.assert_not_called()


class HatchTaskTests(SimpleTestCase):
    @patch('creature.tasks.IncubationService.fail_hatch')
    @patch('creature.tasks.IncubationService.hatch_egg')
    def test_permanent_hatch_failure_refunds_only_first_delivery(
        self,
        hatch,
        fail_hatch,
    ):
        failure = HatchConfigurationError('missing creature pool')
        failed = SimpleNamespace(status=Incubation.Status.FAILED)
        hatch.side_effect = [failure, failure]
        fail_hatch.side_effect = [
            (failed, True),
            (failed, False),
        ]

        first = hatch_egg.run('incubation-id')
        second = hatch_egg.run('incubation-id')

        self.assertEqual(
            first,
            (
                'Egg incubation-id failed permanently '
                'and was refunded.'
            ),
        )
        self.assertEqual(
            second,
            (
                'Egg incubation-id failed permanently '
                'and was already resolved.'
            ),
        )
        self.assertEqual(
            fail_hatch.call_args_list,
            [
                call('incubation-id', failure),
                call('incubation-id', failure),
            ],
        )


class TrainingTaskTests(SimpleTestCase):
    def pending_session(self):
        return SimpleNamespace(
            pk='training-id',
            status=TrainingSession.Status.PENDING,
        )

    @patch('creature.tasks.TrainingService.complete_training')
    @patch('creature.tasks._find_training_session')
    def test_not_ready_training_retries_at_service_deadline(
        self,
        find_session,
        complete,
    ):
        session = self.pending_session()
        find_session.return_value = session
        now = timezone.now()
        error = TrainingNotReadyError(
            session.pk,
            now + timedelta(seconds=17),
            now,
        )
        complete.side_effect = error

        with (
            patch.object(
                complete_training,
                'retry',
                side_effect=Retry(),
            ) as retry,
            self.assertRaises(Retry),
        ):
            complete_training.run(session.pk)

        retry.assert_called_once_with(
            exc=error,
            countdown=17,
        )

    @patch('creature.tasks.TrainingService.fail_training')
    @patch('creature.tasks.TrainingService.complete_training')
    @patch('creature.tasks._find_training_session')
    def test_transient_training_error_retries_before_final_attempt(
        self,
        find_session,
        complete,
        fail_training,
    ):
        session = self.pending_session()
        find_session.return_value = session
        error = OperationalError('database unavailable')
        complete.side_effect = error

        with (
            patch.object(
                complete_training,
                'retry',
                side_effect=Retry(),
            ) as retry,
            self.assertRaises(Retry),
        ):
            complete_training.run(session.pk)

        retry.assert_called_once_with(exc=error, countdown=5)
        fail_training.assert_not_called()

    @patch('creature.tasks.TrainingService.fail_training')
    @patch('creature.tasks.TrainingService.complete_training')
    @patch('creature.tasks._find_training_session')
    def test_final_transient_training_error_fails_and_refunds(
        self,
        find_session,
        complete,
        fail_training,
    ):
        session = self.pending_session()
        failed = SimpleNamespace(
            pk=session.pk,
            status=TrainingSession.Status.FAILED,
        )
        find_session.return_value = session
        error = OperationalError('database unavailable')
        complete.side_effect = error
        fail_training.return_value = (failed, True)

        with (
            patch.object(complete_training, 'max_retries', 0),
            patch.object(complete_training, 'retry') as retry,
        ):
            result = complete_training.run(session.pk)

        self.assertEqual(
            result,
            (
                'Training training-id failed permanently '
                'and was refunded.'
            ),
        )
        retry.assert_not_called()
        fail_training.assert_called_once_with(session.pk, error)

    @patch('creature.tasks.TrainingService.fail_training')
    @patch('creature.tasks.TrainingService.complete_training')
    @patch('creature.tasks._find_training_session')
    def test_permanent_training_failure_is_compensated_once(
        self,
        find_session,
        complete,
        fail_training,
    ):
        session = self.pending_session()
        failed = SimpleNamespace(
            pk=session.pk,
            status=TrainingSession.Status.FAILED,
        )
        first_error = RuntimeError('invalid training')
        second_error = RuntimeError('duplicate delivery')
        find_session.return_value = session
        complete.side_effect = [first_error, second_error]
        fail_training.side_effect = [
            (failed, True),
            (failed, False),
        ]

        first = complete_training.run(session.pk)
        second = complete_training.run(session.pk)

        self.assertIn('failed permanently and was refunded', first)
        self.assertEqual(
            second,
            'Training training-id was already resolved as failed.',
        )
        self.assertEqual(
            fail_training.call_args_list,
            [
                call(session.pk, first_error),
                call(session.pk, second_error),
            ],
        )

    @patch('creature.tasks.TrainingService.complete_training')
    @patch('creature.tasks._find_training_session')
    def test_completed_training_skips_service_on_redelivery(
        self,
        find_session,
        complete,
    ):
        pending = self.pending_session()
        completed = SimpleNamespace(
            pk=pending.pk,
            status=TrainingSession.Status.COMPLETED,
            creature=SimpleNamespace(name='Trainee'),
            result_stat='attack',
            result_boost=3,
        )
        find_session.side_effect = [pending, completed]
        complete.return_value = completed

        first = complete_training.run(pending.pk)
        second = complete_training.run(pending.pk)

        self.assertEqual(first, 'Trainee trained: attack +3.')
        self.assertEqual(
            second,
            'Training training-id was already resolved as completed.',
        )
        complete.assert_called_once_with(pending.pk)


class RecoveryTaskTests(SimpleTestCase):
    @patch('creature.tasks.process_battle_turn.apply_async')
    @patch('creature.tasks.BattleService.recover_battles')
    @patch('creature.tasks.TrainingService.recover_scheduling')
    @patch('creature.tasks.IncubationService.recover_scheduling')
    def test_recovery_returns_counts_and_does_not_reschedule_redelivery(
        self,
        recover_incubations,
        recover_trainings,
        recover_battles,
        apply_async,
    ):
        recover_incubations.side_effect = [2, 0]
        recover_trainings.side_effect = [1, 0]

        def recover_once(schedule_turn):
            if recover_battles.call_count == 1:
                schedule_turn(13, 4)
                return 3
            return 0

        recover_battles.side_effect = recover_once

        first = recover_creature_operations.run()
        second = recover_creature_operations.run()

        self.assertEqual(
            first,
            {
                'incubations': 2,
                'trainings': 1,
                'battles': 3,
            },
        )
        self.assertEqual(
            second,
            {
                'incubations': 0,
                'trainings': 0,
                'battles': 0,
            },
        )
        apply_async.assert_called_once_with(
            args=[13, 4],
            countdown=0,
        )
