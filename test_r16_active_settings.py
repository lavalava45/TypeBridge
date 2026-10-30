"""R16/2b: persistence/migration tests, no hook or physical input."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
from unittest.mock import patch

from app_paths import migrate_user_data
from language_registry import DEFAULT_ACTIVE_LANGUAGES
from typebridge_app import Settings, TypeBridgeGui, load_settings, save_settings
from user_lexicon import UserLexicon


class ActiveSettingsTests(unittest.TestCase):
    def test_legacy_settings_default_to_original_languages(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            for payload in ({}, {'ui_language': 'en', 'conservative': False},
                            {'direct_he': 'Отключено', 'cycle_key': 'Pause'}):
                path.write_text(json.dumps(payload), encoding='utf-8')
                settings = load_settings(path)
                self.assertEqual(settings.active_languages, DEFAULT_ACTIVE_LANGUAGES)
                self.assertEqual(settings.ui_language, payload.get('ui_language', 'ru'))
                self.assertEqual(settings.conservative, payload.get('conservative', True))

    def test_roundtrip_preserves_explicit_order_and_other_settings(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            for active in (('ru', 'en'), ('he', 'en'), ('he',),
                           DEFAULT_ACTIVE_LANGUAGES):
                settings = replace(Settings(), active_languages=active,
                                   ui_language='en', early_correction=True,
                                   conservative=False, direct_he='Отключено')
                save_settings(settings, path)
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(saved['active_languages'], list(active))
                self.assertEqual(load_settings(path), settings)

    def test_invalid_config_is_rejected_without_touching_saved_settings(self):
        invalid = [None, 'ru', 42, [], ['ru', 'ru'], ['ru', 'ar'],
                   ['en', None], ['he', 0], {'ru': True}, ['RU', 'en']]
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            original = replace(Settings(), active_languages=('ru', 'en'))
            save_settings(original, path)
            original_bytes = path.read_bytes()
            for value in invalid:
                with self.subTest(value=value):
                    path.write_text(json.dumps({'active_languages': value}),
                                    encoding='utf-8')
                    with self.assertRaises(ValueError):
                        load_settings(path)
                    path.write_bytes(original_bytes)
                    with self.assertRaises(ValueError):
                        save_settings(replace(original, active_languages=value), path)
                    self.assertEqual(path.read_bytes(), original_bytes)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_migration_copies_profile_without_losing_subsets_or_personal_files(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root / 'old', root / 'new'
            source.mkdir()
            expected = replace(Settings(), active_languages=('en', 'he'),
                               ui_language='he')
            save_settings(expected, source / 'settings.json')
            personal = {'user_lexicon.json': b'{"keep": "personal"}',
                        'snippets.json': b'{"keep": "snippet"}'}
            for name, data in personal.items():
                (source / name).write_bytes(data)
            self.assertEqual(set(migrate_user_data(destination=target, source=source)),
                             {'settings.json', 'user_lexicon.json', 'snippets.json'})
            self.assertEqual(load_settings(target / 'settings.json'), expected)
            for name, data in personal.items():
                self.assertEqual((target / name).read_bytes(), data)
            self.assertEqual(migrate_user_data(destination=target, source=source), [])

    def test_gui_settings_save_path_keeps_loaded_active_subset(self):
        with TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            initial = replace(Settings(), active_languages=('en', 'ru'),
                              corrections_enabled=False)
            path = Path(folder) / 'saved-by-gui.json'
            try:
                real_save = save_settings
                with (patch('typebridge_app.USER_DIR', Path(folder)),
                      patch('typebridge_app.load_settings', return_value=initial),
                      patch('typebridge_app.load_user_lexicon',
                            return_value=UserLexicon()),
                      patch('typebridge_app.read_registration', return_value=None),
                      patch('typebridge_app.write_registration'),
                      patch('typebridge_app.save_settings',
                            side_effect=lambda settings, **kw: real_save(settings, path, **kw))):
                    gui = TypeBridgeGui(root)
                    calculated = gui.current_settings()
                    self.assertEqual(calculated.active_languages, initial.active_languages)
                    self.assertEqual(calculated.ui_language, initial.ui_language)
                    gui.conservative.set(False)
                    saved = gui.save()
                    self.assertIsNotNone(saved)
                    self.assertEqual(saved.active_languages, ('en', 'ru'))
                    self.assertFalse(saved.conservative)
                    self.assertEqual(load_settings(path, catalog=gui.language_catalog), saved)
                    self.assertEqual(load_settings(path).active_languages, ('en', 'ru'))
            finally:
                root.destroy()


if __name__ == '__main__':
    unittest.main()
