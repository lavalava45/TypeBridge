"""Pause without a physical KEYUP, using real hook dispatch and synthetic Win32.

Does not install hooks, call the real SendInput, change foreground or HKL.
Ordinary key hotkeys must still reject typematic KEYDOWN until their KEYUP.
"""
import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from contextlib import nullcontext

from experiments.layout_quality_20261106 import FrozenMapper, KEYMAP
from test_app_controls import key
import test_hook_cycle_offhook as generic_tests
import test_r10_pause_scintilla_20261107 as scintilla_tests
from typebridge_app import (
    VK_PAUSE, VK_CONTROL, VK_MENU, WM_KEYUP,
)
from word_cycle import RecentWord


class PauseWithoutKeyupTests(unittest.TestCase):
    def _word_fixture(self, *, before_space):
        hook, _context = generic_tests.GenericCycleOffHookTests.fixture(self)
        mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
        mapper.layouts = {'ru': 0x0419, 'en': 0x0409, 'he': 0x040D}
        keys = mapper.keys_for('яблок', 'ru')
        self.assertIsNotNone(keys)
        self.assertEqual(mapper.word(keys, 'ru'), 'яблок')
        hook.engine.mapper = mapper
        hook.engine.active_languages = ('ru', 'en', 'he')
        hook.user32.GetKeyboardLayout.return_value = mapper.layouts['ru']
        hook.last_language = 'ru'
        current = hook.context()
        self.assertEqual(current.language, 'ru')
        if before_space:
            hook.keys = list(keys)
            hook.last_word = None
        else:
            hook.keys = []
            hook.last_word = RecentWord(keys, 'яблок', 'ru', ' ',
                                        hook._word_scope(current))
        return hook, mapper, keys

    def _three_presses(self, hook, mapper, keys, *, suffix):
        for count, language in enumerate(('en', 'he', 'ru'), 1):
            with self.subTest(press=count, language=language):
                self.assertEqual(key(hook, VK_PAUSE), 1)
                # Each press must reserve one worker, never write in callback.
                self.assertTrue(hook.native_queue.busy)
                self.assertEqual(hook.user32.SendInput.call_count, count - 1)
                generic_tests.GenericCycleOffHookTests.release(self, hook)
                self.assertEqual(hook.user32.SendInput.call_count, count)
                self.assertEqual(hook.user32.SendMessageTimeoutW.call_count, count)
                self.assertEqual(
                    (hook.last_word.text, hook.last_word.language, hook.last_word.suffix),
                    (mapper.word(keys, language), language, suffix))
        self.assertEqual(hook.user32.PostThreadMessageW.call_count, 3)
        self.assertFalse(hook.native_queue.busy)

    def test_three_pause_without_keyup_in_apple_word_before_space(self):
        hook, mapper, keys = self._word_fixture(before_space=True)
        self._three_presses(hook, mapper, keys, suffix='')

    def test_three_pause_without_keyup_in_apple_word_after_space(self):
        hook, mapper, keys = self._word_fixture(before_space=False)
        self._three_presses(hook, mapper, keys, suffix=' ')

    def test_three_pause_without_keyup_in_scintilla_selection(self):
        hook, transport = scintilla_tests.PauseSelectionTests.fixture(self)
        original = transport.data
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            for count in range(1, 4):
                self.assertEqual(key(hook, VK_PAUSE), 1)
                self.assertTrue(hook.native_queue.busy)
                self.assertEqual(transport.writes, count - 1)
                generic_tests.GenericCycleOffHookTests.release(self, hook)
                self.assertEqual(transport.writes, count)
        self.assertEqual(transport.data, original)
        self.assertEqual(hook.user32.PostThreadMessageW.call_count, 3)
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(hook.native_queue.busy)

    def test_normal_pause_keydown_keyup_still_cycles_and_consumes_keyup(self):
        hook, mapper, keys = self._word_fixture(before_space=False)
        for count, language in enumerate(('en', 'he', 'ru'), 1):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            generic_tests.GenericCycleOffHookTests.release(self, hook)
            self.assertEqual(key(hook, VK_PAUSE, WM_KEYUP), 1)
            self.assertNotIn(VK_PAUSE, hook.suppressed_keyups)
            self.assertEqual(hook.user32.SendInput.call_count, count)
            self.assertEqual(hook.last_word.language, language)

    def test_pause_while_worker_busy_cannot_schedule_second_worker(self):
        hook, _, _ = self._word_fixture(before_space=False)
        self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertTrue(hook.native_queue.busy)
        self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(hook.user32.PostThreadMessageW.call_count, 1)
        hook.user32.SendInput.assert_not_called()
        generic_tests.GenericCycleOffHookTests.release(self, hook)
        self.assertEqual(hook.user32.SendInput.call_count, 1)
        self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(hook.user32.PostThreadMessageW.call_count, 2)
        generic_tests.GenericCycleOffHookTests.release(self, hook)
        self.assertEqual(hook.user32.SendInput.call_count, 2)

    def test_nonpause_hotkey_requires_keyup_to_rearm(self):
        hook, _, _ = self._word_fixture(before_space=False)
        hook.settings = replace(hook.settings, word_key='F9')
        vk = 0x78  # F9
        self.assertEqual(key(hook, vk), 1)
        generic_tests.GenericCycleOffHookTests.release(self, hook)
        self.assertEqual(hook.user32.SendInput.call_count, 1)
        self.assertEqual(key(hook, vk), 1)
        self.assertEqual(hook.user32.SendInput.call_count, 1)
        self.assertFalse(hook.native_queue.busy)
        self.assertEqual(key(hook, vk, WM_KEYUP), 1)
        self.assertNotIn(vk, hook.suppressed_keyups)
        self.assertEqual(key(hook, vk), 1)
        self.assertTrue(hook.native_queue.busy)
        generic_tests.GenericCycleOffHookTests.release(self, hook)
        self.assertEqual(hook.user32.SendInput.call_count, 2)

    def test_ctrl_alt_pause_mode_toggle_still_rejects_typematic_until_keyup(self):
        hook, _ctx = generic_tests.GenericCycleOffHookTests.fixture(self)
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_MENU)
        hook.toggle_pause = Mock()
        self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(hook.toggle_pause.call_count, 1)
        self.assertEqual(key(hook, VK_PAUSE, WM_KEYUP), 1)
        self.assertNotIn(VK_PAUSE, hook.suppressed_keyups)
        self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(hook.toggle_pause.call_count, 2)


if __name__ == '__main__':
    unittest.main()
