"""Exercise the keyboard state machine against an in-memory text buffer only."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from engine import LanguageEngine, source_data_dir, LETTER_PUNCTUATION_KEYS
from typebridge_app import (FocusContext, Settings, load_settings, save_settings,
                             VK_SPACE, VK_PAUSE, VK_BACK, WM_KEYUP)
from test_app_controls import make_hook, key


class WordCycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = LanguageEngine(source_data_dir())

    def setUp(self):
        self.text = ""
        self.language = "en"
        self.focus = 100
        self.shift = False
        self.hook = make_hook()
        self.hook.mouse_hook = None
        self.hook.engine = self.engine
        self.hook.keys = []
        self.hook.token_blocked = False
        self.hook.last_word = None
        self.hook.user32.GetKeyState.return_value = 0
        self.hook.pressed = lambda vk: vk == 0x10 and self.shift
        self.hook.context = lambda: FocusContext(90, self.focus, 1, self.language,
                                                "editor.exe", "test", 100)
        self.hook.user32.GetKeyboardLayout.side_effect = lambda thread: self.engine.mapper.layouts[self.language]
        def change_layout(hwnd, message, flags, hkl, *rest):
            self.language = next(lang for lang, handle in self.engine.mapper.layouts.items() if handle == hkl)
            return 1
        self.hook.user32.SendMessageTimeoutW.side_effect = change_layout
        self.hook.user32.SendInput.side_effect = self.inject

    def inject(self, count, events, size):
        for event in events:
            value = event.u.ki
            if value.dwFlags & 2:
                continue
            if value.dwFlags & 4:
                self.text += chr(value.wScan)
            elif value.wVk == VK_BACK:
                self.text = self.text[:-1]
            elif value.wVk == VK_SPACE:
                self.text += " "
            else:
                self.fail("Unexpected synthetic key")
        return count

    def type(self, text):
        for char in text:
            if char == " ":
                vk, self.shift = VK_SPACE, False
            else:
                vk, self.shift = next((v, shift) for shift in (False, True) for v in
                    [*range(65, 91), *LETTER_PUNCTUATION_KEYS]
                    if self.engine.mapper.char(v, "en", shift) == char)
            before = self.language
            if key(self.hook, vk) != 1:
                self.text += " " if char == " " else self.engine.mapper.char(vk, before, self.shift)
            key(self.hook, vk, WM_KEYUP)
        self.shift = False

    def cycle(self, vk=VK_PAUSE):
        self.assertEqual(key(self.hook, vk), 1)
        key(self.hook, vk, WM_KEYUP)

    def test_cycle_after_automatic_replacement_returns_to_exact_original(self):
        self.type("akuo ")
        self.assertEqual(self.text, "שלום ")
        for expected in ("флгщ ", "akuo ", "שלום "):
            self.cycle()
            self.assertEqual(self.text, expected)

    def test_cycle_before_space_and_no_dictionary_match_required(self):
        self.type("xyzzy")
        self.cycle()
        self.cycle()
        self.cycle()
        self.assertEqual(self.text, "xyzzy")

    def test_physical_shift_state_survives_a_full_cycle(self):
        self.type("HeLLo ")
        for _ in range(3):
            self.cycle()
        self.assertEqual(self.text, "HeLLo ")

    def test_mouse_click_discards_last_word(self):
        self.type("xyzzy ")
        self.hook.handle_mouse(0, 0x0201, 0)
        before = self.text
        self.cycle()
        self.assertEqual(self.text, before)

    def test_keeps_previous_words_spaces_and_literal_punctuation(self):
        self.type("hello xyzzy.  ")
        for _ in range(3):
            self.cycle()
        self.assertEqual(self.text, "hello xyzzy.  ")

    def test_word_starting_with_punctuation_key_keeps_that_physical_key(self):
        for paused in (False, True):
            self.setUp()
            self.hook.paused = paused
            self.type(",tksq ")
            initial = self.text
            for _ in range(3):
                self.cycle()
            self.assertEqual(self.text, initial)
            if paused:
                self.cycle()
                self.cycle()
                self.assertEqual(self.text, "белый ")

    def test_key_repeat_only_cycles_once(self):
        self.type("xyzzy ")
        key(self.hook, VK_PAUSE)
        first = self.text
        key(self.hook, VK_PAUSE)
        self.assertEqual(self.text, first)

    def test_custom_key_and_paused_auto_correction(self):
        self.hook.settings = replace(self.hook.settings, cycle_key="F9")
        self.hook.paused = True
        self.type("akuo ")
        self.assertEqual(self.text, "akuo ")
        self.cycle(0x78)
        self.assertEqual(self.text, "שלום ")

    def test_focus_move_and_cursor_move_discard_last_word(self):
        for action in ("focus", "left", "backspace"):
            self.setUp()
            self.type("xyzzy ")
            if action == "focus":
                self.focus = 101
            else:
                key(self.hook, 0x25 if action == "left" else VK_BACK)
            before = self.text
            self.cycle()
            self.assertEqual(self.text, before)

    def test_next_word_replaces_history_and_enter_discards_it(self):
        self.type("hello xyzzy ")
        self.cycle()
        self.assertTrue(self.text.startswith("hello "))
        key(self.hook, 0x0D)
        before = self.text
        self.cycle()
        self.assertEqual(self.text, before)

    def test_extending_manually_changed_word_does_not_rewrite_only_tail(self):
        self.type("xyzzy")
        self.cycle()
        self.type("akuo ")
        self.hook.user32.SendInput.reset_mock()
        self.cycle()
        self.hook.user32.SendInput.assert_not_called()

    def test_partial_injection_disables_repeat_instead_of_replaying(self):
        self.type("xyzzy ")
        self.hook.user32.SendInput.side_effect = lambda *args: 1
        self.cycle()
        self.hook.user32.SendInput.reset_mock()
        self.cycle()
        self.hook.user32.SendInput.assert_not_called()

    def test_hotkey_settings_roundtrip_and_invalid_value_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = replace(Settings(), cycle_key="F9")
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            path.write_text('{"cycle_key": "A"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)


if __name__ == "__main__":
    unittest.main()
