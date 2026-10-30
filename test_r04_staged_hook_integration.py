"""Real LiveHook constructor + context integration, no global hook or typing.

The explicit injected metadata provider is an offline staging experiment;
real user-facing GUI does NOT activate it. No User32 hook installation.
"""

import ctypes
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from password_fields import PasswordFieldGuard
from test_short_words_settings import HostedFocusTests
from test_app_controls import key
from typebridge_app import (ES_PASSWORD, LiveHook, Settings, WM_LBUTTONDOWN, VK_TAB)


class R04StagedIntegrationTests(unittest.TestCase):
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
