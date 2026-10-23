"""Only temporary lexicons, hidden Tk and mocked window APIs; no live input."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from direct_hotkeys import Chord, compile_bindings, parse_chord
from typebridge_app import (Settings, load_settings, save_settings, TypeBridgeGui,
                             VK_CONTROL, VK_SHIFT, VK_MENU, WM_KEYUP)
from user_lexicon import UserLexicon
from test_app_controls import make_hook, key


class ShortcutSettingsTests(unittest.TestCase):
    def test_roundtrip_and_legacy_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            expected = replace(Settings(), direct_ru="Alt+Shift+R", direct_he="Отключено")
            save_settings(expected, path)
            self.assertEqual(load_settings(path), expected)
            path.write_text('{}', encoding="utf-8")
            self.assertEqual(load_settings(path).direct_en, "Ctrl+Shift+F2")

    def test_bad_keys_types_and_unmodified_letters_rejected(self):
        for value in ([], "Win+R", "A", "Shift+A", "Ctrl+Ctrl+F1", "Ctrl++F1"):
            with self.assertRaises(ValueError):
                parse_chord(value)

    def test_conflicts_use_canonical_chords_and_reserved_actions(self):
        for settings in (
                replace(Settings(), direct_ru="Shift+Control+F2"),
                replace(Settings(), direct_ru="Pause"),
                replace(Settings(), direct_ru="Ctrl+Alt+Shift+Pause"),
                replace(Settings(), direct_ru="F8")):
            with self.assertRaises(ValueError):
                compile_bindings(settings)
        self.assertEqual(compile_bindings(replace(Settings(), direct_ru="F8", manual_f8=False))[
            Chord(0x77, frozenset())], "ru")


class ShortcutRuntimeTests(unittest.TestCase):
    def fixture(self, lang):
        hook = make_hook()
        hook.engine.mapper.layouts = {"ru": 0x04190419, "en": 0x04090409,
                                      "he": 0xFFFFFFFFF03D040D}
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_SHIFT)
        service = Mock()
        service.capture.return_value = SimpleNamespace(top=90, focus=100)
        return hook, service

    def test_all_three_targets_switch_without_typing_or_editing(self):
        for lang, vk in (("ru", 0x70), ("en", 0x71), ("he", 0x72)):
            hook, service = self.fixture(lang)
            with patch("typebridge_app.WindowsLayouts", return_value=service):
                self.assertEqual(key(hook, vk), 1)
            service.request.assert_called_once_with(service.capture.return_value,
                                                     hook.engine.mapper.layouts[lang])
            hook.user32.SendInput.assert_not_called()
            hook.engine.decide.assert_not_called()
            self.assertEqual(hook.last_language, lang)
            self.assertFalse(hook.keys)

    def test_repeat_is_suppressed_and_pause_does_not_disable_manual_command(self):
        hook, service = self.fixture("ru")
        hook.paused = True
        with patch("typebridge_app.WindowsLayouts", return_value=service):
            key(hook, 0x70); key(hook, 0x70)
            self.assertEqual(service.request.call_count, 1)
            self.assertEqual(key(hook, 0x70, WM_KEYUP), 1)
            key(hook, 0x70)
            self.assertEqual(service.request.call_count, 2)

    def test_extra_modifier_passes_through_and_disabled_shortcut_does_not_fire(self):
        hook, service = self.fixture("ru")
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_SHIFT, VK_MENU)
        with patch("typebridge_app.WindowsLayouts", return_value=service):
            self.assertEqual(key(hook, 0x70), 0)
        service.request.assert_not_called()
        hook.settings = replace(Settings(), direct_ru="Отключено")
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_SHIFT)
        with patch("typebridge_app.WindowsLayouts", return_value=service):
            self.assertEqual(key(hook, 0x70), 0)
        service.request.assert_not_called()

    def test_changed_focus_or_request_failure_cannot_edit_text(self):
        hook, service = self.fixture("ru")
        service.capture.return_value.focus = 101
        with patch("typebridge_app.WindowsLayouts", return_value=service):
            key(hook, 0x70)
        service.request.assert_not_called()
        hook.user32.SendInput.assert_not_called()
        hook, service = self.fixture("ru")
        service.request.side_effect = OSError("missing layout")
        with patch("typebridge_app.WindowsLayouts", return_value=service):
            key(hook, 0x70)
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(hook.emit.call_args.args[0], "error")


class DictionaryGuiTests(unittest.TestCase):
    def test_gui_adds_reloads_and_deletes_case_sensitive_words_and_exclusions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            root = tk.Tk(); root.withdraw()
            try:
                with (patch("typebridge_app.USER_DIR", path),
                      patch("typebridge_app.load_settings", return_value=Settings()),
                      patch("typebridge_app.load_user_lexicon", side_effect=lambda: UserLexicon.load(path)),
                      patch("typebridge_app.messagebox.showerror") as error):
                    gui = TypeBridgeGui(root)
                    root.update_idletasks()
                    gui.word_language.set("en")
                    for kind in ("words", "exclusions"):
                        gui.word_kind.set(kind); gui.word_sensitive.set(True)
                        gui.word_text.set("MiXeD"); gui.add_word()
                        loaded = UserLexicon.load(path)
                        self.assertTrue(loaded.contains(kind, "en", "MiXeD"))
                        self.assertFalse(loaded.contains(kind, "en", "mixed"))
                    gui.word_sensitive.set(False); gui.word_kind.set("words")
                    gui.word_text.set("Other"); gui.add_word()
                    self.assertTrue(UserLexicon.load(path).contains("words", "en", "OTHER"))
                    row = next(row for row, entry in gui.word_rows.items()
                               if entry == ("words", "en", "MiXeD", True))
                    gui.word_list.selection_set(row); gui.remove_word()
                    self.assertFalse(UserLexicon.load(path).contains("words", "en", "MiXeD"))
                    self.assertTrue(UserLexicon.load(path).contains("exclusions", "en", "MiXeD"))
                    gui.direct_keys["ru"].set("Alt+Shift+R")
                    self.assertEqual(gui.current_settings().direct_ru, "Alt+Shift+R")
                    error.assert_not_called()
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
