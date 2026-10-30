"""R16/4g: instantiate the REAL Tk settings panel on isolated v2 packages.

No Tk mainloop, hook, SendInput, HKL changes, live settings or user folders.
"""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
from unittest import TestCase
from unittest.mock import patch

from direct_hotkeys import compile_bindings, parse_chord
from language_pack_loader import available_language_catalog
from prepare_bundled_packs import make_bundled_packages
from test_r16_4g_personal_policy import fixture_v2
from test_r16_lexicon_cache import LEGACY
from typebridge_app import (Settings, TypeBridgeGui, language_for_langid,
                            save_settings)
from user_lexicon import UserLexicon


class ActualSettingsPanelTest(TestCase):
    def test_fourth_v2_language_appears_and_is_editable_without_code_registration(self):
        base = Path(self.enterContext(TemporaryDirectory())).resolve()
        data = base / 'data'
        data.mkdir()
        for name, body in LEGACY.items():
            (data / name).write_text(body, encoding='utf-8')
        for name in ('DATA-LICENSE.txt', 'RU-DATA-LICENSE.txt'):
            (data / name).write_text('Synthetic license\n', encoding='utf-8')
        make_bundled_packages(data, base / 'bundled_language_packs')
        packages = base / 'language_packs'
        packages.mkdir()
        fixture_v2(packages, 'ar', 0x0401, 'ARABIC', 'ابتث', 'مرحبا\n',
                   'unicode_script_name')
        catalog = available_language_catalog(packages)
        profile = base / 'profile'
        profile.mkdir()
        settings_path = profile / 'settings.json'
        saved = replace(Settings(), active_languages=('en', 'ar'),
                        language_shortcuts=(('ru', 'Отключено'),
                                            ('en', 'Ctrl+Shift+F2'),
                                            ('ar', 'Ctrl+Shift+F4')))
        save_settings(saved, settings_path, catalog=catalog)

        root = tk.Tk()
        root.withdraw()  # No interactive trial or focus takeover.
        try:
            with (patch('typebridge_app.USER_DIR', profile),
                  patch('app_paths.USER_DIR', profile),
                  patch('typebridge_app.SETTINGS_PATH', settings_path),
                  patch('typebridge_app.LANGUAGE_PACK_DIR', packages),
                  patch('typebridge_app.DEBUG_ENABLED', False),
                  patch('typebridge_app.load_user_lexicon',
                        side_effect=lambda *, catalog=None: UserLexicon.load(
                            profile, catalog=catalog))):
                gui = TypeBridgeGui(root)
            self.assertIn('ar', gui.language_catalog)
            self.assertEqual(gui.language_catalog['ar'].name, 'Fixture ar')
            self.assertEqual(gui.language_catalog['ar'].personal_alphabet_policy,
                             'unicode_script_name')
            self.assertTrue(gui.active_language_vars['ar'].get())
            self.assertFalse(gui.active_language_vars['he'].get())
            self.assertEqual(gui.display_languages, ('en', 'ar'))
            self.assertEqual(language_for_langid(0x0401, gui.language_catalog), 'ar')
            self.assertEqual(gui.direct_keys['ar'].get(), 'Ctrl+Shift+F4')
            actual = gui.current_settings()
            self.assertEqual(actual.active_languages, ('en', 'ar'))
            self.assertEqual(compile_bindings(actual, gui.language_catalog)[
                parse_chord('Ctrl+Shift+F4')], 'ar')
        finally:
            root.destroy()

