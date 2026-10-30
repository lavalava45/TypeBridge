"""R16/4f catalog shortcuts and legacy persistence, isolated from live input."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from direct_hotkeys import Chord, compile_bindings, parse_chord
from settings_language_profile import read_language_profile
from typebridge_app import Settings, load_settings, save_settings, language_for_langid


class UniversalSettingsTests(TestCase):
    def setUp(self):
        self.catalog = {code: SimpleNamespace(langid=langid) for code, langid in
                        [('ru', 0x419), ('en', 0x409), ('he', 0x40d), ('ar', 0x401)]}

    def test_active_catalog_ignores_disabled_shortcuts_and_hkl(self):
        value = replace(Settings(), active_languages=('ar', 'en'),
                        language_shortcuts=(('ru', 'Ctrl+Shift+F1'),
                                            ('he', 'Ctrl+Shift+F3'),
                                            ('ar', 'Ctrl+Shift+F4'),
                                            ('en', 'Ctrl+Shift+F2')))
        bindings = compile_bindings(value, self.catalog)
        self.assertEqual(set(bindings.values()), {'ar', 'en'})
        self.assertEqual(bindings[parse_chord('Ctrl+Shift+F4')], 'ar')
        self.assertEqual(language_for_langid(0x401, self.catalog), 'ar')

    def test_sidecar_v1_and_old_direct_fields_survive_roundtrip(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            original = replace(Settings(), active_languages=('en', 'ar'),
                               direct_ru='Отключено',
                               language_shortcuts=(('ru', 'Отключено'),
                                                   ('en', 'Ctrl+Shift+F2'),
                                                   ('ar', 'Ctrl+Shift+F4')))
            save_settings(original, path, catalog=self.catalog)
            document = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(document['active_languages'], ['en'])
            self.assertEqual(document['direct_ru'], 'Отключено')
            self.assertEqual(read_language_profile(path, self.catalog)[0], ('en', 'ar'))
            self.assertEqual(read_language_profile(path, self.catalog)[2], ())
            restored = load_settings(path, catalog=self.catalog)
            self.assertEqual(restored.active_languages, original.active_languages)
            self.assertEqual(restored.language_shortcuts, original.language_shortcuts)
            self.assertEqual(set(compile_bindings(restored, self.catalog).values()),
                             {'en', 'ar'})

