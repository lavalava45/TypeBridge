"""Five reported P0/R18 failures: API models only, never real input or clipboard."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

from clipboard_selection import SelectionUnavailable, copy_transaction
from engine import Decision
from native_transaction_queue import PhysicalKey
import test_clipboard_selection as clipboard_fixtures
import test_r03_replay_state as replay_fixtures
import test_r04_default_text_target as r04_fixtures
from typebridge_app import (VK_SHIFT, VK_SPACE, WM_KEYDOWN, WM_KEYUP,
                            KEYEVENTF_KEYUP, LiveHook)


class FiveFindingsRegressionTests(unittest.TestCase):
    def browser_fixture(self):
        hook = r04_fixtures.R04DefaultTargetTests.hooked_context()
        r04_fixtures.metadata(hook.password_guard)
        source = hook.context()
        self.assertIsNotNone(source)
        hook.password_guard.revalidate = Mock(return_value=True)
        hook.engine.mapper.layouts = {'ru': 0x0419}
        hook._in_keyboard_callback = False
        hook._remember = Mock()
        hook._trace_word = Mock()
        hook._note_native_mutation = Mock()
        hook._trace_native = Mock()
        hook._native_word_excerpt = Mock(return_value='')
        return hook, source

    def test_r04_manual_sendinput_rechecks_field_before_hkl_and_memory(self):
        hook, source = self.browser_fixture()
        word = Mock(scope=hook._word_scope(source), text='ghbdtn',
                    suffix='', language='en')
        word.next_variant.return_value = ('ru', 'привет')
        hook.last_word = word
        hook.keys = []
        def send(count, *_):
            r04_fixtures.metadata(hook.password_guard, 'password', 'uia:1,101')
            return count
        hook.user32.SendInput.side_effect = send
        LiveHook._cycle_word(hook, source)
        hook.user32.SendInput.assert_called_once()
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertEqual(word.text, 'ghbdtn')
        self.assertTrue(hook.token_blocked)

    def test_r04_native_manual_cycle_rechecks_after_verified_write(self):
        hook, source = self.browser_fixture()
        word = Mock(scope=hook._word_scope(source), text='ghbdtn',
                    suffix='', language='en')
        hook.last_word = word
        outcome = SimpleNamespace(state='verified', reason='')
        bridge = MagicMock(write_started=True, failure='', trace=())
        bridge.__enter__.return_value = bridge
        bridge.__exit__.return_value = False
        def edit(*_):
            r04_fixtures.metadata(hook.password_guard, 'password', 'uia:1,101')
            return outcome
        with (patch('typebridge_app.NativeEditBridge', return_value=bridge),
              patch('typebridge_app.replace_prefix', side_effect=edit)):
            hook._cycle_edit_word(source, word, 'ru', 'привет', native=True)
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertEqual(word.text, 'ghbdtn')
        self.assertTrue(hook.token_blocked)

    def test_r04_early_postwrite_guard_refuses_layout(self):
        hook, source = self.browser_fixture()
        r04_fixtures.metadata(hook.password_guard, 'password', 'uia:1,101')
        decision = Decision('ghbdtn', 'привет', 'ru', 'unique dictionary match')
        self.assertTrue(hook._finish_early_layout(source, decision, 'native_early_disabled'))
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertTrue(hook.native_early_disabled)
        self.assertTrue(hook.token_blocked)

    def test_r03_native_capslock_partial_down_gets_keyup_and_no_hkl(self):
        from test_offhook_native import OffHookNativeTests
        hook = OffHookNativeTests().fixture()
        hook._in_keyboard_callback = False
        source = hook.context()
        hook.engine.mapper.layouts = {'ru': 0x0419}
        hook._note_native_mutation = Mock()
        hook._trace_native = Mock()
        hook._trace_word = Mock()
        hook._native_blocked = Mock(return_value=False)
        outcome = SimpleNamespace(state='verified', reason='')
        bridge = MagicMock(write_started=True, failure='', trace=())
        bridge.__enter__.return_value = bridge
        bridge.__exit__.return_value = False
        calls = []
        def send(count, batch, size):
            calls.append(tuple(int(x.u.ki.dwFlags) for x in batch))
            return [1, 0, 1][len(calls)-1]
        hook.user32.SendInput.side_effect = send
        with (patch('typebridge_app.NativeEditBridge', return_value=bridge),
              patch('typebridge_app.replace_prefix', return_value=outcome)):
            hook._correct_native(source, Decision('hello', 'привет', 'ru', 'unique dictionary match'),
                                 [(0x48, False)], VK_SPACE, '', True, ' ', False, True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0], (0, KEYEVENTF_KEYUP))
        self.assertTrue(all(flag & KEYEVENTF_KEYUP for flag in calls[-1]))
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertTrue(hook.native_edit_disabled)

    def test_r03_cancel_during_recovery_preparation_retains_original_snapshot(self):
        driver = replay_fixtures.ReplayStateTests()
        hook, source = driver.fixture(modifiers=(VK_SHIFT,))
        driver.buffer(hook)
        from test_app_controls import key
        key(hook, 0xA1, WM_KEYUP)
        driver.drain(hook, source)
        pending = hook.native_pending_recovery
        self.assertIsNotNone(pending)
        hook.pressed = lambda vk: vk == VK_SHIFT
        gate = hook.native_queue
        capture = gate.capture
        count = [0]
        def interrupt(item):
            count[0] += 1
            if count[0] == 2:
                capture(PhysicalKey(0xA1, 54, 0, WM_KEYUP))
            return capture(item)
        with patch.object(gate, 'capture', side_effect=interrupt):
            self.assertFalse(hook.restore_native_pending())
        self.assertIs(hook.native_pending_recovery, pending)
        hook.user32.SendInput.assert_not_called()

    def test_r03_callback_release_timeout_retains_following_a_not_trigger(self):
        from test_offhook_native import OffHookNativeTests
        hook = OffHookNativeTests().fixture()
        hook._in_keyboard_callback = False
        hook.user32.PostThreadMessageW.return_value = 1
        source = hook.context()
        trigger = PhysicalKey(0x42, 48, 0, WM_KEYDOWN, True)
        self.assertTrue(hook._schedule_keyboard(source, lambda: False, trigger=trigger))
        self.assertTrue(hook.native_queue.capture(PhysicalKey(0x41, 30, 0, WM_KEYDOWN)))
        self.assertTrue(hook.native_queue.capture(PhysicalKey(0x41, 30, 0, WM_KEYUP)))
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        pending = hook.native_pending_recovery
        self.assertIsNotNone(pending)
        self.assertTrue(pending[2])  # Ambiguous trigger must be skipped.
        self.assertEqual([e.vk for e in pending[1] if not e.trigger], [0x41, 0x41])
        hook.user32.SendInput.assert_not_called()

    def test_r18_delayed_snapshot_cannot_send_late_copy(self):
        n, state, valid, target, clock, sleep = clipboard_fixtures.TransactionTests().fixture()
        def delayed_snapshot():
            state['time'] = 30.0
            return ([(13, 'old')], 10)
        n.snapshot.side_effect = delayed_snapshot
        with self.assertRaisesRegex(SelectionUnavailable, 'истёк срок'):
            copy_transaction(n, target, valid, clock=clock, sleep=sleep,
                             deadline=clock() + 8)
        n.copy.assert_not_called()
        n.restore.assert_not_called()


if __name__ == '__main__':
    unittest.main()
