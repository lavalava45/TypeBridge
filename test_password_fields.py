"""Password metadata and hook gate checks; no hooks, field text or real input."""
import unittest
from unittest.mock import Mock, patch
import os
import queue

from password_fields import PasswordFieldGuard
from test_app_controls import make_hook, key
import test_short_words_settings as settings_tests
import test_word_cycle as cycle_tests


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.changed = Mock()
        self.guard = PasswordFieldGuard(self.changed, clock=lambda: self.now)

    def test_unknown_password_malformed_and_wrong_window_are_denied(self):
        self.assertIsNone(self.guard.permit_revision(10, 20))
        for line in ('10|20|password', '10|20|unknown', 'bad', '10|20|false'):
            self.guard.ingest(line)
            self.assertIsNone(self.guard.permit_revision(10, 20))
        self.guard.ingest('10|20|safe')
        self.assertIsNotNone(self.guard.permit_revision(10, 20))
        self.assertIsNone(self.guard.permit_revision(10, 21))
        self.assertIsNone(self.guard.permit_revision(11, 20))

    def test_expired_or_hung_provider_does_not_leave_permission_enabled(self):
        self.guard.ingest('10|20|safe')
        self.now += .6
        self.assertIsNone(self.guard.permit_revision(10, 20))

    def test_refresh_preserves_revision_but_focus_event_changes_it(self):
        self.guard.ingest('10|20|safe')
        before = self.guard.permit_revision(10, 20)
        self.guard.ingest('10|20|safe')
        self.assertEqual(self.guard.permit_revision(10, 20), before)
        self.changed.assert_called_once()
        self.guard.ingest('0|0|unknown')
        self.guard.ingest('10|20|safe')
        self.assertNotEqual(self.guard.permit_revision(10, 20), before)

    def test_click_requires_new_metadata_but_no_artificial_delay(self):
        self.guard.ingest('10|20|safe')
        self.guard.invalidate()
        self.assertIsNone(self.guard.permit_revision(10, 20))
        self.guard.ingest('10|20|safe')
        self.assertIsNotNone(self.guard.permit_revision(10, 20))

    def test_buffered_pre_click_answer_cannot_reenable_new_field(self):
        with patch('password_fields.time.time', return_value=100):
            self.guard.invalidate()
        ticks = 621355968000000000
        self.guard.ingest(f'10|20|safe|{ticks + 99 * 10_000_000}|checked')
        self.assertIsNone(self.guard.permit_revision(10, 20))
        self.guard.ingest(f'10|20|safe|{ticks + 101 * 10_000_000}|checked')
        self.assertIsNotNone(self.guard.permit_revision(10, 20))

    def test_persistent_unknown_is_notified_once_and_has_reason(self):
        self.guard.ingest('10|20|unknown')
        self.changed.reset_mock()
        self.now += 2.1
        self.guard.ingest('10|20|unknown')
        self.guard.ingest('10|20|unknown')
        self.changed.assert_called_once()

    def test_supervisor_restarts_until_closed(self):
        def first():
            self.guard._update(0, 0, 'unknown', 'helper-exited')
        def second():
            self.guard.stopped.set()
        with patch.object(self.guard, '_watch_once', side_effect=[first, second]) as once, \
                patch.object(self.guard.stopped, 'wait'):
            # Mock side effects must execute, rather than just return functions.
            once.side_effect = lambda: (first if once.call_count == 1 else second)()
            self.guard._watch()
        self.assertEqual(once.call_count, 2)

    def test_hung_helper_is_terminated_without_authorizing_input(self):
        replies = Mock()
        def timeout(**_kwargs):
            self.now += 11
            raise queue.Empty
        replies.get.side_effect = timeout
        process = Mock()
        process.poll.return_value = None
        with patch('password_fields.subprocess.Popen', return_value=process), \
                patch('password_fields.threading.Thread'), \
                patch('password_fields.queue.Queue', return_value=replies):
            self.guard._watch_once()
        self.assertEqual(self.guard.snapshot.reason, 'helper-timeout')
        self.assertIsNone(self.guard.permit_revision(10, 20))
        process.terminate.assert_called_once()


class HookGateTests(unittest.TestCase):
    def test_context_gate_covers_same_hwnd_browser_focus_changes(self):
        hook = settings_tests.HostedFocusTests().fixture()
        hook.password_guard = guard = PasswordFieldGuard()
        self.assertIsNone(hook.context())
        guard.ingest('10|20|safe')
        first = hook.context()
        self.assertIsNotNone(first)
        guard.ingest('0|0|unknown')
        self.assertIsNone(hook.context())
        guard.ingest('10|20|password')
        self.assertIsNone(hook.context())
        guard.ingest('10|20|safe')
        self.assertNotEqual(hook._word_scope(first), hook._word_scope(hook.context()))

    def test_protected_context_clears_state_and_bypasses_every_conversion_command(self):
        hook = make_hook()
        hook.password_guard = PasswordFieldGuard()
        hook.context = lambda: None
        hook.keys = [(65, False)]
        hook.last_word = object()
        hook._try_snippet = Mock()
        hook._try_early = Mock()
        hook._correct = Mock()
        hook._cycle_word = Mock()
        for vk in (65, 0x20, 0x13, 0x91):
            key(hook, vk)
        self.assertEqual(hook.keys, [])
        self.assertIsNone(hook.last_word)
        for action in (hook._try_snippet, hook._try_early, hook._correct, hook._cycle_word):
            action.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_skipped_prefix_is_not_corrected_as_if_its_tail_were_a_whole_word(self):
        driver = cycle_tests.WordCycleTests()
        driver.setUpClass()
        driver.setUp()
        driver.hook.password_guard = guard = PasswordFieldGuard()
        driver.hook.protection_scope = (90, 100)
        original = driver.hook.context
        driver.hook.context = lambda: original() if guard.permit_revision(90, 100) is not None else None
        driver.type('gh')
        guard.ingest('90|100|safe')
        key(driver.hook, 0x11)  # A modifier must not turn a skipped tail into a new word.
        driver.type('bdtn ')
        self.assertEqual(driver.text, 'ghbdtn ')
        driver.type('ghbdtn ')
        self.assertEqual(driver.text, 'ghbdtn привет ')

    def test_own_window_does_not_poison_following_word(self):
        hook = make_hook()
        hook.password_guard = PasswordFieldGuard()
        hook.protection_scope = None  # Own/excluded window, not a failed field check.
        hook.context = lambda: None
        key(hook, 65)
        self.assertFalse(getattr(hook, 'protection_skipped_input', False))

    def test_delimiter_during_unknown_does_not_poison_next_word(self):
        hook = make_hook()
        hook.protection_scope = (10, 20)
        hook.context = lambda: None
        key(hook, 65)
        self.assertTrue(hook.protection_skipped_input)
        key(hook, 0x20)
        self.assertFalse(hook.protection_skipped_input)

    def test_fresh_check_after_click_preserves_boundary_correction(self):
        driver = cycle_tests.WordCycleTests()
        driver.setUpClass()
        driver.setUp()
        hook = driver.hook
        hook.password_guard = guard = PasswordFieldGuard()
        hook.mouse_hook = None
        hook.handle_mouse(0, 0x201, 0)
        original = hook.context
        hook.context = lambda: original() if guard.permit_revision(90, 100) is not None else None
        guard.ingest('90|100|safe')
        driver.type('ghbdtn ')
        self.assertEqual(driver.text, 'привет ')

    def test_click_resets_quarantine_and_routine_notices_are_silent(self):
        from typebridge_app import WM_LBUTTONDOWN
        hook = make_hook()
        hook.password_guard = guard = PasswordFieldGuard()
        hook.mouse_hook = None
        hook.protection_skipped_input = True
        hook.handle_mouse(0, WM_LBUTTONDOWN, 0)
        self.assertFalse(hook.protection_skipped_input)
        hook.user32.GetForegroundWindow.return_value = 10
        def pid(_window, value):
            value._obj.value = os.getpid() + 1
            return 1
        hook.user32.GetWindowThreadProcessId.side_effect = pid
        hook._report_password_state()
        hook.emit.assert_not_called()
        guard.ingest('10|20|password')
        hook._report_password_state()
        hook._report_password_state()
        self.assertEqual(hook.emit.call_count, 1)

    def test_own_window_suppresses_password_notice(self):
        hook = make_hook()
        hook.password_guard = guard = PasswordFieldGuard()
        guard.ingest('10|20|password')
        hook.user32.GetForegroundWindow.return_value = 10
        def pid(_window, value):
            value._obj.value = os.getpid()
            return 1
        hook.user32.GetWindowThreadProcessId.side_effect = pid
        hook._report_password_state()
        hook.emit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
