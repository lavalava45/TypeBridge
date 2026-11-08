"""P0: suppressed, unsubmitted keys survive overflow; no physical input."""
import unittest
from dataclasses import replace
from unittest.mock import Mock

from native_transaction_queue import NativeTransactionQueue
import test_offhook_native as fixtures
from test_app_controls import key


class OverflowRecoveryTests(unittest.TestCase):
    def fixture(self):
        hook = fixtures.OffHookNativeTests.fixture(self)
        hook.native_queue = NativeTransactionQueue(limit=1)
        context = hook.context()
        hook.native_queue.begin(target_scope=hook._native_scope(context))
        return hook, context

    def test_overflow_preserves_only_suppressed_prefix_without_auto_replay(self):
        hook, context = self.fixture()
        self.assertEqual(key(hook, 0x41), 1)
        self.assertEqual(key(hook, 0x42), 0)
        hook._drain_native_input(context, hook.native_queue, None, False)
        hook.user32.SendInput.assert_not_called()
        recovery = hook.native_pending_recovery
        self.assertIsNotNone(recovery)
        self.assertEqual([e.vk for e in recovery[1]], [0x41])
        self.assertFalse(hook.native_queue.busy)
        hook.emit.assert_any_call('native-recovery-available', 1)

    def test_explicit_recovery_refuses_another_field_without_losing_saved_keys(self):
        hook, context = self.fixture()
        key(hook, 0x41)
        key(hook, 0x42)
        hook._drain_native_input(context, hook.native_queue, None, False)
        pending = hook.native_pending_recovery
        hook.context = lambda: replace(context, focus=999)
        self.assertFalse(hook.restore_native_pending())
        self.assertIs(hook.native_pending_recovery, pending)
        hook.user32.SendInput.assert_not_called()

    def test_existing_recovery_is_not_overwritten(self):
        hook, context = self.fixture()
        previous = object()
        hook.native_pending_recovery = previous
        key(hook, 0x41)
        key(hook, 0x42)
        hook._drain_native_input(context, hook.native_queue, None, False)
        self.assertIs(hook.native_pending_recovery, previous)

    def test_pending_recovery_blocks_another_transaction_before_suppressing_input(self):
        hook, context = self.fixture()
        hook.native_queue.close()
        hook.native_pending_recovery = object()
        self.assertFalse(hook._schedule_native(context, Mock()))
        # The reason must be visible once, not only in developer diagnostics.
        self.assertFalse(hook._schedule_native(context, Mock()))
        notices = [call.args[1] for call in hook.emit.call_args_list
                   if call.args and call.args[0] == 'error'
                   and 'задержанные клавиши' in call.args[1]]
        self.assertEqual(len(notices), 1)
        self.assertFalse(hook.native_queue.busy)
        self.assertEqual(hook.native_queue.take(), [])

    def test_explicit_recovery_pairs_down_even_when_original_queue_was_full(self):
        hook, context = self.fixture()
        key(hook, 0x41)
        key(hook, 0x42)
        hook._drain_native_input(context, hook.native_queue, None, False)
        self.assertTrue(hook.restore_native_pending())
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertIsNone(hook.native_pending_recovery)
        hook.user32.SendInput.assert_called_once()
        count, packet, _ = hook.user32.SendInput.call_args.args
        self.assertEqual(count, 2)
        self.assertEqual([packet[i].u.ki.wVk for i in range(count)], [0x41, 0x41])
        self.assertTrue(packet[1].u.ki.dwFlags & 2)


if __name__ == '__main__':
    unittest.main()
