"""Early generic-editor SendInput: no duplication, balanced partial batches."""
import unittest
from unittest.mock import Mock
from dataclasses import replace

from engine import Decision
from test_app_controls import make_hook
from native_transaction_queue import PhysicalKey
from typebridge_app import WM_KEYDOWN, VK_SPACE, WM_RELEASE_KEYBOARD_JOB


class KeyboardEarlyTransportTests(unittest.TestCase):
    def fixture(self):
        hook = make_hook()
        hook.keys = [(0x47, False), (0x48, False), (0x42, False)]
        hook.native_early_disabled = False
        hook._finish_early_layout = Mock(return_value=True)
        decision = Decision('ghb', 'при', 'ru', 'unique dictionary match')
        return hook, decision

    def live_fixture(self):
        hook, decision = self.fixture()
        hook._in_keyboard_callback = True
        hook._last_physical_event = PhysicalKey(0x42, 0, 0, WM_KEYDOWN, True)
        hook.user32.PostThreadMessageW.return_value = 1
        hook.user32.GetKeyState.return_value = 0
        hook.native_pending_recovery = None
        hook._browser_field_revision = 0
        return hook, decision

    def release(self, hook):
        serial = hook.user32.PostThreadMessageW.call_args.args[2]
        hook._release_keyboard_job(serial)
        hook.native_worker.join(1)
        self.assertFalse(hook.native_worker.is_alive())

    def test_live_early_packet_waits_until_callback_returns_to_message_pump(self):
        hook, decision = self.live_fixture()
        self.assertTrue(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        hook.user32.SendInput.assert_not_called()
        hook._finish_early_layout.assert_not_called()
        self.assertEqual(hook.user32.PostThreadMessageW.call_args.args[1], WM_RELEASE_KEYBOARD_JOB)
        self.release(hook)
        hook.user32.SendInput.assert_called_once()
        hook._finish_early_layout.assert_called_once()

    def test_live_boundary_packet_drops_original_separator_exactly_once(self):
        from test_app_controls import key
        hook, _ = self.live_fixture()
        hook.keys = [(ord(c.upper()), False) for c in 'akuo']
        self.assertEqual(key(hook, VK_SPACE), 1)
        hook.user32.SendInput.assert_not_called()
        self.release(hook)
        hook.user32.SendInput.assert_called_once()
        events = hook.user32.SendInput.call_args.args[1]
        spaces = [e for e in events if e.u.ki.wVk == VK_SPACE and not e.u.ki.dwFlags & 2]
        self.assertEqual(len(spaces), 1)

    def test_changed_field_after_callback_never_starts_deletion(self):
        hook, decision = self.live_fixture()
        first = hook.context()
        hook._try_keyboard_early(first, decision, lambda: True)
        hook.context = lambda: replace(first, focus=first.focus + 10)
        self.release(hook)
        hook.user32.SendInput.assert_not_called()
        self.assertIn(hook.native_queue.aborted_reason,
                      ('focus-changed-before-commit', 'keyboard-context-changed'))

    def test_same_hwnd_mouse_change_after_callback_never_starts_deletion(self):
        hook, decision = self.live_fixture()
        hook._try_keyboard_early(hook.context(), decision, lambda: True)
        hook._browser_field_revision += 1
        self.release(hook)
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(hook.native_queue.aborted_reason, 'keyboard-context-changed')

    def test_stale_release_message_cannot_start_current_job(self):
        hook, decision = self.live_fixture()
        hook._try_keyboard_early(hook.context(), decision, lambda: True)
        serial = hook.user32.PostThreadMessageW.call_args.args[2]
        hook._release_keyboard_job(serial - 1)
        hook.user32.SendInput.assert_not_called()
        self.release(hook)
        hook.user32.SendInput.assert_called_once()

    def test_failed_post_does_not_consume_or_edit_original_key(self):
        hook, decision = self.live_fixture()
        hook.user32.PostThreadMessageW.return_value = 0
        self.assertFalse(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        hook.user32.SendInput.assert_not_called()
        self.assertIsNone(hook._keyboard_job_release)

    def test_reentrant_callback_cannot_clear_outer_callback_marker(self):
        hook, _ = self.live_fixture()
        for name in ('debug_keyboard_callbacks', 'debug_keydowns', 'debug_callback_errors',
                     'debug_keyboard_max_ms', 'debug_keyboard_slow'):
            setattr(hook, name, 0)
        def handle(code, message, param):
            if code == 0:
                hook._keyboard_probe_callback(1, message, param)
                self.assertTrue(hook._in_keyboard_callback)
            return 0
        hook.handle_keyboard = handle
        hook._keyboard_probe_callback(0, WM_KEYDOWN, 0)
        self.assertFalse(hook._in_keyboard_callback)
        self.assertEqual(hook._keyboard_callback_depth, 0)

    def test_changed_token_after_callback_cannot_append_replacement_to_old_text(self):
        hook, decision = self.live_fixture()
        hook._try_keyboard_early(hook.context(), decision, lambda: True)
        hook.clear_token()
        self.release(hook)
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(hook.native_queue.aborted_reason, 'keyboard-token-changed')

    def test_changed_settings_cancel_pending_packet(self):
        hook, decision = self.live_fixture()
        hook._try_keyboard_early(hook.context(), decision, lambda: True)
        hook.settings = replace(hook.settings, conservative=not hook.settings.conservative)
        self.release(hook)
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(hook.native_queue.aborted_reason, 'keyboard-context-changed')

    def test_zero_accepted_packet_replays_original_key_without_duplicate_prefix(self):
        hook, decision = self.live_fixture()
        calls = []
        def send(count, events, _size):
            calls.append([(e.u.ki.wVk, e.u.ki.dwFlags) for e in events])
            return 0 if len(calls) == 1 else count
        hook.user32.SendInput.side_effect = send
        self.assertTrue(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        self.release(hook)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], [(0x42, 0)])
        hook._finish_early_layout.assert_not_called()

    def test_post_write_exception_never_replays_original_printable_trigger(self):
        hook, decision = self.live_fixture()
        hook._finish_early_layout.side_effect = RuntimeError('after-write-fault')
        self.assertTrue(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        self.release(hook)
        # A full packet was accepted; a later exception MUST NOT replay B.
        hook.user32.SendInput.assert_called_once()
        self.assertTrue(hook.native_queue.trigger_irreversible)
        self.assertTrue(hook.native_edit_disabled)

    def test_post_write_fault_replays_independent_following_key_not_original(self):
        from test_app_controls import key
        from typebridge_app import WM_KEYUP
        from threading import Event
        hook, decision = self.live_fixture()
        entered, finish = Event(), Event()
        def late_failure(*args, **kwargs):
            entered.set()
            self.assertTrue(finish.wait(2))
            raise RuntimeError('after-write-fault')
        hook._finish_early_layout.side_effect = late_failure
        self.assertTrue(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        serial = hook.user32.PostThreadMessageW.call_args.args[2]
        hook._release_keyboard_job(serial)
        self.assertTrue(entered.wait(2))
        self.assertEqual(key(hook, 0x43), 1)
        self.assertEqual(key(hook, 0x43, WM_KEYUP), 1)
        finish.set()
        hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertEqual(hook.user32.SendInput.call_count, 2)
        count, events, _size = hook.user32.SendInput.call_args.args
        self.assertEqual(count, 2)
        self.assertEqual([events[i].u.ki.wVk for i in range(count)], [0x43, 0x43])
        self.assertNotIn(0x42, [events[i].u.ki.wVk for i in range(count)])

    def test_dom_field_changes_during_send_must_not_switch_layout_or_replay_b(self):
        from dataclasses import replace
        hook, decision = self.live_fixture()
        ctx = replace(hook.context(), process_name='chrome.exe')
        hook.context = lambda: ctx
        hook._browser_field_revision = 2
        def send(count, events, size):
            hook._browser_field_revision += 1
            return count
        hook.user32.SendInput.side_effect = send
        self.assertTrue(hook._try_keyboard_early(ctx, decision, lambda: True))
        self.release(hook)
        hook.user32.SendInput.assert_called_once()
        hook._finish_early_layout.assert_not_called()
        self.assertEqual(hook.native_queue.aborted_reason,
                         'field-revision-changed-after-submit')
        self.assertTrue(hook.native_early_disabled)

    def test_mutation_exception_after_mouse_clears_keys_does_not_index_last_key(self):
        hook, decision = self.live_fixture()
        hook.native_edit_disabled = False
        sent = []
        def send(count, events, size):
            sent.append(count)
            hook.clear_token()  # Simulate concurrent mouse callback on worker.
            return count
        hook.user32.SendInput.side_effect = send
        self.assertTrue(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        self.release(hook)
        self.assertEqual(len(sent), 1)
        self.assertFalse(hook.native_edit_disabled)

    def test_one_bounded_remainder_without_repeated_backspace(self):
        hook, decision = self.fixture()
        calls = []
        def send(count, events, _size):
            current = [(events[i].u.ki.wVk, events[i].u.ki.dwFlags)
                       for i in range(count)]
            calls.append(current)
            return 1 if len(calls) == 1 else count
        hook.user32.SendInput.side_effect = send
        self.assertTrue(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], (8, 0))
        self.assertEqual(calls[1][0], (8, 2))
        hook._finish_early_layout.assert_called_once()

    def test_incomplete_batch_only_releases_accepted_keydown(self):
        hook, decision = self.fixture()
        calls = []
        def send(count, events, _size):
            calls.append([(events[i].u.ki.wVk, events[i].u.ki.dwFlags)
                          for i in range(count)])
            return 1 if len(calls) in (1, 3) else 0
        hook.user32.SendInput.side_effect = send
        self.assertTrue(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][0], (8, 0))
        self.assertEqual(calls[2], [(8, 2)])
        self.assertTrue(hook.token_blocked)
        self.assertTrue(hook.native_early_disabled)
        self.assertIn(0x42, hook.suppressed_keyups)
        hook._finish_early_layout.assert_not_called()

    def test_changed_context_never_starts_early_deletion(self):
        hook, decision = self.fixture()
        self.assertFalse(hook._try_keyboard_early(hook.context(), decision, lambda: False))
        hook.user32.SendInput.assert_not_called()
        hook._finish_early_layout.assert_not_called()

    def test_zero_accepted_keeps_triggering_physical_letter(self):
        hook, decision = self.fixture()
        hook.user32.SendInput.return_value = 0
        hook.user32.SendInput.side_effect = None
        self.assertFalse(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        self.assertTrue(hook.token_blocked)
        self.assertNotIn(0x42, hook.suppressed_keyups)
        hook._finish_early_layout.assert_not_called()


if __name__ == '__main__':
    unittest.main()
