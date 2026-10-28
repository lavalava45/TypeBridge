"""Exclusions/routing tests: fake keyboard events and fake APIs only."""
import ctypes
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from app_exclusions import effective_exclusions, target_allowed, PASSWORD_PROCESSES
from clipboard_hotkeys import ClipboardHotkeys, KeyboardEvent
from clipboard_tools import clipboard_bindings
from typebridge_app import Settings, load_settings, save_settings
from test_app_controls import make_hook, key
import test_short_words_settings


class PresetTests(unittest.TestCase):
    def test_unchanged_exclusions_reuse_immutable_snapshot(self):
        from app_exclusions import _exclusions_for
        _exclusions_for.cache_clear()
        settings = Settings()
        original = effective_exclusions(settings)
        changed_ui = replace(settings, show_caret_flag=True)
        self.assertIs(original, effective_exclusions(changed_ui))
        self.assertEqual(_exclusions_for.cache_info().misses, 1)
        disabled = effective_exclusions(replace(settings, excluded_categories=()))
        self.assertNotIn('code.exe', disabled)
        self.assertIn('keepass.exe', disabled)
        custom = effective_exclusions(replace(settings, excluded_processes=('Custom.EXE',)))
        self.assertIn('custom.exe', custom)
        self.assertNotIn('custom.exe', original)

    def test_defaults_cover_passwords_ide_terminals_and_creative_software(self):
        names = effective_exclusions(Settings())
        for name in ('keepassxc.exe', 'enpass.exe', 'code.exe', 'idea64.exe',
                     'pycharm64.exe', 'rider64.exe', 'windowsterminal.exe',
                     'pwsh.exe', 'photoshop.exe', 'reaper.exe'):
            self.assertIn(name, names)
        self.assertNotIn('notepad++.exe', names)
        self.assertNotIn('notepad.exe', names)

    def test_optional_categories_can_be_disabled_but_passwords_and_custom_remain(self):
        settings = replace(Settings(), excluded_categories=(), excluded_processes=('custom.exe',))
        names = effective_exclusions(settings)
        self.assertNotIn('code.exe', names)
        self.assertNotIn('reaper.exe', names)
        self.assertIn('custom.exe', names)
        self.assertTrue(set(PASSWORD_PROCESSES) <= names)

    def test_old_settings_gain_defaults_and_optout_roundtrips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text('{"excluded_processes":["custom.exe"]}')
            self.assertIn('code.exe', effective_exclusions(load_settings(path)))
            settings = replace(load_settings(path), excluded_categories=())
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            for value in ('null', '"development"', '["unknown"]', '[[]]'):
                path.write_text('{"excluded_categories":' + value + '}')
                with self.assertRaises(ValueError):
                    load_settings(path)

    def test_host_or_child_and_unreadable_process_prevent_intercept(self):
        target = SimpleNamespace(pid=1, host_pid=2)
        for owner in (1, 2):
            self.assertFalse(target_allowed(target, Settings(),
                lambda pid: 'Code.EXE' if pid == owner else 'other.exe'))
        self.assertFalse(target_allowed(None, Settings()))
        self.assertFalse(target_allowed(target, Settings(), Mock(side_effect=OSError())))
        self.assertTrue(target_allowed(target, replace(Settings(), excluded_categories=()),
                                       lambda pid: 'code.exe'))

    def test_live_context_checks_both_processes(self):
        fixture = test_short_words_settings.HostedFocusTests()
        for excluded_pid in (100, 200):
            hook = fixture.fixture()
            hook.process_name = lambda pid: 'code.exe' if pid == excluded_pid else 'other.exe'
            self.assertIsNone(hook.context())


class MainHookPassThroughTests(unittest.TestCase):
    def test_excluded_context_passes_pause_cycle_dictionary_and_direct_commands(self):
        for vk, modifiers in ((0x13, (0x11, 0x12)), (0x13, ()), (0x91, ()),
                              (0x70, (0x11, 0x10)), (0x10, ())):
            hook = make_hook()
            hook.context = lambda: None
            hook.pressed = lambda key: key in modifiers
            hook.toggle_pause = Mock()
            hook._cycle_word = Mock()
            self.assertEqual(key(hook, vk), 0)
            hook.toggle_pause.assert_not_called()
            hook._cycle_word.assert_not_called()
            hook.user32.SendInput.assert_not_called()
            self.assertFalse(hook.suppressed_keyups)

    def test_double_shift_and_ctrl_break_pass_through(self):
        for vk, modifiers in ((0x10, ()), (0x13, (0x11,))):
            hook = make_hook()
            hook.pressed = lambda key: key in modifiers
            hook.toggle_pause = Mock()
            self.assertEqual(key(hook, vk), 0)
            self.assertEqual(key(hook, vk), 0)
            hook.toggle_pause.assert_not_called()
            hook.user32.SendInput.assert_not_called()

    def test_ctrl_key_clears_unfinished_word_before_clipboard_command(self):
        hook = make_hook()
        self.assertTrue(hook.keys)
        self.assertEqual(key(hook, 0x11), 0)
        self.assertFalse(hook.keys)


class ClipboardRoutingTests(unittest.TestCase):
    def fixture(self):
        router = ClipboardHotkeys.__new__(ClipboardHotkeys)
        router.api = Mock()
        router.api.CallNextHookEx.return_value = 0
        router.api.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if vk in (0x11, 0x10) else 0
        router.layouts = Mock()
        router.notify = Mock()
        router.settings = replace(Settings(), plain_paste=True)
        router.bindings = clipboard_bindings(router.settings)
        router.suppressed = set()
        router.hook = 123
        router.kernel = Mock()
        router.callback = None
        return router

    def send(self, router, vk=0x56, message=0x100, flags=0):
        event = KeyboardEvent(vk, 0, flags, 0, 0)
        return router.handle(0, message, ctypes.addressof(event))

    def test_excluded_application_keeps_original_chord_and_keyup(self):
        router = self.fixture()
        with patch('clipboard_hotkeys.target_allowed', return_value=False):
            self.assertEqual(self.send(router), 0)
            self.assertEqual(self.send(router, message=0x101), 0)
        router.notify.assert_not_called()
        self.assertFalse(router.suppressed)

    def test_allowed_chord_fires_once_and_consumes_release(self):
        router = self.fixture()
        with patch('clipboard_hotkeys.target_allowed', return_value=True):
            self.assertEqual(self.send(router), 1)
            self.assertEqual(self.send(router), 1)
            self.assertEqual(self.send(router, message=0x101), 1)
        router.notify.assert_called_once_with(('plain-paste', router.layouts.capture.return_value))

    def test_injected_events_and_modifier_keys_are_never_consumed(self):
        router = self.fixture()
        self.assertEqual(self.send(router, flags=0x10), 0)
        self.assertEqual(self.send(router, vk=0x10), 0)
        router.layouts.capture.assert_not_called()

    def test_disable_removes_mock_hook_without_registering_global_hotkey(self):
        router = self.fixture()
        router.configure(Settings())
        router.api.UnhookWindowsHookEx.assert_called_once_with(123)
        router.api.SetWindowsHookExW.assert_not_called()
        self.assertIsNone(router.hook)


class HiddenExclusionsGuiTests(unittest.TestCase):
    def test_categories_custom_names_and_cancelled_save_without_mapping(self):
        import tkinter as tk
        from exclusions_gui import ExclusionsWindow
        from app_exclusions import CATEGORIES
        root = tk.Tk()
        root.withdraw()
        app = SimpleNamespace(
            excluded=tk.StringVar(master=root, value='old.exe'),
            excluded_categories={name: tk.BooleanVar(master=root, value=True) for name in CATEGORIES},
            save=Mock(return_value=None))
        try:
            editor = ExclusionsWindow(root, app)
            editor.window.withdraw()
            root.update_idletasks()
            self.assertFalse(editor.window.winfo_ismapped())
            self.assertLessEqual(editor.window.winfo_reqheight(), 730)
            editor.categories['development'].set(False)
            editor.custom.delete('1.0', 'end')
            editor.custom.insert('1.0', 'custom.exe\nsecond.exe')
            editor.save()
            self.assertEqual(app.excluded.get(), 'old.exe')
            self.assertTrue(app.excluded_categories['development'].get())
            app.save.return_value = Settings()
            editor.save()
            self.assertEqual(app.excluded.get(), 'custom.exe,second.exe')
            self.assertFalse(app.excluded_categories['development'].get())
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
