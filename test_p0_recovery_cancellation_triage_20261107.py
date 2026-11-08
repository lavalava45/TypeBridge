"""Synthetic recovery concurrency checks; no foreground, hooks, or real SendInput."""
import unittest
from unittest.mock import patch

from native_transaction_queue import PhysicalKey
from test_app_controls import key
import test_r03_replay_state as replay_fixtures
from typebridge_app import VK_SHIFT, WM_KEYUP


class RecoveryCancellationTriage(unittest.TestCase):
    def test_modifier_release_after_atomic_admission_cancels_without_replay_and_retains_snapshot(self):
        driver = replay_fixtures.ReplayStateTests()
        hook, source = driver.fixture(modifiers=(VK_SHIFT,))
        driver.buffer(hook)
        key(hook, 0xA1, WM_KEYUP)
        driver.drain(hook, source)
        pending = hook.native_pending_recovery
        self.assertIsNotNone(pending)
        hook.pressed = lambda vk: vk == VK_SHIFT
        gate = hook.native_queue
        original_take = gate.take
        observed = []

        def cancel_on_worker_take(*, owner=None):
            # Concurrent physical Shift KEYUP between begin(recovery_events)
            # and the worker's first drain. Modifiers must pass through and
            # abort the field-scoped replay before any SendInput.
            if not observed:
                observed.append(gate.capture(PhysicalKey(0xA1, 54, 0, WM_KEYUP)))
            return original_take(owner=owner)

        with patch.object(gate, 'take', side_effect=cancel_on_worker_take):
            self.assertTrue(hook.restore_native_pending())  # Accepted async job.
            hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertEqual(observed, [False])
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(gate.busy)
        recovered = hook.native_pending_recovery
        self.assertIsNotNone(recovered)
        self.assertEqual(tuple(recovered[1]), tuple(pending[1]))
        self.assertEqual(recovered[6:8], pending[6:8])


if __name__ == '__main__':
    unittest.main()
