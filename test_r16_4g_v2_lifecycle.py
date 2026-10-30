"""An unregistered fourth v2 language through the actual package lifecycle.

Disposable files and synthetic mapper only: no user HKL, hook or EXE.
"""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import zipfile

from direct_hotkeys import compile_bindings, parse_chord
from engine import LanguageEngine
from language_pack_loader import available_language_catalog
from language_package_manager import install_directory, update_zip, remove_package, restore_package
from language_package_transactions import recover_package_transactions
from test_r16_4g_personal_policy import fixture_v2
from test_r16_dynamic_engine import SyntheticMapper
from typebridge_app import Settings, load_settings, save_settings
from user_lexicon import UserLexicon


class V2UnregisteredLifecycleTests(TestCase):
    def test_ar_v2_install_activate_cache_policy_diagnostics_update_remove_restore(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            data = base / 'data'
            data.mkdir()
            (data / 'en.txt').write_text('hello\n', encoding='utf-8')
            (data / 'confident_en.txt').write_text('hello\n', encoding='utf-8')
            (data / 'exclude.txt').write_text('', encoding='utf-8')
            root = base / 'language_packs'
            root.mkdir()
            source = base / 'source'
            source.mkdir()
            fresh = fixture_v2(source, 'ar', 0x0401, 'ARABIC', 'ابتث',
                               'مرحبا\n', 'unicode_script_name')
            profile = base / 'profile'
            profile.mkdir()
            settings_path = profile / 'settings.json'
            with (patch('language_package_manager._running_instance_count', return_value=0),
                  patch('language_package_transactions._foreign_typebridge_running',
                        return_value=False)):
                installed = install_directory(fresh, root)
                self.assertTrue((installed / 'manifest.json').is_file())
                catalog = available_language_catalog(root)
                self.assertIn('ar', catalog)
                self.assertEqual(catalog['ar'].personal_alphabet_policy,
                                 'unicode_script_name')
                selected = replace(Settings(), active_languages=('en', 'ar'),
                                   language_shortcuts=(('ar', 'Ctrl+Shift+F4'),
                                                       ('en', 'Ctrl+Shift+F2')))
                save_settings(selected, settings_path, catalog=catalog)
                loaded = load_settings(settings_path, catalog=catalog)
                self.assertEqual(loaded.active_languages, selected.active_languages)
                self.assertEqual(loaded.language_shortcuts, selected.language_shortcuts)
                # R16 sidecar v1 projects active external shortcuts into this
                # old-EXE field on read, independently of the new source map.
                self.assertEqual(loaded.direct_extra, (('ar', 'Ctrl+Shift+F4'),))
                self.assertEqual(compile_bindings(selected, catalog)[
                    parse_chord('Ctrl+Shift+F4')], 'ar')
                personal = UserLexicon(catalog=catalog)
                personal.add('words', 'ar', 'كتاب')
                personal.add('exclusions', 'ar', 'مدينة', sensitive=True)
                personal.save(profile)
                personal = UserLexicon.load(profile, catalog=catalog)
                self.assertTrue(personal.contains('words', 'ar', 'كتاب'))
                self.assertTrue(personal.contains('exclusions', 'ar', 'مدينة'))
                forms = SyntheticMapper({'en': 'xxxxx', 'ar': 'مرحبا'})
                cache = base / 'cache'
                def engine():
                    with patch('engine.LayoutMapper', return_value=forms):
                        return LanguageEngine(data, pack_root=root,
                                              active_languages=('en', 'ar'),
                                              user_lexicon=personal, cache_dir=cache)
                first = engine()
                second = engine()
                self.assertEqual((first.cache_status, second.cache_status),
                                 ('built', 'hit'))
                self.assertIn('مرحبا', second.words['ar'])
                self.assertIn('مرحبا', second.confident['ar'])
                self.assertEqual(first.exclusions, set())
                keys = [(65 + index, False) for index in range(5)]
                decision = second.decide_token(keys, 'en')
                self.assertIsNotNone(decision)
                self.assertEqual((decision.language, decision.replacement),
                                 ('ar', 'مرحبا'))
                self.assertIsNotNone(second.diagnose_token(
                    keys, 'en', exact_policy=True))
                disabled = replace(selected, active_languages=('en',))
                save_settings(disabled, settings_path, catalog=catalog)
                self.assertNotIn('ar', compile_bindings(disabled, catalog).values())

                replacement = base / 'replacement'
                replacement.mkdir()
                updated = fixture_v2(replacement, 'ar', 0x0401, 'ARABIC',
                                     'ابتث', 'كتاب\n', 'unicode_script_name')
                archive = base / 'ar-updated.zip'
                with zipfile.ZipFile(archive, 'w') as zipped:
                    for entry in updated.iterdir():
                        zipped.write(entry, f'ar/{entry.name}')
                now, old = update_zip(archive, root, settings_path,
                                      running_languages=('en',))
                self.assertIn('كتاب', (now / 'words.txt').read_text(encoding='utf-8'))
                self.assertIn('مرحبا', (old / 'words.txt').read_text(encoding='utf-8'))
                gone = remove_package(root, 'ar', settings_path,
                                      running_languages=('en',))
                self.assertTrue(gone.is_dir())
                self.assertNotIn('ar', available_language_catalog(root))
                preserved = UserLexicon.load(profile, catalog=available_language_catalog(root))
                self.assertIn('ar', preserved.unavailable['words'])
                recover_package_transactions(root, running_languages=('en',))
                restore_package(root, old, settings_path, running_languages=('en',))
                self.assertIn('ar', available_language_catalog(root))
                self.assertTrue(UserLexicon.load(profile,
                    catalog=available_language_catalog(root)).contains(
                        'words', 'ar', 'كتاب'))

