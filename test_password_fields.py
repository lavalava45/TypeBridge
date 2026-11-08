"""Password metadata and staged hook checks; no live hooks or typed input.

The GUI supplies a field metadata guard. These offline fixtures also exercise
explicit guards, unknown/password/stale refusal and native exclusions.
"""
import unittest
from unittest.mock import Mock, patch
from password_fields import PasswordFieldGuard
from uia_password_native import native_style_state, editable_metadata
from test_app_controls import make_hook, key
import test_short_words_settings as settings_tests
import test_word_cycle as cycle_tests


class MetadataTests(unittest.TestCase):
    def test_native_edit_metadata_does_not_authorize_browser_or_password_styles(self):
        self.assertEqual([native_style_state(name, style) for name, style in (
            ('Edit', 0), ('Edit', 32), ('RichEditD2DPT', 0),
            ('RichEditD2DPT', 32), ('Chrome_RenderWidgetHostHWND', 0))],
            [0, 1, 0, 1, -1])

    def test_native_editability_requires_focused_writable_metadata(self):
        # Exact metadata policy tested without invoking a live UIA provider.
        self.assertTrue(editable_metadata(50003, True, True, True, False,
                                          False, -1, True))
        self.assertFalse(editable_metadata(50003, True, True, True, True,
                                           False, -1, True))
        self.assertFalse(editable_metadata(50030, True, True, False, True,
                                           True, 1, True))
        self.assertFalse(editable_metadata(50030, True, False, False, True,
                                           True, 0, True))

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
        service = Mock()
        def timeout(*_args, **_kwargs):
            self.guard.stopped.set()
            return None
        service.request.side_effect = timeout
        self.guard.ingest('10|20|safe')
        with patch('uia_native_worker.get_uia_service', return_value=service):
            self.guard._watch_once()
        self.assertEqual(self.guard.snapshot.reason, 'helper-unavailable')
        self.assertIsNone(self.guard.permit_revision(10, 20))
        service.request.assert_called_once()


class HookGateTests(unittest.TestCase):
    def test_explicit_metadata_guard_gates_context_and_tracks_revision(self):
        hook = settings_tests.HostedFocusTests().fixture()
        hook.password_guard = guard = PasswordFieldGuard()
        self.assertIsNone(hook.context())
        guard.ingest('10|20|safe')
        first = hook.context()
        self.assertIsNotNone(first)
        guard.ingest('10|20|password')
        self.assertIsNone(hook.context())
        guard.ingest('10|20|unknown')
        self.assertIsNone(hook.context())
        guard.ingest('10|20|safe')
        second = hook.context()
        self.assertIsNotNone(second)
        self.assertNotEqual(first.password_revision, second.password_revision)
        self.assertNotEqual(hook._word_scope(first), hook._word_scope(second))

    def test_without_guard_old_native_context_remains_supported(self):
        hook = settings_tests.HostedFocusTests().fixture()
        self.assertIsNotNone(hook.context())

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

    def test_unknown_context_does_not_retain_partial_token_through_delimiter(self):
        hook = make_hook()
        hook.context = lambda: None
        key(hook, 65)
        self.assertEqual(hook.keys, [])
        key(hook, 0x20)
        self.assertEqual(hook.keys, [])
        self.assertFalse(hook.token_blocked)

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

    def test_click_clears_token_and_advances_field_revision_without_password_popup(self):
        from typebridge_app import WM_LBUTTONDOWN
        hook = make_hook()
        hook.mouse_hook = None
        hook.keys = [(0x41, False)]
        hook._browser_field_revision = 3
        hook.handle_mouse(0, WM_LBUTTONDOWN, 0)
        self.assertEqual(hook.keys, [])
        self.assertEqual(hook._browser_field_revision, 4)
        hook.emit.assert_not_called()

    def test_own_window_does_not_emit_retired_password_notice(self):
        hook = make_hook()
        hook.context = lambda: None
        key(hook, 65)
        hook.emit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
