"""Refused zero-event replay must not drop held physical text. Mock Win32 only."""
import unittest
from unittest.mock import Mock

from test_r03_replay_state import ReplayStateTests


class PasswordReplayRefusalTests(unittest.TestCase):
    def test_context_provider_exception_before_send_preserves_held_letters(self):
        driver = ReplayStateTests()
        hook, source = driver.fixture()
        driver.buffer(hook)
        hook.context = Mock(side_effect=RuntimeError('synthetic provider timeout'))
        driver.drain(hook, source)
        self.assertEqual(hook.native_queue.aborted_reason,
                         'unsafe-replay-guard-exception-before-send')
        hook.user32.SendInput.assert_not_called()
        self.assertIsNotNone(hook.native_pending_recovery)
        self.assertEqual([key.vk for key in hook.native_pending_recovery[1]],
                         [0x41, 0x41])
        self.assertFalse(hook.native_edit_disabled)
        self.assertFalse(hook.restore_native_pending())

    def test_exception_after_send_start_never_stores_replayable_backlog(self):
        driver = ReplayStateTests()
        hook, source = driver.fixture()
        driver.buffer(hook)
        def uncertain_send(events, valid, *, before_send=None):
            before_send()
            raise RuntimeError('synthetic uncertain SendInput')
        hook._send_guarded_inputs = uncertain_send
        driver.drain(hook, source)
        self.assertEqual(hook.native_queue.aborted_reason,
                         'unsafe-replay-send-exception')
        self.assertIsNone(hook.native_pending_recovery)
        self.assertTrue(hook.native_edit_disabled)

    def test_zero_event_password_guard_refusals_keep_explicit_recovery(self):
        driver = ReplayStateTests()
        for reason in (
            'password-field-unverified',
            'password-field-changed-before-send',
            'context-changed-after-password-check',
            'password-check-requires-worker',
        ):
            with self.subTest(reason=reason):
                hook, source = driver.fixture()
                driver.buffer(hook)
                hook._send_guarded_inputs = Mock(return_value=(0, reason))
                driver.drain(hook, source)
                hook.user32.SendInput.assert_not_called()
                self.assertEqual(hook.native_queue.aborted_reason,
                                 'unsafe-replay-' + reason)
                pending = hook.native_pending_recovery
                self.assertIsNotNone(pending)
                self.assertEqual([key.vk for key in pending[1]], [0x41, 0x41])
                self.assertFalse(hook.native_edit_disabled)
                # Recovery never starts on a now-unknown password/focus.
                hook.context = lambda: None
                self.assertFalse(hook.restore_native_pending())
                hook.user32.SendInput.assert_not_called()
                self.assertIs(hook.native_pending_recovery, pending)


if __name__ == '__main__':
    unittest.main()
