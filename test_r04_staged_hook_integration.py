"""Real LiveHook constructor + context integration, no global hook or typing.

The metadata messages are simulated; real GUI startup supplies the guard.
These tests do not start a live provider or install a User32 hook.
"""

import ctypes
from dataclasses import replace
import time
import unittest
from unittest.mock import Mock, patch

from password_fields import PasswordFieldGuard
from test_short_words_settings import HostedFocusTests
from test_app_controls import key
from typebridge_app import (ES_PASSWORD, LiveHook, Settings, WM_LBUTTONDOWN, VK_TAB)


class R04StagedIntegrationTests(unittest.TestCase):
    @staticmethod
    def metadata_line(element_id, *, state='safe'):
        # A simulated helper response. No UIA provider or focused window read.
        ticks = 621355968000000000 + int(time.time() * 10_000_000)
        return f'10|20|{state}|{ticks}|checked|{element_id}'

    def real_constructor(self, guard=None):
        user = Mock()
        kernel = Mock()
        engine = Mock()
        engine.active_languages = ('ru', 'en', 'he')
        engine.language_catalog = None
        engine.mapper = Mock()
        with patch('typebridge_app.ctypes.WinDLL', side_effect=[user, kernel]), \
             patch.object(LiveHook, '_declare_api'), \
             patch('typebridge_app.prepare_early'):
            hook = LiveHook(engine, Settings(), password_guard=guard)
        hook.pressed = lambda _vk: False
        return hook

    def test_constructor_never_starts_helper_and_defaults_to_legacy_behavior(self):
        # A real constructor, not LiveHook.__new__, tested without a hook.
        guard = Mock()
        staged = self.real_constructor(guard)
        self.assertIs(staged.password_guard, guard)
        guard.start.assert_not_called()
        guard.close.assert_not_called()
        self.assertIsNone(self.real_constructor().password_guard)

    def test_hook_lifecycle_closes_guard_after_unhook(self):
        calls = []
        guard = Mock()
        guard.start.side_effect = lambda: calls.append('start')
        guard.close.side_effect = lambda: calls.append('close')
        hook = self.real_constructor(guard)
        hook.callback_type = lambda callback: callback
        hook.kernel32.GetCurrentThreadId.return_value = 123
        hook.user32.SetWindowsHookExW.side_effect = [11, 22]
        hook.user32.GetMessageW.return_value = 0
        hook.user32.UnhookWindowsHookEx.side_effect = lambda value: calls.append('unhook')
        hook.run()  # MOCKED User32 only: no real global hook installed.
        self.assertEqual(calls, ['start', 'unhook', 'unhook', 'close'])
        self.assertEqual(hook.thread_id, 0)
        self.assertIsNone(hook.keyboard_hook)

    def test_failed_guard_start_always_unhooks(self):
        guard = Mock()
        guard.start.side_effect = RuntimeError('simulated provider failure')
        hook = self.real_constructor(guard)
        hook.callback_type = lambda callback: callback
        hook.kernel32.GetCurrentThreadId.return_value = 123
        hook.user32.SetWindowsHookExW.side_effect = [11, 22]
        with self.assertRaisesRegex(RuntimeError, 'simulated provider failure'):
            hook.run()
        self.assertEqual(hook.user32.UnhookWindowsHookEx.call_count, 2)
        guard.close.assert_called_once_with()

    def test_real_constructor_context_refuses_unknown_password_and_stale(self):
        clock = [10.0]
        guard = PasswordFieldGuard(clock=lambda: clock[0])
        hook = self.real_constructor(guard)
        fixture = HostedFocusTests().fixture()
        hook.user32 = fixture.user32
        hook.process_name = fixture.process_name
        self.assertIsNone(hook.context())
        guard.ingest('10|20|password')
        self.assertIsNone(hook.context())
        guard.ingest('10|20|safe')
        checked = hook.context()
        self.assertIsNotNone(checked)
        self.assertGreater(checked.password_revision, 0)
        clock[0] += .51
        self.assertIsNone(hook.context())
        guard.ingest('10|20|safe')
        self.assertIsNotNone(hook.context())

    def test_same_hwnd_new_uia_element_invalidates_stale_scope(self):
        guard = PasswordFieldGuard()
        hook = self.real_constructor(guard)
        fixture = HostedFocusTests().fixture()
        hook.user32 = fixture.user32
        hook.process_name = fixture.process_name
        guard.ingest(self.metadata_line('uia:11,1'))
        first = hook.context()
        self.assertIsNotNone(first)
        guard.ingest(self.metadata_line('uia:11,1'))
        self.assertEqual(hook.context().password_revision, first.password_revision)
        # A DOM script changes focus without a native HWND change or a UIA
        # focus event. The next polling response identifies another element.
        guard.ingest(self.metadata_line('uia:11,2'))
        second = hook.context()
        self.assertIsNotNone(second)
        self.assertEqual((first.top, first.focus), (second.top, second.focus))
        self.assertNotEqual(first.password_revision, second.password_revision)
        self.assertNotEqual(hook._native_scope(first), hook._native_scope(second))
        guard.ingest(self.metadata_line(''))
        self.assertIsNone(hook.context())  # Missing identity cannot authorize edits.

    def test_slow_or_future_helper_response_cannot_authorize_mutation(self):
        clock = [10.0]
        guard = PasswordFieldGuard(clock=lambda: clock[0])
        epoch_ticks = 621355968000000000
        with patch('password_fields.time.time', return_value=100.0):
            for checked_at in (98.0, 101.0):
                with self.subTest(checked_at=checked_at):
                    ticks = epoch_ticks + int(checked_at * 10_000_000)
                    guard.ingest(f'10|20|safe|{ticks}|checked|uia:11,1')
                    self.assertIsNone(guard.permit_revision(10, 20))
                    self.assertEqual(guard.snapshot.reason, 'stale-helper-response')
            ticks = epoch_ticks + int(99.9 * 10_000_000)
            guard.ingest(f'10|20|safe|{ticks}|checked|uia:11,1')
            self.assertIsNotNone(guard.permit_revision(10, 20))
            clock[0] += 0.41  # 0.10 query age + 0.41 elapsed > 0.50 TTL.
            self.assertIsNone(guard.permit_revision(10, 20))

    def test_same_hwnd_new_uia_element_discards_previous_word(self):
        guard = PasswordFieldGuard()
        hook = self.real_constructor(guard)
        fixture = HostedFocusTests().fixture()
        hook.user32 = fixture.user32
        hook.process_name = fixture.process_name
        guard.ingest(self.metadata_line('uia:11,1'))
        old = hook.context()
        self.assertIsNotNone(old)
        hook._tracked_password_revision = old.password_revision
        hook.last_focus, hook.last_language = old.focus, old.language
        hook.keys = [(0x47, False)]
        hook.engine.mapper.char.return_value = 'a'
        hook.user32.GetKeyState.return_value = 0
        hook._try_snippet = Mock(return_value=(False, False))
        hook._boundary = Mock(return_value=None)
        hook._try_early = Mock(return_value=False)
        guard.ingest(self.metadata_line('uia:11,2'))
        with patch('typebridge_app.candidate_token_key', return_value=True):
            key(hook, 0x41)
        self.assertEqual(hook.keys, [(0x41, False)])
        self.assertEqual(hook._tracked_password_revision, hook.context().password_revision)
        hook.user32.SendInput.assert_not_called()

    def test_mouse_click_invalidates_same_hwnd_metadata_and_token(self):
        guard = PasswordFieldGuard()
        hook = self.real_constructor(guard)
        fixture = HostedFocusTests().fixture()
        hook.user32 = fixture.user32
        hook.process_name = fixture.process_name
        guard.ingest('10|20|safe')
        self.assertIsNotNone(hook.context())
        hook.keys = [(65, False)]
        hook.last_focus = 20
        hook.handle_mouse(0, WM_LBUTTONDOWN, 0)
        self.assertIsNone(hook.context())
        self.assertEqual(hook.keys, [])
        self.assertEqual(hook.last_focus, 0)

    def test_tab_navigation_invalidates_same_hwnd_permission_and_passes_key(self):
        guard = PasswordFieldGuard()
        hook = self.real_constructor(guard)
        fixture = HostedFocusTests().fixture()
        hook.user32 = fixture.user32
        hook.process_name = fixture.process_name
        guard.ingest('10|20|safe')
        self.assertIsNotNone(hook.context())
        key(hook, VK_TAB)
        self.assertIsNone(hook.context())
        hook.user32.SendInput.assert_not_called()

    def test_protected_context_passes_keys_without_replacement(self):
        guard = PasswordFieldGuard()
        hook = self.real_constructor(guard)
        fixture = HostedFocusTests().fixture()
        hook.user32 = fixture.user32
        hook.process_name = fixture.process_name
        hook.keys = [(65, False)]
        guard.ingest('10|20|password')
        key(hook, 65)
        self.assertEqual(hook.keys, [])
        hook.user32.SendInput.assert_not_called()

    def test_revision_change_denies_guarded_send_before_any_input(self):
        guard = PasswordFieldGuard()
        hook = self.real_constructor(guard)
        fixture = HostedFocusTests().fixture()
        hook.user32 = fixture.user32
        hook.process_name = fixture.process_name
        guard.ingest('10|20|safe')
        old = hook.context()
        self.assertIsNotNone(old)
        guard.ingest('10|20|unknown')
        guard.ingest('10|20|safe')
        current = hook.context()
        self.assertIsNotNone(current)
        self.assertNotEqual(hook._native_scope(old), hook._native_scope(current))
        inputs = [Mock()]
        sent, reason = hook._send_guarded_inputs(
            inputs, lambda: hook.context() is not None and
            hook._native_scope(hook.context()) == hook._native_scope(old))
        self.assertEqual((sent, reason), (0, 'context-changed-before-send'))
        hook.user32.SendInput.assert_not_called()

    def test_protected_native_edit_and_richedit_styles_are_rejected(self):
        hook = self.real_constructor()
        fixture = HostedFocusTests().fixture()
        hook.user32 = fixture.user32
        hook.process_name = fixture.process_name
        for name in ('Edit', 'RichEdit20W', 'RichEdit50W', 'RichEditD2DPT'):
            with self.subTest(name=name):
                def class_name(hwnd, output, size):
                    output.value = 'ApplicationFrameWindow' if hwnd == 10 else name
                    return len(output.value)
                hook.user32.GetClassNameW.side_effect = class_name
                hook.user32.GetWindowLongPtrW.return_value = ES_PASSWORD
                self.assertIsNone(hook.context())
        hook.user32.GetWindowLongPtrW.return_value = 0
        self.assertIsNotNone(hook.context())


if __name__ == '__main__':
    unittest.main()
