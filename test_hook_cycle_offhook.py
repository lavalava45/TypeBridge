"""Generic manual word cycling must never block the low-level hook.

All tests use a mocked user32 API and a synthetic in-memory focus; no
SetWindowsHookEx, foreground change, or physical input is involved.
"""
import unittest
from dataclasses import replace
from unittest.mock import Mock

from native_transaction_queue import NativeTransactionQueue, PhysicalKey
from test_app_controls import make_hook, key
from typebridge_app import VK_PAUSE, WM_KEYDOWN, WM_KEYUP
from word_cycle import RecentWord


class GenericCycleOffHookTests(unittest.TestCase):
    def fixture(self):
        hook = make_hook()
        hook.native_queue = NativeTransactionQueue()
        hook.native_edit_disabled = False
        hook.keys = []
        hook.token_blocked = False
        hook._in_keyboard_callback = True
        hook.thread_id = 321
        hook._last_physical_event = PhysicalKey(VK_PAUSE, 0, 0, WM_KEYDOWN, True)
        hook.user32.PostThreadMessageW.return_value = 1
        hook.user32.GetClassNameW.side_effect = (
            lambda hwnd, buf, size: setattr(buf, 'value', 'Chrome_RenderWidgetHostHWND'))
        hook.engine.mapper.word = lambda keys, lang: {
            'ru': 'флгщ', 'en': 'akuo', 'he': 'שלום'}[lang]
        hook.engine.mapper.layouts = {'ru': 0x0419, 'en': 0x0409, 'he': 0x040D}
        ctx = hook.context()
        hook.last_word = RecentWord(
            ((65, False), (75, False), (85, False), (79, False)),
            'akuo', 'en', ' ', hook._word_scope(ctx))
        return hook, ctx

    def release(self, hook):
        serial = hook.user32.PostThreadMessageW.call_args.args[2]
        hook._release_keyboard_job(serial)
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertFalse(hook.native_queue.busy)

    def test_generic_cycle_waits_for_callback_release_before_any_win32_write(self):
        hook, ctx = self.fixture()
        # Go through the real manual-cycle hotkey branch (mocked Win32 only).
        self.assertEqual(key(hook, VK_PAUSE), 1)
        hook.user32.SendInput.assert_not_called()
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertTrue(hook.native_queue.busy)
        self.release(hook)
        hook.user32.SendInput.assert_called_once()
        hook.user32.SendMessageTimeoutW.assert_called_once()
        self.assertEqual((hook.last_word.text, hook.last_word.language),
                         ('שלום', 'he'))
        # The trigger KEYUP can arrive after the worker completed; it must
        # be swallowed once, not injected into the editor.
        self.assertEqual(key(hook, VK_PAUSE, WM_KEYUP), 1)
        self.assertNotIn(VK_PAUSE, hook.suppressed_keyups)

    def test_other_keys_wait_behind_generic_cycle_and_replay_after_commit(self):
        hook, ctx = self.fixture()
        hook._cycle_word(ctx)
        self.assertEqual(key(hook, 0x42), 1)
        self.assertEqual(key(hook, 0x42, WM_KEYUP), 1)
        hook.user32.SendInput.assert_not_called()
        self.release(hook)
        self.assertEqual(hook.user32.SendInput.call_count, 2)
        _, events, _ = hook.user32.SendInput.call_args.args
        self.assertEqual([events[i].u.ki.wVk for i in range(2)], [0x42, 0x42])
        self.assertFalse(hook.native_edit_disabled)

    def test_focus_changed_before_release_aborts_without_editing_other_field(self):
        hook, ctx = self.fixture()
        hook._cycle_word(ctx)
        hook.context = lambda: replace(ctx, focus=ctx.focus + 1)
        self.release(hook)
        hook.user32.SendInput.assert_not_called()
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertIn(hook.native_queue.aborted_reason,
                      ('focus-changed-before-commit', 'keyboard-context-changed'))

    def test_failed_schedule_never_falls_back_to_synchronous_hkl_request(self):
        hook, ctx = self.fixture()
        hook._schedule_keyboard = Mock(return_value=False)
        hook._cycle_word(ctx)
        hook._schedule_keyboard.assert_called_once()
        self.assertTrue(hook._schedule_keyboard.call_args.kwargs['drop_always'])
        hook.user32.SendInput.assert_not_called()
        hook.user32.SendMessageTimeoutW.assert_not_called()
        hook.emit.assert_any_call('diagnostic', 'manual-cycle-keyboard-worker-unavailable')

    def test_failed_release_post_neither_writes_nor_leaves_input_barrier(self):
        hook, ctx = self.fixture()
        hook.user32.PostThreadMessageW.return_value = 0
        hook._cycle_word(ctx)
        self.assertFalse(hook.native_queue.busy)
        self.assertIsNone(hook._keyboard_job_release)
        hook.user32.SendInput.assert_not_called()
        hook.user32.SendMessageTimeoutW.assert_not_called()

    def test_same_hwnd_browser_field_revision_change_refuses_manual_cycle(self):
        hook, ctx = self.fixture()
        hook._browser_field_revision = 1
        hook._cycle_word(ctx)
        hook._browser_field_revision += 1  # DOM focus moves with HWND unchanged.
        self.release(hook)
        hook.user32.SendInput.assert_not_called()
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertEqual(hook.native_queue.aborted_reason, 'keyboard-context-changed')

    def test_cycle_without_last_word_does_not_start_worker(self):
        hook, ctx = self.fixture()
        hook.last_word = None
        hook._schedule_keyboard = Mock()
        hook._cycle_word(ctx)
        hook._schedule_keyboard.assert_not_called()
        hook.user32.SendInput.assert_not_called()


if __name__ == '__main__':
    unittest.main()
