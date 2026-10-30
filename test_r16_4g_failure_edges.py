"""R16/4g final audit regressions: missing packages and live search shortcuts."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from language_pack_loader import available_language_catalog
from language_registry import LANGUAGE_BY_CODE
from quick_search import DEFAULT_ENGINES
from typebridge_app import Settings, TypeBridgeGui, load_settings, save_settings
from user_lexicon import UserLexicon


class FailedCatalogSelectionTests(TestCase):
    def test_absent_selected_package_is_value_error_in_strict_and_tolerant_mode(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / 'language_packs'
            root.mkdir()
            for options in ({}, {'tolerant': True, 'errors': {}}):
                with self.subTest(options=options), self.assertRaisesRegex(
                        ValueError, 'Selected language package'):
                    available_language_catalog(root, selected_codes=('zz',), **options)
            self.assertIn('en', available_language_catalog(
                root, selected_codes=('en',)))
            for invalid in (('ar', 'ar'), ('../outside',)):
                with self.assertRaises(ValueError):
                    available_language_catalog(root, selected_codes=invalid)


class EffectiveSearchHotkeyTests(TestCase):
    def setUp(self):
        folder = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.path = folder / 'settings.json'
        self.catalog = dict(LANGUAGE_BY_CODE)
        self.saved = replace(Settings(), active_languages=('ru', 'en'),
                             language_shortcuts=(('ru', 'Ctrl+Shift+F1'),
                                                 ('en', 'Ctrl+Shift+F2'),
                                                 ('he', 'Ctrl+Shift+F3')))
        save_settings(self.saved, self.path, catalog=self.catalog)
        self.main_bytes = self.path.read_bytes()
        self.sidecar_bytes = (folder / 'settings_languages.json').read_bytes()
        self.gui = SimpleNamespace(
            language_catalog=self.catalog,
            lexicon=UserLexicon(catalog=self.catalog),
            search_settings=self.saved, quick_search=Mock(),
            clipboard_tools=Mock(), tray=None, emit=Mock())

    def apply(self, key):
        with patch('typebridge_app.SETTINGS_PATH', self.path):
            return TypeBridgeGui.apply_search_settings(
                self.gui, True, key, 'Google', DEFAULT_ENGINES)

    def assert_no_mutation(self):
        self.assertEqual(self.path.read_bytes(), self.main_bytes)
        self.assertEqual((self.path.parent / 'settings_languages.json').read_bytes(),
                         self.sidecar_bytes)
        self.gui.quick_search.configure.assert_not_called()

    def test_search_refuses_he_shortcut_while_he_is_still_running(self):
        hook = Mock()
        hook.engine = SimpleNamespace(active_languages=('ru','en','he'),
                                      language_catalog=self.catalog)
        self.gui.hook = hook
        self.gui.run_state = 'running'
        with self.assertRaisesRegex(ValueError, 'Сочетание'):
            self.apply('Ctrl+Shift+F3')
        self.assert_no_mutation()
        hook.request_config.assert_not_called()

    def test_search_refuses_he_shortcut_while_mapper_is_loading(self):
        self.gui.hook = None
        self.gui.run_state = 'starting'
        self.gui.launch_languages = ('ru','en','he')
        with self.assertRaisesRegex(ValueError, 'Сочетание'):
            self.apply('Ctrl+Shift+F3')
        self.assert_no_mutation()

    def test_nonconflicting_search_reaches_live_hook_and_keeps_sidecar(self):
        hook = Mock()
        hook.engine = SimpleNamespace(active_languages=('ru','en','he'),
                                      language_catalog=self.catalog)
        self.gui.hook = hook
        self.gui.run_state = 'running'
        self.apply('Ctrl+Alt+J')
        hook.request_config.assert_called_once()
        self.assertIs(hook.request_config.call_args.args[1], self.gui.lexicon)
        self.assertEqual(hook.request_config.call_args.args[0].quick_search_key,
                         'Ctrl+Alt+J')
        self.assertEqual(self.gui._desired_runtime_config[0].quick_search_key,
                         'Ctrl+Alt+J')
        self.assertEqual(load_settings(self.path,
                         catalog=self.catalog).active_languages, ('ru','en'))
        self.assertEqual((self.path.parent / 'settings_languages.json').read_bytes(),
                         self.sidecar_bytes)
        self.assertIn('после перезапуска',
                      self.gui.emit.call_args.args[1])

