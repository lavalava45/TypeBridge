"""Configurable autocorrection pause chord; mocked hook, no real input or HKL."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from direct_hotkeys import (PAUSE_TOGGLE_DEFAULT, compile_bindings,
                            parse_chord, pause_toggle_binding)
from test_app_controls import key, make_hook
from typebridge_app import (Settings, TypeBridgeGui, VK_CONTROL, VK_MENU,
                            VK_SHIFT, VK_PAUSE, WM_KEYUP,
                            load_settings, save_settings)
from user_lexicon import UserLexicon


class TogglePauseSettingsTests(unittest.TestCase):
    def test_legacy_json_defaults_to_existing_chord(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text('{"word_key":"Pause"}', encoding="utf-8")
            settings = load_settings(path)
            self.assertEqual(settings.pause_toggle_key, PAUSE_TOGGLE_DEFAULT)
            self.assertEqual(pause_toggle_binding(settings),
                             parse_chord("Ctrl+Alt+Pause"))

    def test_disabled_custom_and_restored_settings_round_trip(self):
        for value in ("Отключено", "Ctrl+Alt+F10", "Alt+Shift+F9"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "settings.json"
                original = replace(Settings(), pause_toggle_key=value)
                save_settings(original, path)
                self.assertEqual(load_settings(path), original)
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["pause_toggle_key"], value)
                self.assertEqual(pause_toggle_binding(original), parse_chord(value))

    def test_invalid_toggle_chords_do_not_overwrite_existing_file(self):
        for value in ([], 3, "", "  ", "F7", "Shift+F9", "Ctrl+Alt", "Ctrl+Ctrl+F9",
                      "Win+F9", "Ctrl+Alt+Delete"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "settings.json"
                path.write_text('{"pause_toggle_key":"Ctrl+Alt+F10"}',
                                encoding="utf-8")
                baseline = path.read_bytes()
                with self.assertRaises(ValueError):
                    save_settings(replace(Settings(), pause_toggle_key=value), path)
                self.assertEqual(path.read_bytes(), baseline)

    def test_conflicts_with_direct_layout_and_clipboard_commands(self):
        for settings in (
            replace(Settings(), pause_toggle_key="Ctrl+Shift+F1"),
            replace(Settings(), pause_toggle_key="Ctrl+Alt+H",
                    clipboard_history=True),
            replace(Settings(), pause_toggle_key="Ctrl+Shift+V",
                    plain_paste=True),
            replace(Settings(), pause_toggle_key="Ctrl+Alt+G",
                    quick_search=True),
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                compile_bindings(settings)

    def test_reassignment_or_disable_releases_old_reserved_chord(self):
        old = replace(Settings(), direct_ru="Ctrl+Alt+Pause")
        with self.assertRaises(ValueError):
            compile_bindings(old)
        self.assertEqual(compile_bindings(replace(old, pause_toggle_key="Отключено"))[
            parse_chord("Ctrl+Alt+Pause")], "ru")
        self.assertEqual(compile_bindings(replace(old, pause_toggle_key="Ctrl+Alt+F10"))[
            parse_chord("Ctrl+Alt+Pause")], "ru")
        # The historical Ctrl+Alt+Shift+Pause alias stays reserved only
        # while the original default toggle is selected.
        with self.assertRaises(ValueError):
            compile_bindings(replace(Settings(), direct_ru="Ctrl+Alt+Shift+Pause"))

    def test_disabled_toggle_does_not_swallow_previous_shortcut(self):
        hook = make_hook()
        hook.settings = replace(hook.settings, pause_toggle_key="Отключено")
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_MENU)
        hook.toggle_pause = Mock()
        self.assertEqual(key(hook, VK_PAUSE), 0)
        self.assertEqual(key(hook, VK_PAUSE, WM_KEYUP), 0)
        hook.toggle_pause.assert_not_called()
        self.assertNotIn(VK_PAUSE, hook.suppressed_keyups)

    def test_disabling_toggle_releases_old_chord_for_live_layout_binding(self):
        hook = make_hook()
        hook.settings = replace(hook.settings, pause_toggle_key="Отключено",
                                direct_ru="Ctrl+Alt+Pause")
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_MENU)
        hook.toggle_pause = Mock()
        hook._switch_direct = Mock()
        self.assertEqual(key(hook, VK_PAUSE), 1)
        hook._switch_direct.assert_called_once()
        hook.toggle_pause.assert_not_called()

    def test_new_shortcut_and_live_reconfiguration(self):
        hook = make_hook()
        hook.settings = replace(hook.settings, pause_toggle_key="Ctrl+Alt+F10")
        hook.toggle_pause = Mock()
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_MENU)
        f10 = 0x79
        self.assertEqual(key(hook, VK_PAUSE), 0)
        hook.toggle_pause.assert_not_called()
        self.assertEqual(key(hook, f10), 1)
        self.assertEqual(key(hook, f10), 1)
        hook.toggle_pause.assert_called_once()
        self.assertEqual(key(hook, f10, WM_KEYUP), 1)
        self.assertEqual(key(hook, f10), 1)
        self.assertEqual(hook.toggle_pause.call_count, 2)
        self.assertEqual(key(hook, f10, WM_KEYUP), 1)

        # The actual hook processes a config reload by replacing self.settings.
        # The callback's binding cache must track that identity, not a startup
        # value or the now-obsolete combination.
        hook.settings = replace(hook.settings, pause_toggle_key="Ctrl+Alt+F9")
        f9 = 0x78
        self.assertEqual(key(hook, f10), 0)
        self.assertEqual(key(hook, f9), 1)
        self.assertEqual(hook.toggle_pause.call_count, 3)
        hook.settings = replace(hook.settings, pause_toggle_key="Отключено")
        self.assertEqual(key(hook, f9, WM_KEYUP), 1)
        self.assertEqual(key(hook, f9), 0)
        self.assertEqual(hook.toggle_pause.call_count, 3)

    def test_custom_shortcut_requires_exact_modifiers(self):
        hook = make_hook()
        hook.settings = replace(hook.settings, pause_toggle_key="Ctrl+Alt+F10")
        hook.toggle_pause = Mock()
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_MENU, VK_SHIFT)
        self.assertEqual(key(hook, 0x79), 0)
        hook.toggle_pause.assert_not_called()

    def test_original_ctrl_alt_shift_pause_alias_is_preserved(self):
        hook = make_hook()
        hook.toggle_pause = Mock()
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_MENU, VK_SHIFT)
        self.assertEqual(key(hook, VK_PAUSE), 1)
        hook.toggle_pause.assert_called_once()
        self.assertEqual(key(hook, VK_PAUSE, WM_KEYUP), 1)
        hook.settings = replace(hook.settings, pause_toggle_key="Ctrl+Alt+F10")
        self.assertEqual(key(hook, VK_PAUSE), 0)
        hook.toggle_pause.assert_called_once()

    def test_gui_exposes_editable_combo_and_current_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            try:
                with (patch("typebridge_app.USER_DIR", Path(folder)),
                      patch("typebridge_app.load_settings", return_value=Settings()),
                      patch("typebridge_app.load_user_lexicon",
                            return_value=UserLexicon()),
                      patch("typebridge_app.read_registration", return_value=None)):
                    gui = TypeBridgeGui(root)
                    self.assertEqual(gui.pause_toggle_key.get(), PAUSE_TOGGLE_DEFAULT)
                    gui.pause_toggle_key.set("Ctrl+Alt+F10")
                    self.assertEqual(gui.current_settings().pause_toggle_key, "Ctrl+Alt+F10")
                    gui.pause_toggle_key.set("Отключено")
                    self.assertIsNone(pause_toggle_binding(gui.current_settings()))
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
