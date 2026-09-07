from unittest.mock import patch

from django.test import SimpleTestCase

from .tasks import recover_pending_withdrawals


class WithdrawalRecoveryTaskTests(SimpleTestCase):
    @patch('banking.tasks.WithdrawalService.recover_pending')
    def test_recovery_returns_idempotent_service_counts(
        self,
        recover_pending,
    ):
        recover_pending.side_effect = [2, 0]

        first = recover_pending_withdrawals.run()
        second = recover_pending_withdrawals.run()

        self.assertEqual(first, 2)
        self.assertEqual(second, 0)
        self.assertEqual(recover_pending.call_count, 2)
