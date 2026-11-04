"""Replay-state regressions: mocked Win32 only, no hooks or real input."""
from dataclasses import replace
from threading import Event
import unittest
from unittest.mock import Mock, patch

from native_transaction_queue import PhysicalKey
from input_transport import send_with_remainder
import test_offhook_native as fixtures
import test_keyboard_early_transport as keyboard_fixtures
from test_app_controls import key
from typebridge_app import VK_SHIFT, VK_CONTROL, WM_KEYDOWN, WM_KEYUP, input_key


class ReplayStateTests(unittest.TestCase):
    def test_partial_exception_releases_only_proven_down_and_quarantines_worker(self):
        for mode in ('validation', 'remainder'):
            with self.subTest(mode=mode):
                hook, source = self.fixture()
                hook.native_queue.close()
                held, calls = set(), []
                def broken_context():
                    raise RuntimeError('validation-failed')
                def send(count, events, size):
                    calls.append([int(e.u.ki.dwFlags) for e in events])
                    if len(calls) == 1:
                        held.add(30)
                        if mode == 'validation':
                            hook.context = broken_context
                        return 1
                    if mode == 'remainder' and len(calls) == 2:
                        raise RuntimeError('remainder-failed')
                    self.assertTrue(all(e.u.ki.dwFlags & 2 for e in events))
                    held.discard(30)
                    return count
                hook.user32.SendInput.side_effect = send
                def job():
                    self.buffer(hook)
                    return False
                self.assertTrue(hook._schedule_native(source, job))
                hook.native_worker.join(2)
                self.assertFalse(hook.native_worker.is_alive())
                self.assertFalse(held)
                self.assertEqual(len(calls), 2 if mode == 'validation' else 3)
                self.assertTrue(all(flags & 2 for flags in calls[-1]))
                self.assertFalse(hook.native_queue.busy)
                self.assertTrue(hook.native_edit_disabled)
                self.assertIsNone(hook.native_pending_recovery)

    def test_cleanup_bypasses_failed_mutation_callback_and_preserves_original_error(self):
        for cleanup in ('success', 'exception', 'refused'):
            with self.subTest(cleanup=cleanup):
                hook, source = self.fixture()
                events = [input_key(0x41), input_key(0x41, 2)]
                callback = Mock(side_effect=[None, RuntimeError('mutation-failed')])
                result = RuntimeError('cleanup-failed') if cleanup == 'exception' else (1 if cleanup == 'success' else 0)
                hook.user32.SendInput.side_effect = [1, result]
                with self.assertRaisesRegex(RuntimeError, 'mutation-failed'):
                    hook._send_guarded_inputs(events, lambda: True, before_send=callback)
                self.assertEqual(callback.call_count, 2)
                self.assertEqual(hook.user32.SendInput.call_count, 2)
                count, releases, _ = hook.user32.SendInput.call_args.args
                self.assertEqual(count, 1)
                self.assertEqual(releases[0].u.ki.dwFlags, 2)
                if cleanup != 'success':
                    hook.emit.assert_called_with('error', 'SendInput: не подтверждено отпускание клавиш')

    def test_progress_reports_only_validated_acceptance_counts(self):
        progress = []
        send = Mock(side_effect=[1, RuntimeError('remainder-failed')])
        with self.assertRaisesRegex(RuntimeError, 'remainder-failed'):
            send_with_remainder([1, 2], send, lambda: True, on_progress=progress.append)
        self.assertEqual(progress, [1])
        progress.clear()
        with self.assertRaises(ValueError):
            send_with_remainder([1, 2], Mock(return_value=3), lambda: True, on_progress=progress.append)
        self.assertEqual(progress, [])

    def test_worker_construction_or_start_failure_releases_queue_and_allows_retry(self):
        hook, source = self.fixture()
        hook.native_queue.close()
        action = Mock(return_value=False)
        for failure in ('constructor', 'start'):
            with self.subTest(failure=failure):
                with patch('typebridge_app.threading.Thread') as thread:
                    if failure == 'constructor':
                        thread.side_effect = RuntimeError('construction failed')
                    else:
                        thread.return_value.start.side_effect = RuntimeError('start failed')
                    self.assertFalse(hook._schedule_native(source, action))
                self.assertFalse(hook.native_queue.busy)
                action.assert_not_called()
                hook.user32.SendInput.assert_not_called()
        self.assertTrue(hook._schedule_native(source, action))
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertFalse(hook.native_queue.busy)
        action.assert_called_once()

    def test_recovery_construction_or_start_failure_preserves_snapshot_for_retry(self):
        hook, source = self.fixture()
        self.buffer(hook)
        key(hook, 0xA1, WM_KEYUP)
        self.drain(hook, source)
        pending = hook.native_pending_recovery
        self.assertIsNotNone(pending)
        for failure in ('constructor', 'start'):
            with self.subTest(failure=failure):
                with patch('typebridge_app.threading.Thread') as thread:
                    if failure == 'constructor':
                        thread.side_effect = RuntimeError('construction failed')
                    else:
                        thread.return_value.start.side_effect = RuntimeError('start failed')
                    self.assertFalse(hook.restore_native_pending())
                self.assertIs(hook.native_pending_recovery, pending)
                self.assertFalse(hook.native_queue.busy)
                hook.user32.SendInput.assert_not_called()
        self.assertTrue(hook.restore_native_pending())
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertFalse(hook.native_queue.busy)
        self.assertIsNone(hook.native_pending_recovery)
        hook.user32.SendInput.assert_called_once()

    def test_drain_keeps_queue_reserved_until_old_finally_finishes(self):
        hook, source = self.fixture()
        gate = hook.native_queue
        old = gate.owner
        close_if_drained = gate.close_if_drained
        interleaving = []
        def close_then_new_request(**kwargs):
            closed = close_if_drained(**kwargs)
            if closed:
                interleaving.append(gate.begin(target_scope=('new',)))
                # The inactive barrier must let physical input pass instead
                # of swallowing it while its old worker finishes cleanup.
                interleaving.append(gate.capture(PhysicalKey(0x42, 48, 0, WM_KEYDOWN)))
                self.assertFalse(gate.capture(PhysicalKey(0xA1, 54, 0, WM_KEYUP)))
                self.assertEqual(gate.abort('focus-changed-during-cleanup'), 0)
            return closed
        gate.close_if_drained = close_then_new_request
        self.drain(hook, source)
        self.assertEqual(interleaving, [False, False])
        self.assertFalse(gate.busy)
        self.assertFalse(hook.native_edit_disabled)
        self.assertFalse(hook._native_blocked(source))
        hook.user32.SendInput.assert_not_called()
        self.assertTrue(gate.begin(target_scope=('new',)))
        letter = PhysicalKey(0x42, 48, 0, WM_KEYDOWN)
        gate.capture(letter)
        # A late old drain must not consume or close this new transaction.
        hook._drain_native_input(source, gate, None, False, owner=old)
        self.assertTrue(gate.busy)
        self.assertEqual(gate.take(), [letter])

    def test_recovery_caller_retains_ownership_through_its_own_cleanup(self):
        hook, source = self.fixture()
        gate = hook.native_queue
        owner = gate.owner
        hook._drain_native_input(source, gate, None, False, owner=owner, release=False)
        self.assertTrue(gate.busy)
        self.assertFalse(gate.active)
        self.assertFalse(gate.begin())
        gate.close(owner=owner)
        self.assertTrue(gate.begin())

    def fixture(self, *, modifiers=()):
        hook = fixtures.OffHookNativeTests().fixture()
        source = replace(hook.context(), hkl=0x0409)
        hook.user32.GetKeyboardLayout.return_value = source.hkl
        hook.context = lambda: source
        hook.native_queue.begin(target_scope=hook._native_scope(source),
                                expected_hkl=source.hkl, expected_language='en',
                                replay_modifiers=modifiers)
        return hook, source

    def buffer(self, hook, vk=0x41, scan=30):
        gate = hook.native_queue
        gate.capture(PhysicalKey(vk, scan, 0, WM_KEYDOWN))
        gate.capture(PhysicalKey(vk, scan, 0, WM_KEYUP))

    def drain(self, hook, source):
        hook._drain_native_input(source, hook.native_queue, None, False)

    def test_shift_release_preserves_letters_but_never_replays_a_modifier(self):
        hook, source = self.fixture(modifiers=(VK_SHIFT,))
        self.buffer(hook)
        self.assertEqual(key(hook, 0xA1, WM_KEYUP), 0)
        self.drain(hook, source)
        hook.user32.SendInput.assert_not_called()
        pending = hook.native_pending_recovery
        self.assertEqual([e.vk for e in pending[1]], [0x41, 0x41])
        self.assertEqual(pending[6:8], (0x0409, (VK_SHIFT,)))
        self.assertFalse(hook.restore_native_pending())  # Shift is now up.
        self.assertIs(hook.native_pending_recovery, pending)
        hook.user32.SendInput.assert_not_called()
        hook.pressed = lambda vk: vk == VK_SHIFT
        self.assertTrue(hook.restore_native_pending())
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertIsNone(hook.native_pending_recovery)
        count, events, _ = hook.user32.SendInput.call_args.args
        self.assertEqual(count, 2)
        self.assertEqual([e.u.ki.wScan for e in events], [30, 30])
        self.assertFalse(hook._native_blocked(source))

    def test_recovery_requires_original_caps_state_and_original_field(self):
        hook, source = self.fixture()
        self.buffer(hook)
        key(hook, 0xA1, WM_KEYUP)
        self.drain(hook, source)
        pending = hook.native_pending_recovery
        hook.user32.GetKeyState.return_value = 1
        self.assertFalse(hook.restore_native_pending())
        hook.user32.GetKeyState.return_value = 0
        hook.context = lambda: replace(source, focus=source.focus + 1)
        self.assertFalse(hook.restore_native_pending())
        hook.context = lambda: None
        self.assertFalse(hook.restore_native_pending())
        self.assertIs(hook.native_pending_recovery, pending)
        hook.user32.SendInput.assert_not_called()

    def test_same_language_different_hkl_refuses_replay_and_preserves_snapshot(self):
        hook, source = self.fixture()
        self.buffer(hook)
        hook.user32.GetKeyboardLayout.return_value = 0xF0010409
        self.drain(hook, source)
        self.assertEqual(hook.native_queue.aborted_reason, 'layout-changed')
        hook.user32.SendInput.assert_not_called()
        pending = hook.native_pending_recovery
        self.assertEqual(pending[6], source.hkl)
        self.assertFalse(hook.restore_native_pending())
        self.assertIs(hook.native_pending_recovery, pending)
        hook.user32.GetKeyboardLayout.return_value = source.hkl
        self.assertTrue(hook.restore_native_pending())
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        hook.user32.SendInput.assert_called_once()

    def test_different_language_with_same_field_refuses_replay(self):
        hook, source = self.fixture()
        self.buffer(hook)
        hook.context = lambda: replace(source, language='ru')
        hook.user32.GetKeyboardLayout.return_value = 0x0419
        self.drain(hook, source)
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(hook.native_pending_recovery[4], 'en')

    def test_explicit_confirmed_layout_change_allows_following_typing(self):
        hook, source = self.fixture()
        hook.native_queue.close()
        entered, release = Event(), Event()
        def job():
            entered.set()
            self.assertTrue(release.wait(2))
            hook.user32.GetKeyboardLayout.return_value = 0x0419
            hook.context = lambda: replace(source, language='ru', hkl=0x0419)
            self.assertEqual(hook._record_replay_layout(source, 0x0419), 0x0419)
            return False
        self.assertTrue(hook._schedule_native(source, job))
        self.assertTrue(entered.wait(2))
        self.buffer(hook)
        release.set()
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertFalse(hook.native_queue.aborted_reason)
        self.assertEqual(hook.native_queue.expected_hkl, 0x0419)
        hook.user32.SendInput.assert_called_once()

    def test_unconfirmed_own_layout_change_cannot_release_buffer(self):
        hook, source = self.fixture()
        self.buffer(hook)
        hook._record_replay_layout(source, 0x0419)  # OS still reports EN.
        self.drain(hook, source)
        self.assertEqual(hook.native_queue.aborted_reason, 'layout-unconfirmed')
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(hook.native_pending_recovery[4], 'ru')
        self.assertEqual(hook.native_pending_recovery[6], 0x0419)

    def test_layout_change_between_partial_calls_never_replays_accepted_prefix(self):
        hook, source = self.fixture()
        self.buffer(hook)
        calls = []
        def partial(count, events, size):
            calls.append([(e.u.ki.wScan, e.u.ki.dwFlags) for e in events])
            hook.user32.GetKeyboardLayout.return_value = 0xF0010409
            return 1
        hook.user32.SendInput.side_effect = partial
        self.drain(hook, source)
        self.assertEqual(len(calls), 2)  # One down accepted, then KEYUP cleanup only.
        self.assertTrue(calls[1][0][1] & 2)
        self.assertIsNone(hook.native_pending_recovery)
        self.assertTrue(hook.native_edit_disabled)

    def test_cancellation_after_complete_batch_preserves_only_unsent_tail(self):
        for reason in ('modifier', 'layout'):
            with self.subTest(reason=reason):
                hook, source = self.fixture()
                self.buffer(hook)
                def accept_first(count, events, size):
                    self.buffer(hook, 0x42, 48)
                    if reason == 'modifier':
                        key(hook, 0xA1, WM_KEYUP)
                    else:
                        hook.user32.GetKeyboardLayout.return_value = 0xF0010409
                    return count
                hook.user32.SendInput.side_effect = accept_first
                self.drain(hook, source)
                hook.user32.SendInput.assert_called_once()
                self.assertEqual([e.vk for e in hook.native_pending_recovery[1]], [0x42, 0x42])

    def test_generic_packet_remainder_checks_full_hkl_independently(self):
        hook, source = self.fixture()
        gate = hook.native_queue
        gate.replay_thread_id = source.thread_id
        events = [input_key(0x41), input_key(0x41, 2)]
        calls = []
        def accept_one(count, batch, size):
            calls.append([e.u.ki.dwFlags for e in batch])
            hook.user32.GetKeyboardLayout.return_value = 0xF0010409
            return 1
        hook.user32.SendInput.side_effect = accept_one
        sent, reason = hook._send_guarded_inputs(events, lambda: True)
        self.assertEqual((sent, reason), (1, 'partial'))
        self.assertEqual(calls, [[0, 2], [2]])
        self.assertEqual(gate.aborted_reason, 'layout-changed')

    def test_same_language_layout_change_before_callback_release_refuses_edit(self):
        driver = keyboard_fixtures.KeyboardEarlyTransportTests()
        hook, decision = driver.live_fixture()
        self.assertTrue(hook._try_keyboard_early(hook.context(), decision, lambda: True))
        self.buffer(hook)
        hook.user32.GetKeyboardLayout.return_value = 0xF001040D
        driver.release(hook)
        hook.user32.SendInput.assert_not_called()
        hook._finish_early_layout.assert_not_called()
        self.assertEqual(hook.native_queue.aborted_reason, 'layout-changed')
        self.assertIsNotNone(hook.native_pending_recovery)

    def test_ctrl_snapshot_is_never_restored_as_a_shortcut(self):
        hook, source = self.fixture(modifiers=(VK_CONTROL,))
        self.buffer(hook)
        key(hook, 0xA2, WM_KEYUP)
        self.drain(hook, source)
        hook.pressed = lambda vk: vk == VK_CONTROL
        self.assertFalse(hook.restore_native_pending())
        self.assertIsNotNone(hook.native_pending_recovery)
        hook.user32.SendInput.assert_not_called()


if __name__ == '__main__':
    unittest.main()
