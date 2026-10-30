"""R16/3b/3 model contract: GUI, settings, chords, context, personal words."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from direct_hotkeys import compile_bindings, parse_chord
from engine import LanguageEngine
from language_pack_loader import available_language_catalog
from test_r16_language_packs import write_pack
from typebridge_app import (LiveHook, Settings, TypeBridgeGui, language_for_langid,
                            load_settings, save_settings)
from user_lexicon import UserLexicon, LexiconLoadError


class RuntimePackageTests(unittest.TestCase):
    def test_gui_starts_with_corrupt_disabled_es_and_preserves_its_personal_words(self):
        path = self.folder / 'settings.json'
        save_settings(Settings(), path, catalog=self.catalog)
        personal = UserLexicon(catalog=self.catalog)
        personal.add('words', 'es', 'niños')
        personal.save(self.folder)
        original = (self.folder / 'user_lexicon_packages.json').read_bytes()
        (self.packs / 'es' / 'words.txt').write_text('tampered\n', encoding='utf-8')
        root = tk.Tk()
        root.withdraw()
        try:
            with (patch('typebridge_app.USER_DIR', self.folder),
                  patch('typebridge_app.SETTINGS_PATH', path),
                  patch('typebridge_app.LANGUAGE_PACK_DIR', self.packs),
                  patch('typebridge_app.DEBUG_ENABLED', False),
                  patch('typebridge_app.load_user_lexicon',
                        side_effect=lambda *, catalog=None: UserLexicon.load(
                            self.folder, catalog=catalog))):
                gui = TypeBridgeGui(root)
            self.assertEqual(gui.display_languages, ('ru','en','he'))
            self.assertIn('ar', gui.language_catalog)
            self.assertNotIn('es', gui.language_catalog)
            self.assertIn('niños', gui.lexicon.unavailable['words']['es']['insensitive'])
            self.assertEqual((self.folder / 'user_lexicon_packages.json').read_bytes(), original)
        finally:
            root.destroy()

    def setUp(self):
        base = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.folder = base / 'profile'
        self.folder.mkdir()
        self.packs = base / 'packs'
        self.packs.mkdir()
        write_pack(self.packs, 'ar')
        write_pack(self.packs, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='niños\n')
        self.catalog = available_language_catalog(self.packs)

    def test_settings_roundtrip_and_validation_with_dynamic_selection(self):
        path = self.folder / 'settings.json'
        settings = replace(Settings(), active_languages=('en', 'ar'),
                           direct_extra=(('ar', 'Ctrl+Shift+F4'), ('es', 'Отключено')))
        save_settings(settings, path, catalog=self.catalog)
        self.assertEqual(load_settings(path, catalog=self.catalog), settings)
        original = path.read_bytes()
        # Older EXE sees a valid built-in subset and never parses the sidecar.
        legacy = load_settings(path)
        self.assertEqual(legacy.active_languages, ('en',))
        self.assertEqual(legacy.direct_extra, ())
        with self.assertRaises(ValueError):
            save_settings(replace(settings, active_languages=('ru', 'en', 'ar', 'es')),
                          path, catalog=self.catalog)
        with self.assertRaises(ValueError):
            save_settings(replace(settings, direct_extra=(('ar', 'F4'), ('es', 'F4')),
                          active_languages=('ar', 'es')), path, catalog=self.catalog)
        self.assertEqual(path.read_bytes(), original)

    def test_only_active_external_chords_are_intercepted(self):
        settings = replace(Settings(), active_languages=('en', 'ar'),
                           direct_extra=(('ar', 'Ctrl+Shift+F4'), ('es', 'Ctrl+Shift+F5')))
        bindings = compile_bindings(settings, self.catalog)
        self.assertEqual(bindings[parse_chord('Ctrl+Shift+F4')], 'ar')
        self.assertNotIn(parse_chord('Ctrl+Shift+F5'), bindings)
        self.assertIn(parse_chord('Ctrl+Shift+F2'), bindings)
        with self.assertRaises(ValueError):
            compile_bindings(replace(settings, direct_extra=(('ar', 'Ctrl+Shift+F2'),)),
                             self.catalog)
        with self.assertRaises(ValueError):
            compile_bindings(replace(settings, direct_extra=(('ar', 'Ctrl+Shift+F4'),
                                                              ('ar', 'Ctrl+Alt+F4'))),
                             self.catalog)

    def test_quick_search_conflict_with_active_ar_never_corrupts_profile(self):
        from quick_search import DEFAULT_ENGINES
        path = self.folder / 'settings.json'
        settings = replace(Settings(), active_languages=('en', 'ar'),
                           direct_extra=(('ar', 'Ctrl+Alt+G'),),
                           quick_search=False)
        save_settings(settings, path, catalog=self.catalog)
        sidecar = self.folder / 'settings_languages.json'
        previous_main = path.read_bytes()
        previous_sidecar = sidecar.read_bytes()
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.language_catalog = self.catalog
        gui.search_settings = settings
        gui.quick_search = Mock()
        gui.clipboard_tools = Mock()
        gui.tray = None
        gui.emit = Mock()
        with patch('typebridge_app.SETTINGS_PATH', path):
            with self.assertRaisesRegex(ValueError, 'Сочетание поиска'):
                gui.apply_search_settings(True, 'Ctrl+Alt+G', 'Google', DEFAULT_ENGINES)
            self.assertEqual(path.read_bytes(), previous_main)
            self.assertEqual(sidecar.read_bytes(), previous_sidecar)
            gui.quick_search.configure.assert_not_called()
            self.assertFalse(gui.search_settings.quick_search)
            # A non-conflicting key must be persisted with the *same* catalog.
            gui.apply_search_settings(True, 'Ctrl+Alt+J', 'Google', DEFAULT_ENGINES)
        reloaded = load_settings(path, catalog=self.catalog)
        self.assertTrue(reloaded.quick_search)
        self.assertEqual(reloaded.quick_search_key, 'Ctrl+Alt+J')
        self.assertEqual(reloaded.active_languages, ('en', 'ar'))
        self.assertEqual(reloaded.direct_extra, settings.direct_extra)
        self.assertEqual(sidecar.read_bytes(), previous_sidecar)
        gui.quick_search.configure.assert_called_once()

    def test_inactive_external_chord_does_not_block_search(self):
        from quick_search import DEFAULT_ENGINES
        path = self.folder / 'settings.json'
        settings = replace(Settings(), active_languages=('ru', 'en'),
                           direct_extra=(('ar', 'Ctrl+Alt+G'),))
        save_settings(settings, path, catalog=self.catalog)
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.language_catalog = self.catalog
        gui.search_settings = settings
        gui.quick_search = Mock()
        gui.clipboard_tools = Mock()
        gui.tray = None
        gui.emit = Mock()
        with patch('typebridge_app.SETTINGS_PATH', path):
            gui.apply_search_settings(True, 'Ctrl+Alt+G', 'Google', DEFAULT_ENGINES)
        self.assertTrue(load_settings(path, catalog=self.catalog).quick_search)
        self.assertEqual(load_settings(path, catalog=self.catalog).direct_extra,
                         settings.direct_extra)

    def test_auxiliary_gui_changes_preserve_external_language_pair(self):
        path = self.folder / 'settings.json'
        original = replace(Settings(), active_languages=('en', 'ar'),
                           direct_extra=(('ar', 'Ctrl+Shift+F4'),))
        save_settings(original, path, catalog=self.catalog)
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.language_catalog = self.catalog
        gui.ui_language = Mock()
        gui.ui_language.get.return_value = 'English'
        gui.tray = None
        gui.show_caret_flag = Mock()
        gui.show_caret_flag.get.return_value = False
        gui._apply_caret_indicator = Mock()
        gui._configure_journal = Mock()
        gui.journal_enabled = Mock()
        gui.journal_days = Mock()
        gui.journal_apps = Mock()
        gui.emit = Mock()
        with (patch('typebridge_app.SETTINGS_PATH', path),
              patch('typebridge_app.set_language')):
            gui.toggle_caret_flag()
            gui.apply_journal_settings(True, 7, ('notepad.exe',))
        saved = load_settings(path, catalog=self.catalog)
        self.assertEqual(saved.active_languages, original.active_languages)
        self.assertEqual(saved.direct_extra, original.direct_extra)
        self.assertTrue(saved.show_caret_flag)
        self.assertTrue(saved.journal_enabled)

    def test_language_id_lookup_is_catalog_based_but_legacy_unchanged(self):
        self.assertEqual(language_for_langid(0x0401), '')
        self.assertEqual(language_for_langid(0x0401, self.catalog), 'ar')
        self.assertEqual(language_for_langid(0x0C0A, self.catalog), 'es')
        self.assertEqual(language_for_langid(0x0409, self.catalog), 'en')
        hook = LiveHook.__new__(LiveHook)
        hook.engine = Mock(language_catalog=self.catalog)
        self.assertEqual(hook._language_from_id(0x0401), 'ar')
        hook.engine.language_catalog = None
        self.assertEqual(hook._language_from_id(0x0401), '')

    def test_deferred_hook_does_not_receive_inactive_external_chords(self):
        hook = LiveHook.__new__(LiveHook)
        hook.engine = Mock(active_languages=('ru', 'en', 'he'),
                           language_catalog=None)
        hook.thread_id = 0
        hook.user32 = Mock()
        hook.settings = Settings()
        hook.pending_config = None
        requested = replace(Settings(), active_languages=('en', 'ar'),
                            direct_extra=(('ar', 'Ctrl+Shift+F4'),))
        hook.request_config(requested, UserLexicon())
        pending = hook.pending_config[0]
        self.assertEqual(pending.active_languages, ('ru', 'en', 'he'))
        self.assertEqual(pending.direct_extra, ())
        self.assertNotIn(parse_chord('Ctrl+Shift+F4'), compile_bindings(pending))

    def test_deferred_runtime_refuses_ru_f3_while_he_still_active(self):
        hook = LiveHook.__new__(LiveHook)
        hook.engine = Mock(active_languages=('ru', 'en', 'he'),
                           language_catalog=None)
        hook.thread_id = 0
        hook.user32 = Mock()
        hook.pending_config = None
        proposed = replace(Settings(), active_languages=('ru', 'en'),
                           direct_ru='Ctrl+Shift+F3')
        # Future RU+EN is valid, but HE still owns F3 until restart.
        self.assertEqual(compile_bindings(proposed)[parse_chord('Ctrl+Shift+F3')], 'ru')
        with self.assertRaisesRegex(ValueError, 'Одно сочетание'):
            hook.request_config(proposed, UserLexicon())
        self.assertIsNone(hook.pending_config)
        hook.user32.PostThreadMessageW.assert_not_called()

    def test_gui_refuses_ru_f3_before_settings_or_registry_write(self):
        settings = Settings()
        root = tk.Tk()
        root.withdraw()
        try:
            with (patch('typebridge_app.available_language_catalog', return_value=self.catalog),
                  patch('typebridge_app.load_settings', return_value=settings),
                  patch('typebridge_app.load_user_lexicon', return_value=UserLexicon()),
                  patch('typebridge_app.read_registration') as read_reg,
                  patch('typebridge_app.write_registration') as write_reg,
                  patch('typebridge_app.save_settings') as write_settings,
                  patch('typebridge_app.messagebox.showerror') as show_error):
                gui = TypeBridgeGui(root)
                read_reg.reset_mock()
                gui.active_language_vars['he'].set(False)
                gui.direct_keys['ru'].set('Ctrl+Shift+F3')
                gui.hook = Mock(engine=Mock(
                    active_languages=('ru','en','he'),language_catalog=None))
                self.assertIsNone(gui.save())
                show_error.assert_called_once()
                read_reg.assert_not_called()
                write_reg.assert_not_called()
                write_settings.assert_not_called()
        finally:
            root.destroy()

    def test_legacy_hook_ignores_inactive_saved_external_chords(self):
        settings = replace(Settings(), direct_extra=(('ar', 'Ctrl+Shift+F4'),))
        compiled = compile_bindings(settings)
        self.assertNotIn(parse_chord('Ctrl+Shift+F4'), compiled)
        with self.assertRaises(ValueError):
            save_settings(settings, self.folder / 'settings.json')

    def test_external_personal_word_available_to_dynamic_engine(self):
        from test_r16_dynamic_engine import SyntheticMapper
        data = self.folder / 'data'
        data.mkdir()
        (data / 'exclude.txt').write_text('', encoding='utf-8')
        (data / 'en.txt').write_text('hello\n', encoding='utf-8')
        (data / 'confident_en.txt').write_text('hello\n', encoding='utf-8')
        lexicon = UserLexicon(catalog=self.catalog)
        lexicon.add('words', 'ar', 'كتاب')
        lexicon.save(self.folder)
        lexicon = UserLexicon.load(self.folder, catalog=self.catalog)
        with patch('engine.LayoutMapper', return_value=SyntheticMapper(
                {'en': 'xxxx', 'ar': 'كتاب'})):
            engine = LanguageEngine(data, pack_root=self.packs,
                                    active_languages=('en', 'ar'),
                                    user_lexicon=lexicon)
        keys = [(65 + i, False) for i in range(4)]
        decision = engine.decide_token(keys, 'en')
        self.assertIsNotNone(decision)
        self.assertEqual((decision.language, decision.replacement), ('ar', 'كتاب'))
        self.assertTrue(engine._user_contains('words', 'ar', 'كتاب'))

    def test_gui_shows_installed_languages_and_saves_selection_without_hooks(self):
        root = tk.Tk()
        root.withdraw()
        target = self.folder / 'settings.json'
        settings = replace(Settings(), active_languages=('en', 'ar'))
        lexicon = UserLexicon(catalog=self.catalog)
        try:
            with (patch('typebridge_app.available_language_catalog', return_value=self.catalog),
                  patch('typebridge_app.load_settings', return_value=settings),
                  patch('typebridge_app.load_user_lexicon', return_value=lexicon),
                  patch('typebridge_app.read_registration', return_value=None),
                  patch('typebridge_app.write_registration'),
                  patch('typebridge_app.USER_DIR', self.folder),
                  patch('typebridge_app.save_settings',
                        side_effect=lambda value, **kw: save_settings(value, target, **kw)),
                  patch('typebridge_app.check_active_layouts_loaded')):
                gui = TypeBridgeGui(root)
                self.assertEqual(set(gui.active_language_vars), set(self.catalog))
                self.assertEqual(gui.current_settings().active_languages, ('en', 'ar'))
                gui.direct_keys['ar'].set('Ctrl+Shift+F4')
                gui.active_language_vars['ar'].set(False)
                gui.active_language_vars['es'].set(True)
                saved = gui.save()
                self.assertEqual(saved.active_languages, ('en', 'es'))
                self.assertEqual(dict(saved.direct_extra), {'ar': 'Ctrl+Shift+F4'})
                self.assertEqual(load_settings(target, catalog=self.catalog), saved)
                with patch('typebridge_app.threading.Thread') as worker:
                    gui.start()  # Must not activate the new language without restart.
                    worker.assert_not_called()
                    self.assertEqual(gui.run_state, 'stopped')
        finally:
            root.destroy()

    def test_sidecar_missing_pack_keeps_dormant_words_without_destructive_recovery(self):
        lexicon = UserLexicon(catalog=self.catalog)
        lexicon.add('words', 'ar', 'مرحبا')
        lexicon.save(self.folder)
        primary = (self.folder / 'user_lexicon.json').read_bytes()
        sidecar = (self.folder / 'user_lexicon_packages.json').read_bytes()
        other = {key: value for key, value in self.catalog.items() if key != 'ar'}
        loaded = UserLexicon.load(self.folder, catalog=other)
        self.assertNotIn('ar', loaded.data['words'])
        self.assertEqual(loaded.unavailable['words']['ar']['insensitive'], ('مرحبا',))
        with self.assertRaises(ValueError):
            loaded.contains('words', 'ar', 'مرحبا')
        loaded.save(self.folder)
        self.assertEqual((self.folder / 'user_lexicon.json').read_bytes(), primary)
        self.assertEqual((self.folder / 'user_lexicon_packages.json').read_bytes(), sidecar)


if __name__ == '__main__':
    unittest.main()
