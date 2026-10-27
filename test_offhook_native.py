"""Off-hook RichEdit transaction tests; no global hook or physical typing."""
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from native_transaction_queue import NativeTransactionQueue, PhysicalKey
from test_app_controls import make_hook, key
from typebridge_app import LiveHook, TypeBridgeGui, WM_KEYDOWN, WM_KEYUP, VK_SPACE, VK_PAUSE


class OffHookNativeTests(unittest.TestCase):
    def fixture(self):
        hook = make_hook()
        hook.native_queue = NativeTransactionQueue()
        hook.suppressed_keyups = set()
        hook.native_edit_disabled = False
        hook.native_early_disabled = False
        hook.native_pending_recovery = None
        hook.mouse_hook = None
        hook._in_keyboard_callback = True
        hook._last_physical_event = PhysicalKey(VK_SPACE, 57, 0, WM_KEYDOWN, True)
        return hook

    def test_hook_queues_without_waiting_and_replays_after_worker(self):
        hook = self.fixture()
        entered, release = Event(), Event()
        def delayed_editor_commit():
            entered.set()
            self.assertTrue(release.wait(3))
            return False  # Space should be replayed after the edit.

        ctx = hook.context()
        self.assertTrue(hook._schedule_native(ctx, delayed_editor_commit,
                                              trigger=hook._last_physical_event))
        self.assertTrue(entered.wait(2))
        self.assertTrue(hook.native_queue.active)
        self.assertEqual(key(hook, 0x41), 1)
        self.assertEqual(key(hook, 0x41, WM_KEYUP), 1)
        self.assertEqual(key(hook, VK_SPACE, WM_KEYUP), 1)
        hook.user32.SendInput.assert_not_called()  # Still in worker.
        release.set()
        hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertFalse(hook.native_queue.active)
        self.assertEqual(hook.user32.SendInput.call_count, 1)
        events = hook.user32.SendInput.call_args.args[1]
        self.assertEqual([events[i].u.ki.wScan for i in range(4)], [57, 0, 0, 0])
        self.assertTrue(hook.token_blocked)  # Buffered physical letters are not parsed.

    def test_dom_click_with_unchanged_browser_hwnd_aborts_buffer(self):
        from dataclasses import replace
        from typebridge_app import WM_LBUTTONDOWN
        hook = self.fixture()
        current = replace(hook.context(), process_name='chrome.exe')
        hook.context = lambda: current
        hook._browser_field_revision = 3
        entered, release = Event(), Event()
        def delayed():
            entered.set()
            self.assertTrue(release.wait(3))
            return False
        self.assertTrue(hook._schedule_native(current, delayed))
        self.assertTrue(entered.wait(2))
        self.assertEqual(key(hook, 0x43), 1)  # C is held for field A.
        hook.handle_mouse(0, WM_LBUTTONDOWN, 0)  # DOM focus A -> B, same HWND.
        self.assertEqual(hook._browser_field_revision, 4)
        self.assertEqual(hook.native_queue.aborted_reason, 'field-revision-changed')
        self.assertEqual(key(hook, 0x58), 0)  # X in B must pass through.
        release.set()
        hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        hook.user32.SendInput.assert_not_called()
        self.assertIsNone(hook.native_pending_recovery)
        self.assertFalse(hook.native_edit_disabled)  # Browser-only field failure.

    def test_dom_revision_alone_refuses_replay_before_sendinput(self):
        from dataclasses import replace
        hook = self.fixture()
        ctx = replace(hook.context(), process_name='chrome.exe')
        hook.context = lambda: ctx
        hook._browser_field_revision = 1
        hook.native_queue.begin(target_scope=hook._native_scope(ctx))
        hook.native_queue.capture(PhysicalKey(0x43, 46, 0, WM_KEYDOWN))
        hook._browser_field_revision = 2
        hook._drain_native_input(ctx, hook.native_queue, None, False)
        hook.user32.SendInput.assert_not_called()
        self.assertIsNone(hook.native_pending_recovery)
        self.assertTrue(hook._native_blocked(ctx))

    def test_browser_tab_during_active_barrier_aborts_before_swallowing_new_field(self):
        from dataclasses import replace
        from typebridge_app import VK_TAB
        hook = self.fixture()
        ctx = replace(hook.context(), process_name='chrome.exe')
        hook.context = lambda: ctx
        hook._browser_field_revision = 7
        hook.native_queue.begin(target_scope=hook._native_scope(ctx))
        self.assertEqual(key(hook, 0x43), 1)
        self.assertEqual(key(hook, VK_TAB), 0)
        self.assertEqual(hook.native_queue.aborted_reason, 'field-revision-changed')
        self.assertEqual(key(hook, 0x58), 0)
        hook._drain_native_input(ctx, hook.native_queue, None, False)
        hook.user32.SendInput.assert_not_called()

    def test_keyup_only_drain_keeps_early_token_tracking(self):
        hook = self.fixture()
        hook.keys = [(0x47, False), (0x48, False)]
        hook.early_done = True
        hook.token_blocked = False
        hook.native_queue.begin(target_scope=hook._native_scope(hook.context()))
        hook.native_queue.capture(PhysicalKey(0x48, 35, 0, WM_KEYUP))
        hook._drain_native_input(hook.context(), hook.native_queue, None, False)
        self.assertEqual(hook.keys, [(0x47, False), (0x48, False)])
        self.assertTrue(hook.early_done)
        self.assertFalse(hook.token_blocked)

    def test_ordinary_trigger_repeat_survives_verified_edit(self):
        hook = self.fixture()
        trigger = PhysicalKey(0x42, 48, 0, WM_KEYDOWN, True)
        entered, release = Event(), Event()
        def job():
            entered.set()
            self.assertTrue(release.wait(3))
            return True
        self.assertTrue(hook._schedule_native(hook.context(), job, trigger=trigger))
        self.assertTrue(entered.wait(2))
        self.assertEqual(key(hook, 0x42), 1)
        self.assertEqual(key(hook, 0x42), 1)
        self.assertEqual(key(hook, 0x42, WM_KEYUP), 1)
        release.set()
        hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        hook.user32.SendInput.assert_called_once()
        count, events, _size = hook.user32.SendInput.call_args.args
        self.assertEqual(count, 3)
        self.assertEqual([bool(events[i].u.ki.dwFlags & 2) for i in range(count)],
                         [False, False, True])
        self.assertNotIn(0x42, hook.suppressed_keyups)

    def test_verified_punctuation_skips_original_trigger_but_replays_following_key(self):
        hook = self.fixture()
        hook._last_physical_event = PhysicalKey(0xBE, 52, 0, WM_KEYDOWN, True)
        entered, release = Event(), Event()
        def edited_including_punctuation():
            entered.set()
            self.assertTrue(release.wait(3))
            return True
        self.assertTrue(hook._schedule_native(hook.context(), edited_including_punctuation,
                                              trigger=hook._last_physical_event))
        self.assertTrue(entered.wait(2))
        self.assertEqual(key(hook, 0xBE, WM_KEYUP), 1)
        self.assertEqual(key(hook, 0x41), 1)
        release.set()
        hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertEqual(hook.user32.SendInput.call_count, 1)
        events = hook.user32.SendInput.call_args.args[1]
        self.assertEqual(events[0].u.ki.wVk, 0x41)
        self.assertNotIn(0xBE, hook.suppressed_keyups)

    def test_focus_change_skips_editor_mutation_and_never_replays_to_new_field(self):
        hook = self.fixture()
        first = hook.context()
        second = type(first)(first.top, first.focus + 7, first.thread_id,
                             first.language, first.process_name, first.title,
                             first.layout_window)
        hook.context = lambda: second
        action = Mock()
        self.assertTrue(hook._schedule_native(first, action,
                                              trigger=hook._last_physical_event))
        hook.native_worker.join(3)
        action.assert_not_called()
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(hook.native_edit_disabled)
        self.assertTrue(hook._native_blocked(first))
        self.assertFalse(hook._native_blocked(second))

    def test_focus_switch_during_busy_barrier_never_swallows_new_field_keystrokes(self):
        from dataclasses import replace
        hook = self.fixture()
        old = hook.context()
        gate = hook.native_queue
        self.assertTrue(gate.begin(hook._last_physical_event,
                                   target_scope=hook._word_scope(old)))
        self.assertEqual(key(hook, 0x41), 1)  # Buffered in old field.
        hook.context = lambda: replace(old, focus=old.focus + 5)
        self.assertEqual(key(hook, 0x42), 0)  # New field: forwarded!
        self.assertEqual(key(hook, 0x42, WM_KEYUP), 0)
        self.assertFalse(gate.active)
        self.assertTrue(gate.busy)
        self.assertEqual(gate.aborted_reason, 'focus-changed')
        self.assertEqual([e.vk for e in gate.take()], [VK_SPACE, 0x41])
        # The already buffered old-field input cannot be safely replayed into
        # a different focused document. Explicit quarantine, never blind send.
        hook._drain_native_input(old, gate, hook._last_physical_event, False)
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(hook.native_edit_disabled)
        self.assertTrue(hook._native_blocked(old))
        self.assertFalse(hook._native_blocked(hook.context()))
        self.assertFalse(gate.busy)

    def test_old_field_held_typing_recovers_only_after_explicit_refocus(self):
        from dataclasses import replace
        hook = self.fixture()
        original = hook.context()
        gate = hook.native_queue
        gate.begin(hook._last_physical_event, target_scope=hook._word_scope(original))
        self.assertEqual(key(hook, 0x41), 1)
        self.assertEqual(key(hook, 0x41, WM_KEYUP), 1)
        other = replace(original, focus=original.focus + 8)
        hook.context = lambda: other
        self.assertEqual(key(hook, 0x42), 0)
        hook._drain_native_input(original, gate, hook._last_physical_event, False)
        self.assertIsNotNone(hook.native_pending_recovery)
        self.assertTrue(hook._native_blocked(original))
        self.assertFalse(hook._native_blocked(other))
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(hook.restore_native_pending())  # Wrong field, no edit.
        hook.user32.SendInput.assert_not_called()
        hook.context = lambda: original
        self.assertTrue(hook.restore_native_pending())
        hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertIsNone(hook.native_pending_recovery)
        self.assertFalse(hook._native_blocked(original))
        hook.user32.SendInput.assert_called()
        # The original Space and letter were both recovered, never the B
        # belonging to the second focused field.
        count, events, size = hook.user32.SendInput.call_args.args
        self.assertEqual([events[i].u.ki.wVk for i in range(count)],
                         [0, 0x41, 0x41, 0])
        self.assertEqual([events[i].u.ki.wScan for i in range(count)],
                         [57, 0, 0, 57])

    def test_focus_lost_at_send_preflight_preserves_entire_unsent_batch(self):
        from dataclasses import replace
        hook = self.fixture()
        source = hook.context()
        gate = hook.native_queue
        gate.begin(hook._last_physical_event, target_scope=hook._word_scope(source))
        gate.capture(PhysicalKey(0x41, 30, 0, WM_KEYDOWN))
        gate.capture(PhysicalKey(0x41, 30, 0, WM_KEYUP))
        hook.context = lambda: replace(source, focus=source.focus + 20)
        # No hook callback witnessed the change. Worker detects it just
        # before the first SendInput; that refusal must remain recoverable.
        hook._drain_native_input(source, gate, hook._last_physical_event, False)
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(hook.native_edit_disabled)
        self.assertTrue(hook._native_blocked(source))
        self.assertEqual(len(hook.native_pending_recovery[1]), 3)
        self.assertFalse(gate.busy)

    def test_accepted_replay_prefix_is_never_marked_as_recoverable(self):
        from dataclasses import replace
        hook = self.fixture()
        source = hook.context()
        gate = hook.native_queue
        gate.begin(hook._last_physical_event, target_scope=hook._word_scope(source))
        gate.capture(PhysicalKey(0x41, 30, 0, WM_KEYDOWN))
        hook.user32.SendInput.return_value = 1
        hook.user32.SendInput.side_effect = [1, 0, 1]
        hook._drain_native_input(source, gate, hook._last_physical_event, False)
        self.assertTrue(hook.native_edit_disabled)
        self.assertIsNone(hook.native_pending_recovery)

    def test_unsafe_shortcut_backlog_is_not_offered_for_recovery(self):
        hook = self.fixture()
        original = hook.context()
        gate = hook.native_queue
        gate.begin(target_scope=hook._word_scope(original))
        gate.capture(PhysicalKey(0x11, 29, 0, WM_KEYDOWN))  # Ctrl
        gate.abort('focus-changed')
        hook._drain_native_input(original, gate, None, False)
        self.assertIsNone(hook.native_pending_recovery)
        hook.user32.SendInput.assert_not_called()

    def test_recovery_expires_without_sending_into_stale_editor(self):
        from dataclasses import replace
        from typebridge_app import perf_counter
        hook = self.fixture()
        old = hook.context()
        hook.native_pending_recovery = (old, (PhysicalKey(0x41, 30, 0, WM_KEYDOWN),),
                                        False, None, old.language, perf_counter()-181)
        self.assertFalse(hook.restore_native_pending())
        self.assertIsNone(hook.native_pending_recovery)
        hook.user32.SendInput.assert_not_called()

    def test_recovery_rejects_wrong_language_and_retains_backlog(self):
        from dataclasses import replace
        from typebridge_app import perf_counter
        hook = self.fixture()
        old = hook.context()
        hook.native_pending_recovery = (old, (PhysicalKey(0x41, 30, 0, WM_KEYDOWN),),
                                        False, None, old.language, perf_counter())
        hook.context = lambda: replace(old, language='he' if old.language != 'he' else 'en')
        self.assertFalse(hook.restore_native_pending())
        self.assertIsNotNone(hook.native_pending_recovery)
        hook.user32.SendInput.assert_not_called()

    def test_pause_recovery_does_not_leave_suppressed_hotkey(self):
        from typebridge_app import perf_counter
        hook = self.fixture()
        old = hook.context()
        pause = PhysicalKey(VK_PAUSE, 0, 0, WM_KEYDOWN, True)
        hook.native_pending_recovery = (old, (pause,), True, pause,
                                        old.language, perf_counter())
        self.assertTrue(hook.restore_native_pending())
        hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertNotIn(VK_PAUSE, hook.suppressed_keyups)
        hook.user32.SendInput.assert_not_called()

    def test_global_uncertain_quarantine_not_reduced_to_one_field(self):
        from dataclasses import replace
        hook = self.fixture()
        original = hook.context()
        hook._quarantine_native('uncertain-write', original)
        self.assertTrue(hook._native_blocked(original))
        self.assertTrue(hook._native_blocked(replace(original, focus=original.focus+1)))

    def test_buffer_overflow_allows_next_physical_key_and_blocks_new_jobs(self):
        hook = self.fixture()
        old = hook.context()
        gate = hook.native_queue = NativeTransactionQueue(limit=1)
        self.assertTrue(gate.begin(target_scope=hook._word_scope(old)))
        self.assertEqual(key(hook, 0x41), 1)
        self.assertEqual(key(hook, 0x42), 0)  # Overflow key is NOT swallowed.
        self.assertEqual(key(hook, 0x43), 0)
        self.assertEqual(gate.aborted_reason, 'buffer-overflow')
        self.assertFalse(hook._schedule_native(old, lambda: True))
        hook._drain_native_input(old, gate, None, False)
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(hook.native_edit_disabled)
        self.assertTrue(hook._native_blocked(old))

    def test_external_injected_input_is_not_buffered_and_reinjected(self):
        from typebridge_app import LLKHF_INJECTED
        import ctypes
        from typebridge_app import KbdHook
        hook = self.fixture()
        gate = hook.native_queue
        old = hook.context()
        gate.begin(target_scope=hook._word_scope(old))
        injected = KbdHook(vkCode=0x41, scanCode=30, flags=LLKHF_INJECTED)
        self.assertEqual(hook.handle_keyboard(0, WM_KEYDOWN,
                                             ctypes.addressof(injected)), 0)
        self.assertEqual(gate.aborted_reason, 'external-injected-input')
        self.assertEqual(gate.take(), [])
        hook._drain_native_input(old, gate, None, False)
        hook.user32.SendInput.assert_not_called()

    def test_native_boundary_in_hook_schedules_job_instead_of_calling_editor(self):
        hook = self.fixture()
        hook.user32.GetClassNameW.side_effect = lambda hwnd, buf, size: setattr(buf, 'value', 'RichEditD2DPT')
        hook._schedule_native = Mock(return_value=True)
        hook._correct_native = Mock()
        self.assertTrue(hook._correct(hook.context(), VK_SPACE, ''))
        hook._schedule_native.assert_called_once()
        hook._correct_native.assert_not_called()

    def test_off_hook_pause_cycle_commits_then_discards_hotkey(self):
        from test_early_native_edit import TrialDeliveryTests
        hook, editor, language = TrialDeliveryTests().native_cycle_fixture()
        hook.native_queue = NativeTransactionQueue()
        hook._in_keyboard_callback = True
        hook._last_physical_event = PhysicalKey(VK_PAUSE, 0, 0, WM_KEYDOWN, True)
        hook.suppressed_keyups.add(VK_PAUSE)
        with patch('typebridge_app.NativeEditBridge', return_value=editor):
            hook._cycle_word(hook.context())
            hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertEqual(editor.data.decode(), 'ארקק ')
        self.assertEqual(language[0], 'he')
        hook.user32.SendInput.assert_not_called()

    def test_long_native_word_admitted_only_for_offhook_worker(self):
        from early_native_edit import (NativeEditBridge, NATIVE_MAX_INLINE_CHARS,
                                       NATIVE_MAX_WORKER_CHARS)
        self.assertGreater(NATIVE_MAX_WORKER_CHARS, NATIVE_MAX_INLINE_CHARS)
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.pending_char = ''
        bridge.last_snapshot = (b'original', len(b'original'))
        bridge.off_hook = False
        with self.assertRaisesRegex(ValueError, 'callback-admission-too-large'):
            bridge.prepare(0, 8, b'x' * (NATIVE_MAX_INLINE_CHARS + 1))
        bridge.off_hook = True
        bridge.prepare(0, 8, b'x' * (NATIVE_MAX_WORKER_CHARS - 1))
        self.assertEqual(len(bridge.prepared[2]), NATIVE_MAX_WORKER_CHARS - 1)

    def test_preflight_budget_extends_only_outside_keyboard_hook(self):
        from early_native_edit import NativeEditBridge
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.started = 0
        bridge.write_started = False
        bridge.budget_exceeded = False
        bridge.phase = 'preflight'
        with patch('early_native_edit.perf_counter', return_value=.095):
            bridge.off_hook = True
            bridge.check_deadline()
            bridge.off_hook = False
            with self.assertRaisesRegex(TimeoutError, 'native-edit-budget'):
                bridge.check_deadline()

    def test_gui_shutdown_postponed_until_native_job_finishes(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        job = SimpleNamespace(is_alive=Mock(return_value=True))
        gui.hook = SimpleNamespace(native_worker=job)
        gui.root = Mock()
        gui.status = Mock()
        gui.cancel_start = Event()
        gui.run_state = 'running'
        gui.worker = Mock()
        gui._sync_tray_state = Mock()
        gui.stop()
        gui.root.after.assert_called_once_with(50, gui.stop)
        self.assertIsNotNone(gui.hook)
        self.assertTrue(gui.cancel_start.is_set())

    def test_native_early_keyup_offloads_before_space_without_replay(self):
        from test_early_native_edit import TrialDeliveryTests
        from engine import Decision
        hook, editor, _, language = TrialDeliveryTests().fixture(
            source='en', typed='ghbdt', target='ru', result='приве')
        hook.native_queue = NativeTransactionQueue()
        hook._in_keyboard_callback = True
        hook.keys = [(0x41, False)] * 4
        hook.last_language, hook.last_focus = 'en', 100
        hook.user32.GetKeyState.return_value = 0
        hook.token_blocked = False
        hook.engine.mapper.char = lambda *_: 't'
        decision = Decision('ghbdt', 'приве', 'ru', 'unique dictionary match')
        editor.data, editor.caret = b'ghbd', 4
        with patch('typebridge_app.candidate_token_key', return_value=True), \
                patch('typebridge_app.suggest_live_early', return_value=decision), \
                patch('typebridge_app.NativeEditBridge', return_value=editor):
            self.assertEqual(key(hook, 0x54), 0)  # Real fifth KEYDOWN forwarded.
            editor.data, editor.caret = b'ghbdt', 5  # Actual editor delivery.
            self.assertEqual(key(hook, 0x54, WM_KEYUP), 0)
            hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertEqual(editor.data, 'приве'.encode())
        self.assertEqual(language[0], 'ru')
        self.assertEqual(editor.writes, 1)
        hook.user32.SendInput.assert_not_called()

    def test_offhook_exception_cannot_leave_keyboard_barrier_active(self):
        hook = self.fixture()
        hook._drain_native_input = Mock(side_effect=RuntimeError('simulated drain failure'))
        self.assertTrue(hook._schedule_native(hook.context(), lambda: True,
                                              trigger=hook._last_physical_event))
        hook.native_worker.join(3)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertFalse(hook.native_queue.active)
        self.assertTrue(hook.native_edit_disabled)

    def test_gui_close_waits_before_stopping_hooks(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.hook = SimpleNamespace(native_worker=SimpleNamespace(is_alive=lambda: True))
        gui.root = Mock()
        gui.status = Mock()
        gui.stop = Mock()
        gui.closing = False
        gui.close()
        gui.root.after.assert_called_once_with(50, gui.close)
        gui.stop.assert_not_called()
        self.assertFalse(gui.closing)



if __name__ == '__main__':
    unittest.main()
