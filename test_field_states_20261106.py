"""Model-only field-state matrix; no UIA provider, GUI, hooks, or physical input."""

from dataclasses import replace
import time
import unittest
from unittest.mock import Mock, patch

from password_fields import PasswordFieldGuard
from engine import LayoutMapper
from r04_text_target import native_field_metadata
import test_browser_search_fields_20261106 as browser_fixtures
import test_p0_guard_transport_integration as guard_fixtures
from typebridge_app import ES_PASSWORD, Settings, staged_password_guard
from word_cycle import remember_word


TICKS = 621355968000000000


def sample(element, state='safe', *, top=10, focus=20, when=None):
    if when is None:
        when = time.time()
    return f'{top}|{focus}|{state}|{TICKS + int(when * 10_000_000)}|checked|{element}'


class FieldStateMatrixTests(unittest.TestCase):
    def fixture(self, enabled, process='editor.exe', cls='Edit', style=0):
        guard = staged_password_guard(replace(Settings(), experimental_password_guard=enabled))
        hook = guard_fixtures.P0GuardTransportIntegration().hook(guard)
        hook.process_name = lambda _pid: process
        hook.user32.GetClassNameW.side_effect = (
            lambda hwnd, output, _size: setattr(
                output, 'value', cls if hwnd == 20 else 'GenericWindow'))
        hook.user32.GetWindowLongPtrW.return_value = style
        return hook, guard

    def test_on_off_native_edit_and_richedit_explicit_password(self):
        for enabled in (False, True):
            for cls in ('Edit', 'RichEdit20W', 'RichEdit50W', 'RichEditD2DPT'):
                with self.subTest(enabled=enabled, cls=cls):
                    hook, guard = self.fixture(enabled, cls=cls)
                    self.assertEqual(guard.native_fast_path, not enabled)
                    self.assertIsNotNone(hook.context())
                    guard.ingest(sample('win32:20'))
                    self.assertIsNotNone(hook.context())
                    hook.user32.GetWindowLongPtrW.return_value = ES_PASSWORD
                    self.assertIsNone(hook.context())

    def test_on_off_native_unknown_and_explicit_password_metadata(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                hook, guard = self.fixture(enabled)
                for state in ('unknown', 'password'):
                    guard.ingest(sample('win32:20', state))
                    self.assertEqual(hook.context() is None, state == 'password')
                # Positive password evidence is honored with either old option.
                self.assertIsNone(hook.context())

    def test_on_off_chrome_wrapper_never_uses_native_shortcut(self):
        for enabled in (False, True):
            for cls in ('Chrome_WidgetWin_1', 'Chrome_RenderWidgetHostHWND', 'Edit'):
                with self.subTest(enabled=enabled, cls=cls):
                    hook, guard = self.fixture(enabled, process='chrome.exe', cls=cls)
                    self.assertFalse(native_field_metadata('chrome.exe', 'chrome.exe', cls))
                    # Until a fresh addressable UIA identity arrives, the
                    # browser hook must pass physical keys through untouched.
                    self.assertIsNone(hook.context())
                    for state in ('password', 'unknown'):
                        guard.ingest(sample('uia:4,8', state))
                        self.assertIsNone(hook.context())
                    guard.ingest(sample('uia:4,9'))
                    self.assertIsNotNone(hook.context())
                    guard.ingest(sample('win32:20'))
                    self.assertIsNone(hook.context())

    def test_chrome_edit_child_cannot_route_manual_cycle_to_native_hwnd(self):
        # Even class "Edit" is not sufficient proof of Win32 text delivery
        # when the host is Chrome. Exercise the real dispatch, not just policy.
        hook, guard = self.fixture(False, process='chrome.exe', cls='Edit')
        guard.ingest(sample('uia:4,9'))
        context = hook.context()
        self.assertIsNotNone(context)
        self.assertFalse(context.native_target)
        hook.engine.mapper = LayoutMapper()
        # Loading native HKLs may exceed the browser's 500 ms metadata TTL.
        # Refresh this synthetic safe lease before testing the route itself.
        guard.ingest(sample('uia:4,9'))
        context = hook.context()
        self.assertIsNotNone(context)
        hook.engine.active_languages = ('en', 'ru', 'he')
        hook.keys = []
        hook.token_blocked = False
        hook.last_word = remember_word(
            [(ord(c.upper()), False) for c in 'ghbdtn'], 'ghbdtn', 'en', '',
            hook._word_scope(context), whole=True)
        hook._send_guarded_inputs = Mock(side_effect=lambda events, *_args: (len(events), 'complete'))
        hook._postwrite_permitted = Mock(return_value=True)
        hook._record_replay_layout = Mock(return_value=0x0419)
        # Do not forge RU while the simulated foreground HKL is still EN:
        # browser selection admission correctly refuses that mismatch.
        hook._in_keyboard_callback = False
        with patch('typebridge_app.NativeEditBridge', side_effect=AssertionError('browser native path')), \
             patch('typebridge_app.read_selected_word', return_value=None), \
             patch('typebridge_app.read_caret_word', return_value=None) as reader:
            hook._cycle_word(context)
        reader.assert_called_once()
        hook._send_guarded_inputs.assert_not_called()

    def test_unknown_target_and_malformed_identity_refuse_browser_mutation(self):
        for enabled in (False, True):
            hook, guard = self.fixture(enabled, process='chrome.exe')
            for element in ('', 'uia:', 'uia:1,,2', 'win32:21', 'custom:1'):
                with self.subTest(enabled=enabled, element=element):
                    guard.ingest(sample(element))
                    self.assertIsNone(hook.context())
                    self.assertEqual(guard.snapshot.state, 'unknown')

    def test_seven_field_safe_but_unaddressable_refuses_browser_mutation(self):
        # Knowing IsPassword=False does not prove a safe editable DOM target.
        # A refused context passes physical input through without correction.
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                hook, guard = self.fixture(enabled, process='chrome.exe', cls='Chrome_WidgetWin_1')
                guard.ingest(sample('uia:4,7') + '|0')
                self.assertEqual(guard.snapshot.state, 'safe')
                self.assertFalse(guard.snapshot.addressable)
                self.assertIsNotNone(guard.permit_revision(10, 20))
                self.assertIsNone(hook.context())
                guard.ingest(sample('uia:4,7') + '|1')
                self.assertIsNotNone(guard.permit_revision(10, 20))
                self.assertIsNotNone(hook.context())

    def test_delayed_uia_response_cannot_reauthorize_after_focus_invalidated(self):
        clock = [10.0]
        guard = PasswordFieldGuard(require_element_id=True, clock=lambda: clock[0])
        with patch('password_fields.time.time', return_value=100.0):
            guard.ingest(sample('uia:3,1', when=99.9))
            self.assertIsNotNone(guard.permit_revision(10, 20))
            guard.invalidate()
            guard.ingest(sample('uia:3,1', when=99.95))
            self.assertIsNone(guard.permit_revision(10, 20))
            guard.ingest(sample('uia:3,1', when=100.01))
            self.assertIsNotNone(guard.permit_revision(10, 20))
            guard.ingest(sample('uia:3,1', when=98.0))
            # An obsolete answer is discarded; it cannot revoke a newer
            # safe answer either. The valid answer must expire by TTL.
            clock[0] += .51
            self.assertIsNone(guard.permit_revision(10, 20))

    def test_same_hwnd_new_browser_source_revision_is_new_target(self):
        hook, guard = self.fixture(True, process='chrome.exe', cls='Chrome_WidgetWin_1')
        guard.ingest(sample('uia:4,1'))
        source = hook.context()
        self.assertIsNotNone(source)
        guard.ingest(sample('uia:4,2'))
        current = hook.context()
        self.assertIsNotNone(current)
        self.assertEqual((source.top, source.focus), (current.top, current.focus))
        self.assertNotEqual(source.password_revision, current.password_revision)
        self.assertNotEqual(hook._native_scope(source), hook._native_scope(current))
        hook.user32.GetKeyState.return_value = 0
        hook.native_queue.begin = Mock(wraps=hook.native_queue.begin)
        job = Mock()
        self.assertTrue(hook._schedule_native(source, job))
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        job.assert_not_called()
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(hook.native_edit_disabled)
        self.assertTrue(hook._native_blocked(source))
        self.assertFalse(hook._native_blocked(current))

    def test_preflight_crash_local_and_post_job_crash_global(self):
        for crash_after_job in (False, True):
            with self.subTest(crash_after_job=crash_after_job):
                hook, source = browser_fixtures.BrowserSearchFieldTests().fixture()
                job = Mock(side_effect=RuntimeError('synthetic crash'))
                if not crash_after_job:
                    hook.context = Mock(side_effect=RuntimeError('synthetic provider crash'))
                self.assertTrue(hook._schedule_native(
                    source, job, trigger=hook._last_physical_event))
                hook.native_worker.join(2)
                self.assertFalse(hook.native_worker.is_alive())
                self.assertEqual(job.call_count, int(crash_after_job))
                hook.user32.SendInput.assert_not_called()
                self.assertEqual(hook.native_edit_disabled, crash_after_job)
                self.assertTrue(hook._native_blocked(source))
                other = replace(source, password_revision=source.password_revision + 1)
                self.assertEqual(hook._native_blocked(other), crash_after_job)
                self.assertEqual(hook.native_pending_recovery is None, crash_after_job)


if __name__ == '__main__':
    unittest.main()
