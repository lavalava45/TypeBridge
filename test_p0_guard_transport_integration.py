"""P0 cross-component checks with real LiveHook constructor, mocked Win32.

No global hooks, foreground input, user settings, or live UIA connections.
The simulated UIA messages are NOT evidence of real browser password safety.
"""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from engine import Decision
from password_fields import PasswordFieldGuard
import test_r04_staged_hook_integration as staged_tests
import test_short_words_settings as focus_tests
from typebridge_app import (Settings, TypeBridgeGui, load_settings, save_settings,
                            staged_password_guard, input_key, VK_SPACE)


def metadata(element_id, state='safe', top=10, focus=20):
    ticks = 621355968000000000 + int(time.time() * 10000000)
    return f'{top}|{focus}|{state}|{ticks}|checked|{element_id}'


class P0GuardTransportIntegration(unittest.TestCase):
    def hook(self, guard):
        hook = staged_tests.R04StagedIntegrationTests().real_constructor(guard)
        fixture = focus_tests.HostedFocusTests().fixture()
        hook.user32 = fixture.user32
        hook.process_name = fixture.process_name
        hook.user32.CallNextHookEx.return_value = 0
        hook.user32.SendInput.side_effect = lambda count, *_: count
        return hook

    def test_profile_opt_in_is_strict_off_by_default_and_reversible(self):
        self.assertIsNone(staged_password_guard(Settings()))
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            save_settings(Settings(), path)
            self.assertFalse(load_settings(path).experimental_password_guard)
            opted = replace(load_settings(path), experimental_password_guard=True)
            save_settings(opted, path)
            self.assertEqual(load_settings(path), opted)
            guard = staged_password_guard(load_settings(path))
            self.assertIsInstance(guard, PasswordFieldGuard)
            self.assertTrue(guard.require_element_id)
            self.assertIsNone(guard.worker)  # Not started by configuration.
            save_settings(Settings(), path)
            self.assertIsNone(staged_password_guard(load_settings(path)))
            path.write_text('{"experimental_password_guard":"true"}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'experimental_password_guard'):
                load_settings(path)

    def test_real_hidden_settings_panel_opt_in_roundtrip_without_running_hook(self):
        from user_lexicon import UserLexicon
        with TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            try:
                with patch('typebridge_app.USER_DIR', Path(directory)), \
                     patch('typebridge_app.load_settings', return_value=Settings()), \
                     patch('typebridge_app.load_user_lexicon', return_value=UserLexicon()), \
                     patch('typebridge_app.read_registration', return_value=None), \
                     patch('typebridge_app.DEBUG_ENABLED', False):
                    gui = TypeBridgeGui(root)
                    self.assertFalse(gui.experimental_password_guard.get())
                    self.assertFalse(gui.current_settings().experimental_password_guard)
                    gui.experimental_password_guard.set(True)
                    self.assertTrue(gui.current_settings().experimental_password_guard)
                    self.assertIsNone(gui.hook)
            finally:
                root.destroy()

    def test_strict_runtime_rejects_legacy_metadata_and_safe_same_hwnd_switch(self):
        guard = staged_password_guard(replace(Settings(), experimental_password_guard=True))
        hook = self.hook(guard)
        guard.ingest('10|20|safe')
        self.assertIsNone(hook.context())
        guard.ingest(metadata('uia:1,1'))
        first = hook.context()
        self.assertIsNotNone(first)
        guard.ingest(metadata('uia:1,2', state='password'))
        self.assertIsNone(hook.context())
        guard.ingest(metadata('uia:1,2', state='unknown'))
        self.assertIsNone(hook.context())
        guard.ingest(metadata('uia:1,3'))
        last = hook.context()
        self.assertIsNotNone(last)
        self.assertNotEqual(first.password_revision, last.password_revision)
        self.assertEqual((first.top, first.focus), (last.top, last.focus))

    def test_strict_runtime_rejects_empty_malformed_or_wrong_win32_identity(self):
        guard = staged_password_guard(replace(Settings(), experimental_password_guard=True))
        hook = self.hook(guard)
        for identity in ('uia:', 'uia:1,,2', 'uia:one', 'win32:21', 'other:1'):
            with self.subTest(identity=identity):
                guard.ingest(metadata(identity))
                self.assertIsNone(hook.context())
                self.assertEqual(guard.snapshot.state, 'unknown')
        guard.ingest(metadata('win32:20'))
        self.assertIsNotNone(hook.context())

    def test_running_hook_cannot_hot_swap_password_guard_on_config_reload(self):
        selected = replace(Settings(), experimental_password_guard=True)
        for initial in (None, PasswordFieldGuard(require_element_id=True)):
            with self.subTest(initially_enabled=initial is not None):
                hook = self.hook(initial)
                hook.request_config(selected if initial is None else Settings(), Mock())
                effective, _lexicon, _index = hook.pending_config
                self.assertEqual(effective.experimental_password_guard,
                                 initial is not None)
                self.assertIs(hook.password_guard, initial)
                self.assertIsNone(getattr(initial, 'worker', None))

    def test_opt_in_guard_disables_plaintext_diagnostic_word_trace(self):
        guard = PasswordFieldGuard(require_element_id=True)
        with patch('typebridge_app.DEBUG_WORDS_ENABLED', True):
            self.assertTrue(self.hook(None).debug_words)
            hook = self.hook(guard)
            self.assertFalse(hook.debug_words)
            self.assertFalse(hook._trace_word('typed-character', None, source='secret'))
            hook.user32.SendInput.assert_not_called()

    def test_no_conversion_or_injection_from_unknown_and_password_fields(self):
        from test_app_controls import key
        guard = staged_password_guard(replace(Settings(), experimental_password_guard=True))
        hook = self.hook(guard)
        hook._try_snippet = Mock()
        hook._try_early = Mock()
        hook._correct = Mock()
        hook._cycle_word = Mock()
        hook._switch_direct = Mock()
        for state in ('password', 'unknown'):
            guard.ingest(metadata('uia:6,7', state=state))
            for vk in (0x41, VK_SPACE, 0x13, 0x91, 0x77):
                with self.subTest(state=state, vk=vk):
                    hook.keys = [(0x42, False)]
                    self.assertEqual(key(hook, vk), 0)
                    self.assertEqual(hook.keys, [])
        for path in (hook._try_snippet, hook._try_early, hook._correct,
                     hook._cycle_word, hook._switch_direct):
            path.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_unknown_prefix_then_safe_same_hwnd_suffix_is_not_corrected(self):
        from test_app_controls import key
        guard = staged_password_guard(replace(Settings(), experimental_password_guard=True))
        hook = self.hook(guard)
        hook._correct = Mock(return_value=True)
        hook._try_snippet = Mock(return_value=(False, False))
        hook._boundary = Mock(return_value=None)
        hook._try_early = Mock(return_value=False)
        hook.engine.mapper.char.return_value = 'a'
        hook.user32.GetKeyState.return_value = 0
        guard.ingest(metadata('uia:1,1', state='unknown'))
        self.assertEqual(key(hook, 0x47), 0)  # Unknown prefix is delivered.
        self.assertTrue(hook._guard_skipped_token)
        guard.ingest(metadata('uia:1,1'))
        self.assertEqual(key(hook, 0x48), 0)  # Safe suffix still passed intact.
        self.assertEqual(hook.keys, [])
        hook._correct.assert_not_called()
        self.assertEqual(key(hook, VK_SPACE), 0)
        self.assertFalse(hook._guard_skipped_token)
        with patch('typebridge_app.candidate_token_key', return_value=True):
            self.assertEqual(key(hook, 0x41), 0)
        self.assertEqual(hook.keys, [(0x41, False)])
        hook.user32.SendInput.assert_not_called()

    def test_partial_send_does_not_repeat_accepted_prefix_after_password_transition(self):
        guard = staged_password_guard(replace(Settings(), experimental_password_guard=True))
        hook = self.hook(guard)
        guard.ingest(metadata('uia:1,1'))
        old = hook.context()
        self.assertIsNotNone(old)
        packet = [input_key(0x41), input_key(0x41, 0x2),
                  input_key(0x42), input_key(0x42, 0x2)]
        sends = []

        def send(count, events, size):
            sends.append([int(e.u.ki.wVk) for e in events])
            guard.ingest(metadata('uia:1,2', 'password'))
            return 2  # A down/up accepted; B must never be sent in password field.

        hook.user32.SendInput.side_effect = send
        def valid():
            current = hook.context()
            return current is not None and hook._word_scope(current) == hook._word_scope(old)
        sent, status = hook._send_guarded_inputs(packet, valid)
        self.assertEqual((sent, status), (2, 'partial'))
        self.assertEqual(sends, [[0x41, 0x41, 0x42, 0x42]])
        self.assertIsNone(hook.context())
        # SendInput's accepted-event count cannot prove the resulting document.

    def test_all_six_layout_directions_reject_stale_password_scope_before_send(self):
        import r16_six_direction_trial as trial
        self.assertEqual(len(trial.CASES), 6)
        for case in trial.CASES:
            with self.subTest(direction=f'{case.source}->{case.target}'):
                guard = staged_password_guard(
                    replace(Settings(), experimental_password_guard=True))
                hook = self.hook(guard)
                guard.ingest(metadata('uia:1,1'))
                old = hook.context()
                self.assertIsNotNone(old)
                guard.ingest(metadata('uia:1,2', state='unknown'))
                events = [input_key(0x41), input_key(0x41, 0x2)]

                def still_valid():
                    current = hook.context()
                    return current is not None and hook._word_scope(current) == hook._word_scope(old)

                self.assertEqual(hook._send_guarded_inputs(events, still_valid),
                                 (0, 'context-changed-before-send'))
                hook.user32.SendInput.assert_not_called()

    def test_verified_native_write_cannot_change_hkl_after_focus_turns_unknown(self):
        guard = staged_password_guard(replace(Settings(), experimental_password_guard=True))
        hook = self.hook(guard)
        guard.ingest(metadata('uia:1,1'))
        old = hook.context()
        self.assertIsNotNone(old)
        hook._trace_native = Mock()
        hook._trace_word = Mock()
        bridge = Mock()
        bridge.__enter__ = Mock(return_value=bridge)
        bridge.__exit__ = Mock(return_value=False)
        bridge.write_started = True
        outcome = SimpleNamespace(state='verified', reason='verified')

        def complete_and_move(*_):
            guard.ingest(metadata('uia:1,2', 'unknown'))
            return outcome

        with patch('typebridge_app.NativeEditBridge', return_value=bridge), \
             patch('typebridge_app.replace_prefix', side_effect=complete_and_move):
            completed = hook._correct_native(old, Decision('ghbdtn', 'привет', 'ru',
                                                           'unique dictionary match'),
                                             [(0x47, False)], VK_SPACE, '', False,
                                             '', False, True)
        self.assertFalse(completed)
        hook.user32.SendInput.assert_not_called()
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertTrue(hook.token_blocked)


if __name__ == '__main__':
    unittest.main()
