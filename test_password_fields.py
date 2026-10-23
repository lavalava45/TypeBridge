"""Password metadata and hook gate checks; no hooks, field text or real input."""
import unittest
from unittest.mock import Mock

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

    def test_click_or_tab_requires_a_new_check_and_short_settle_interval(self):
        self.guard.ingest('10|20|safe')
        self.guard.invalidate()
        self.assertIsNone(self.guard.permit_revision(10, 20))
        self.guard.ingest('10|20|safe')
        self.assertIsNone(self.guard.permit_revision(10, 20))
        self.now += .16
        self.assertIsNotNone(self.guard.permit_revision(10, 20))


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
        original = driver.hook.context
        driver.hook.context = lambda: original() if guard.permit_revision(90, 100) is not None else None
        driver.type('gh')
        guard.ingest('90|100|safe')
        key(driver.hook, 0x11)  # A modifier must not turn a skipped tail into a new word.
        driver.type('bdtn ')
        self.assertEqual(driver.text, 'ghbdtn ')
        driver.type('ghbdtn ')
        self.assertEqual(driver.text, 'ghbdtn привет ')


if __name__ == '__main__':
    unittest.main()
