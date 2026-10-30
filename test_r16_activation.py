"""R16/2c activation contracts: all tests run without global hooks or typing."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
from test_app_controls import make_hook, key
from typebridge_app import FocusContext

from direct_hotkeys import compile_bindings, parse_chord
from engine import (LanguageEngine, LayoutMapper, check_active_layouts_loaded,
                    select_loaded_layouts)
from typebridge_app import (LiveHook, Settings, TypeBridgeGui, load_settings,
                            save_settings)
from user_lexicon import UserLexicon


class ActivationTests(unittest.TestCase):
    def test_missing_disabled_layout_is_not_required(self):
        ru, en = 0x04190419, 0x04090409
        self.assertEqual(select_loaded_layouts([ru, en], {}, ('ru', 'en')),
                         {'ru': ru, 'en': en})
        with self.assertRaisesRegex(OSError, 'HE keyboard layout'):
            select_loaded_layouts([ru, en], {})
        with self.assertRaisesRegex(OSError, 'HE keyboard layout'):
            select_loaded_layouts([ru, en], {}, ('ru', 'he'))

    def test_layout_mapper_does_not_bind_disabled_layout(self):
        ru, en = 0x04190419, 0x04090409
        api = Mock()
        def get_list(count, target):
            if target is not None:
                target[0], target[1] = ru, en
            return 2
        api.GetKeyboardLayoutList.side_effect = get_list
        def unicode_map(vk, scan, state, output, size, flags, hkl):
            output.value = 'ф' if hkl & 0xFFFF == 0x0419 else 'a'
            return 1
        api.ToUnicodeEx.side_effect = unicode_map
        with patch('engine.ctypes.WinDLL', return_value=api), \
                patch('engine.configured_layout_entries', return_value={}):
            mapper = LayoutMapper(active_languages=('ru', 'en'))
        self.assertEqual(mapper.layouts, {'ru': ru, 'en': en})
        api.LoadKeyboardLayoutW.assert_not_called()

    def test_windows_preflight_only_enumerates_and_rejects_missing_layout(self):
        ru, en = 0x04190419, 0x04090409
        api = Mock()
        def get_list(count, target):
            if target is not None:
                target[0], target[1] = ru, en
            return 2
        api.GetKeyboardLayoutList.side_effect = get_list
        with patch('engine.ctypes.WinDLL', return_value=api):
            check_active_layouts_loaded(('ru', 'en'))
            with self.assertRaisesRegex(OSError, 'HE keyboard layout'):
                check_active_layouts_loaded(('ru', 'he'))
        api.LoadKeyboardLayoutW.assert_not_called()
        api.SendMessageTimeoutW.assert_not_called()

    def test_disabled_direct_bindings_do_not_consume_or_conflict(self):
        settings = replace(Settings(), active_languages=('ru', 'en'),
                           direct_ru='Ctrl+Shift+F1', direct_en='Ctrl+Shift+F2',
                           direct_he='Ctrl+Shift+F1')
        actual = compile_bindings(settings)
        self.assertEqual(set(actual.values()), {'ru', 'en'})
        self.assertEqual(actual[parse_chord('Ctrl+Shift+F1')], 'ru')
        with self.assertRaisesRegex(ValueError, 'двум языкам'):
            compile_bindings(replace(settings, active_languages=('ru', 'en', 'he')))

    def test_config_update_defers_active_set_until_new_hook(self):
        hook = LiveHook.__new__(LiveHook)
        hook.engine = Mock()
        hook.engine.active_languages = ('ru', 'en', 'he')
        hook.engine.language_catalog = None
        hook.thread_id = 17
        hook.user32 = Mock()
        hook.pending_config = None
        hook.settings = Settings()
        lexicon = UserLexicon()
        requested = replace(hook.settings, active_languages=('ru', 'en'),
                            conservative=False, early_correction=True)
        with patch('typebridge_app.cached_early_index', return_value='index'):
            hook.request_config(requested, lexicon)
        pending, copied, index = hook.pending_config
        self.assertEqual(pending.active_languages, ('ru', 'en', 'he'))
        self.assertFalse(pending.conservative)
        self.assertIs(copied, lexicon)
        self.assertEqual(index, 'index')
        hook.user32.PostThreadMessageW.assert_called_once()

    def test_direct_switch_refuses_disabled_target_before_any_side_effect(self):
        hook = LiveHook.__new__(LiveHook)
        hook.engine = Mock()
        hook.engine.active_languages = ('ru', 'en')
        hook.clear_token = Mock()
        hook.emit = Mock()
        hook._in_keyboard_callback = False
        with patch('typebridge_app.WindowsLayouts') as layouts:
            hook._switch_direct(None, 'he')
            layouts.assert_not_called()
        hook.clear_token.assert_not_called()

    def test_disabled_layout_physical_key_passes_through_without_mapping(self):
        hook = make_hook()
        hook.engine.active_languages = ('ru', 'en')
        hook.settings = replace(Settings(), active_languages=('ru', 'en'))
        hook.context = lambda: FocusContext(90, 100, 1, 'he', 'editor.exe', 'test')
        hook.engine.mapper.char = Mock(side_effect=AssertionError('disabled mapper accessed'))
        hook.engine.mapper.word = Mock(side_effect=AssertionError('disabled mapper accessed'))
        hook._trace_browser_key = Mock()
        hook._try_snippet = Mock(side_effect=AssertionError('disabled snippet mapper accessed'))
        result = key(hook, ord('A'))
        self.assertEqual(result, 0)
        hook.engine.mapper.char.assert_not_called()
        hook.engine.mapper.word.assert_not_called()
        hook._trace_browser_key.assert_not_called()
        hook._try_snippet.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_gui_checked_subset_saves_and_preserves_default_order(self):
        with TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            settings = replace(Settings(), active_languages=('he', 'en'))
            destination = Path(folder) / 'settings.json'
            try:
                with (patch('typebridge_app.USER_DIR', Path(folder)),
                      patch('typebridge_app.load_settings', return_value=settings),
                      patch('typebridge_app.load_user_lexicon', return_value=UserLexicon()),
                      patch('typebridge_app.read_registration', return_value=None),
                      patch('typebridge_app.write_registration'),
                      patch('typebridge_app.save_settings',
                            side_effect=lambda s, **kw: save_settings(s, destination, **kw)),
                      patch('typebridge_app.messagebox.showerror'),
                      patch('typebridge_app.check_active_layouts_loaded')):
                    gui = TypeBridgeGui(root)
                    self.assertEqual(gui.current_settings().active_languages, ('he', 'en'))
                    gui.active_language_vars['ru'].set(True)
                    self.assertEqual(gui.current_settings().active_languages,
                                     ('he', 'en', 'ru'))
                    gui.active_language_vars['he'].set(False)
                    saved = gui.save()
                    self.assertEqual(saved.active_languages, ('en', 'ru'))
                    self.assertEqual(load_settings(destination).active_languages, ('en', 'ru'))
                    for lang in ('ru', 'en', 'he'):
                        gui.active_language_vars[lang].set(False)
                    self.assertIsNone(gui.save())
                    self.assertEqual(load_settings(destination).active_languages, ('en', 'ru'))
            finally:
                root.destroy()

    def test_gui_rejects_missing_hkl_before_writing_settings_or_registry(self):
        with TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            initial = Settings()
            try:
                with (patch('typebridge_app.USER_DIR', Path(folder)),
                      patch('typebridge_app.load_settings', return_value=initial),
                      patch('typebridge_app.load_user_lexicon', return_value=UserLexicon()),
                      patch('typebridge_app.read_registration', return_value=None),
                      patch('typebridge_app.write_registration') as registry,
                      patch('typebridge_app.save_settings') as written,
                      patch('typebridge_app.messagebox.showerror') as shown,
                      patch('typebridge_app.check_active_layouts_loaded',
                            side_effect=OSError('HE keyboard layout not available'))):
                    gui = TypeBridgeGui(root)
                    gui.active_language_vars['ru'].set(False)
                    self.assertIsNone(gui.save())
                    registry.assert_not_called()
                    written.assert_not_called()
                    shown.assert_called_once()
            finally:
                root.destroy()


if __name__ == '__main__':
    unittest.main()
