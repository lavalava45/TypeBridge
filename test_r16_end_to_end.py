"""R16/3b/4: safe model-only profile→engine→hook integration; no OS input."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from direct_hotkeys import compile_bindings, parse_chord
from engine import LanguageEngine
from incremental_recognition import prepare_early, suggest_live_early
from language_pack_loader import available_language_catalog
from test_r16_dynamic_engine import SyntheticMapper
from test_r16_language_packs import write_pack
from typebridge_app import LiveHook, Settings, load_settings, save_settings
from settings_language_profile import TRANSACTION_NAME
from user_lexicon import UserLexicon
from word_cycle import remember_word
from app_paths import migrate_user_data


class LanguageLifecycleTests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.root = folder
        self.data = folder / 'data'
        self.data.mkdir()
        for name, content in {
            'en.txt': 'hello\n', 'confident_en.txt': 'hello\n',
            'ru.txt': 'дом\n', 'confident_ru.txt': 'дом\n',
            'he.txt': 'שלום\n', 'confident_he.txt': 'שלום\n',
            'he_custom.txt': 'מלך\n', 'exclude.txt': '',
        }.items():
            (self.data / name).write_text(content, encoding='utf-8')
        self.packs = folder / 'language_packs'
        self.packs.mkdir()
        write_pack(self.packs, 'ar', word='مرحبا\n')
        write_pack(self.packs, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='niños\n')
        self.catalog = available_language_catalog(self.packs)
        self.profile = folder / 'profile'
        self.profile.mkdir()
        self.settings_path = self.profile / 'settings.json'

    def save_and_load(self, languages, extra=()):
        expected = replace(Settings(), active_languages=tuple(languages),
                           direct_extra=tuple(extra), early_correction=True,
                           corrections_enabled=True, ui_language='en')
        save_settings(expected, self.settings_path, catalog=self.catalog)
        loaded = load_settings(self.settings_path, catalog=self.catalog)
        self.assertEqual(loaded, expected)
        old = load_settings(self.settings_path)
        self.assertTrue(set(old.active_languages) <= {'ru', 'en', 'he'})
        self.assertLessEqual(len(old.active_languages), 3)
        self.assertEqual(old.direct_extra, ())
        primary = json.loads(self.settings_path.read_text(encoding='utf-8'))
        self.assertFalse(any(x in ('ar', 'es') for x in primary['active_languages']))
        self.assertEqual(primary['direct_extra'], [])
        return loaded

    def make_engine(self, settings, forms, lexicon=None):
        active = settings.active_languages
        with patch('engine.LayoutMapper', return_value=SyntheticMapper(forms)):
            engine = LanguageEngine(self.data, active_languages=active,
                                  pack_root=self.packs, user_lexicon=lexicon,
                                  cache_dir=self.root / 'cache')
        prepare_early(engine)
        return engine

    def test_en_ar_to_en_es_to_original_three_preserves_disabled_data(self):
        extra = (('ar', 'Ctrl+Shift+F4'), ('es', 'Ctrl+Shift+F5'))
        personal = UserLexicon(catalog=self.catalog)
        personal.add('words', 'ar', 'كتاب')
        personal.add('words', 'es', 'años')
        personal.add('words', 'ru', 'Привет')
        personal.save(self.profile)
        personal_hash = {p.name: p.read_bytes() for p in self.profile.glob('user_lexicon*.json')}
        first = self.save_and_load(('en', 'ar'), extra)
        eng1 = self.make_engine(first, {'en':'xxxxx', 'ar':'مرحبا'}, personal)
        self.assertEqual(set(eng1.words), {'en', 'ar'})
        self.assertEqual(set(compile_bindings(first, self.catalog).values()), {'en', 'ar'})
        self.assertEqual(suggest_live_early(eng1, [(65+i, False) for i in range(5)], 'en').language, 'ar')
        second = self.save_and_load(('en', 'es'), extra)
        eng2 = self.make_engine(second, {'en':'xxxxx', 'es':'niños'}, personal)
        self.assertEqual(set(eng2.words), {'en', 'es'})
        self.assertEqual(set(compile_bindings(second, self.catalog).values()), {'en', 'es'})
        self.assertNotIn('ar', eng2.words)
        self.assertEqual(suggest_live_early(eng2, [(65+i, False) for i in range(5)], 'en').language, 'es')
        third = self.save_and_load(('ru', 'en', 'he'), extra)
        eng3 = self.make_engine(third, {'ru':'дом', 'en':'xxx', 'he':'שלום'}, personal)
        self.assertEqual(set(eng3.words), {'ru','en','he'})
        self.assertEqual(set(compile_bindings(third, self.catalog).values()), {'ru','en','he'})
        self.assertEqual(self.save_and_load(('ru','en','he'), extra).active_languages,
                         ('ru','en','he'))
        self.assertEqual({p.name: p.read_bytes() for p in self.profile.glob('user_lexicon*.json')},
                         personal_hash)
        reloaded = UserLexicon.load(self.profile, catalog=self.catalog)
        self.assertTrue(reloaded.contains('words', 'ar', 'كتاب'))
        self.assertTrue(reloaded.contains('words', 'es', 'años'))

    def test_model_hook_uses_active_catalog_early_and_never_installs_hooks(self):
        settings = self.save_and_load(('en','ar'), (('ar','Ctrl+Shift+F4'),))
        engine = self.make_engine(settings, {'en':'xxxxx', 'ar':'مرحبا'})
        callbacks = Mock()
        with (patch('typebridge_app.ctypes.WinDLL', return_value=callbacks),
              patch.object(LiveHook, '_declare_api')):
            hook = LiveHook(engine, settings)
        self.assertEqual(hook._language_from_id(0x0401), 'ar')
        # An installed but inactive pack must not participate in runtime
        # language recognition or silently affect an EN+AR engine.
        self.assertEqual(hook._language_from_id(0x0C0A), '')
        self.assertNotIn('es', hook.engine.active_languages)
        self.assertEqual(set(hook.engine._early_base_index), {'en','ar'})
        self.assertIn(parse_chord('Ctrl+Shift+F4'), compile_bindings(hook.settings, hook.engine.language_catalog))
        callbacks.SetWindowsHookExW.assert_not_called()
        callbacks.SendInput.assert_not_called()
        callbacks.LoadKeyboardLayoutW.assert_not_called()

    def test_missing_installed_hkl_blocks_before_live_hook(self):
        settings = self.save_and_load(('en', 'ar'))
        mock_api = Mock()
        def get_list(count, handles):
            if handles is not None:
                handles[0] = 0x04090409
            return 1
        mock_api.GetKeyboardLayoutList.side_effect = get_list
        with (patch('engine.ctypes.WinDLL', return_value=mock_api),
              patch('engine.configured_layout_entries', return_value={}),
              patch('typebridge_app.LiveHook') as live):
            with self.assertRaises(OSError):
                LanguageEngine(self.data, active_languages=settings.active_languages,
                               pack_root=self.packs)
            live.assert_not_called()
        mock_api.LoadKeyboardLayoutW.assert_not_called()

    def test_known_source_and_concurrent_latin_candidates_are_not_guessed(self):
        (self.data / 'en.txt').write_text('xxxxx\n', encoding='utf-8')
        (self.data / 'confident_en.txt').write_text('xxxxx\n', encoding='utf-8')
        settings = self.save_and_load(('en','es'))
        engine = self.make_engine(settings, {'en':'xxxxx', 'es':'niños'})
        keys = [(65+i, False) for i in range(5)]
        self.assertIsNone(engine.decide_token(keys, 'en'))
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))
        self.assertNotIn('ar', engine.words)

    def test_external_profile_never_uses_old_priority_to_guess_between_targets(self):
        (self.data / 'ru.txt').write_text('книга\n', encoding='utf-8')
        (self.data / 'confident_ru.txt').write_text('книга\n', encoding='utf-8')
        settings = self.save_and_load(('en','ru','es'))
        # EN's legacy priority starts with RU, yet an equally valid Spanish
        # candidate means no automatic correction in a mixed-language set.
        mapper = {'en':'xxxxx', 'ru':'книга', 'es':'niños'}
        engine = self.make_engine(settings, mapper)
        keys = [(65+i, False) for i in range(5)]
        self.assertIsNone(engine.decide_token(keys, 'en'))
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))

    def test_invalid_package_and_corrupt_settings_fail_closed_without_mutation(self):
        settings = self.save_and_load(('en','ar'))
        old = self.settings_path.read_bytes()
        sidecar = self.profile / 'settings_languages.json'
        previous = sidecar.read_bytes()
        # A removed language refuses new startup; neither settings file moves.
        with self.assertRaises(ValueError):
            load_settings(self.settings_path,
                          catalog={k:v for k,v in self.catalog.items() if k!='ar'})
        self.assertEqual(self.settings_path.read_bytes(), old)
        self.assertEqual(sidecar.read_bytes(), previous)
        sidecar.write_bytes(b'{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            load_settings(self.settings_path, catalog=self.catalog)
        self.assertEqual(self.settings_path.read_bytes(), old)

    def test_failed_legacy_commit_does_not_publish_new_language_selection(self):
        original = self.save_and_load(('en', 'ar'))
        before_base = self.settings_path.read_bytes()
        sidecar = self.profile / 'settings_languages.json'
        before_external = sidecar.read_bytes()
        with patch('typebridge_app.os.replace', side_effect=OSError('disk blocked')):
            with self.assertRaises(OSError):
                save_settings(replace(original, active_languages=('en', 'es')),
                              self.settings_path, catalog=self.catalog)
        self.assertEqual(self.settings_path.read_bytes(), before_base)
        self.assertEqual(sidecar.read_bytes(), before_external)
        self.assertEqual(load_settings(self.settings_path, catalog=self.catalog), original)

    def test_second_file_replace_failure_rolls_back_both_files(self):
        import settings_language_profile as pair
        original = self.save_and_load(('en', 'ar'), (('ar', 'Ctrl+Shift+F4'),))
        legacy = self.settings_path.read_bytes()
        sidecar = self.profile / 'settings_languages.json'
        external = sidecar.read_bytes()
        new = replace(original, active_languages=('en', 'es'),
                      quick_search=True, quick_search_key='Ctrl+Alt+J')
        real_replace = pair.os.replace
        failure_count = 0
        def fail_second(src, dst):
            nonlocal failure_count
            if Path(dst) == sidecar and failure_count == 0:
                failure_count += 1
                raise OSError('simulated sidecar replace failure')
            return real_replace(src, dst)
        with patch('settings_language_profile.os.replace', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'sidecar replace failure'):
                save_settings(new, self.settings_path, catalog=self.catalog)
        self.assertEqual(failure_count, 1)
        self.assertEqual(self.settings_path.read_bytes(), legacy)
        self.assertEqual(sidecar.read_bytes(), external)
        self.assertFalse((self.profile / TRANSACTION_NAME).exists())
        self.assertEqual(load_settings(self.settings_path, catalog=self.catalog), original)

    def test_crash_between_files_is_recovered_on_next_load(self):
        import settings_language_profile as pair
        original = self.save_and_load(('en', 'ar'))
        old_legacy = self.settings_path.read_bytes()
        sidecar = self.profile / 'settings_languages.json'
        old_external = sidecar.read_bytes()
        real_write = pair._atomic_write
        def crash_before_second(path, payload):
            if Path(path) == sidecar:
                raise KeyboardInterrupt('simulated process termination')
            return real_write(path, payload)
        with patch('settings_language_profile._atomic_write', side_effect=crash_before_second):
            with self.assertRaises(KeyboardInterrupt):
                save_settings(replace(original, active_languages=('ru', 'es'),
                                      quick_search=True),
                              self.settings_path, catalog=self.catalog)
        self.assertTrue((self.profile / TRANSACTION_NAME).exists())
        self.assertNotEqual(self.settings_path.read_bytes(), old_legacy)
        self.assertEqual(load_settings(self.settings_path, catalog=self.catalog), original)
        self.assertEqual(self.settings_path.read_bytes(), old_legacy)
        self.assertEqual(sidecar.read_bytes(), old_external)
        self.assertFalse((self.profile / TRANSACTION_NAME).exists())

    def test_interrupted_profile_migration_keeps_recovery_journal(self):
        import settings_language_profile as pair
        original = self.save_and_load(('en', 'ar'))
        sidecar = self.profile / 'settings_languages.json'
        real_write = pair._atomic_write
        def interrupt(path, payload):
            if Path(path) == sidecar:
                raise KeyboardInterrupt('simulated crash')
            return real_write(path, payload)
        with patch('settings_language_profile._atomic_write', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                save_settings(replace(original, active_languages=('en', 'es')),
                              self.settings_path, catalog=self.catalog)
        destination = self.root / 'profile_migrated_after_crash'
        copied = migrate_user_data(destination, self.profile)
        self.assertIn(TRANSACTION_NAME, copied)
        self.assertEqual(load_settings(destination / 'settings.json',
                                       catalog=self.catalog), original)
        self.assertFalse((destination / TRANSACTION_NAME).exists())

    def test_commit_finished_but_journal_cleanup_interrupted_keeps_new_pair(self):
        import settings_language_profile as pair
        original = self.save_and_load(('en', 'ar'))
        new = replace(original, active_languages=('en', 'es'),
                      quick_search=True)
        marker = self.profile / TRANSACTION_NAME
        real_unlink = Path.unlink
        def failed_cleanup(path, *args, **kwargs):
            if path == marker:
                raise OSError('simulated crash after two commits')
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', failed_cleanup):
            with self.assertRaisesRegex(OSError, 'crash after two commits'):
                save_settings(new, self.settings_path, catalog=self.catalog)
        self.assertTrue(marker.exists())
        self.assertEqual(load_settings(self.settings_path, catalog=self.catalog), new)
        self.assertFalse(marker.exists())

    def test_failed_rollback_keeps_marker_and_recovery_refuses_unknown_changes(self):
        import settings_language_profile as pair
        original = self.save_and_load(('en', 'ar'))
        marker = self.profile / TRANSACTION_NAME
        sidecar = self.profile / 'settings_languages.json'
        real_write = pair._atomic_write
        def abrupt_exit(path, payload):
            if Path(path) == sidecar:
                raise KeyboardInterrupt('crash')
            return real_write(path, payload)
        with patch('settings_language_profile._atomic_write', side_effect=abrupt_exit):
            with self.assertRaises(KeyboardInterrupt):
                save_settings(replace(original, active_languages=('en', 'es')),
                              self.settings_path, catalog=self.catalog)
        self.assertTrue(marker.exists())
        changed = self.settings_path.read_bytes().replace(b'"quick_search": false',
                                                          b'"quick_search": true')
        self.settings_path.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'edited after interrupted'):
            load_settings(self.settings_path, catalog=self.catalog)
        self.assertTrue(marker.exists())

    def test_manual_cycle_and_undo_snapshot_with_external_languages(self):
        keys = [(65+i, False) for i in range(5)]
        scope = (1, 2, 3, 'editor', 2)
        word = remember_word(keys, 'مرحبا', 'ar', ' ', scope, whole=True,
                             active_languages=('ar','es'))
        self.assertIsNotNone(word)
        language, replacement = word.next_variant(
            SyntheticMapper({'ar':'مرحبا', 'es':'niños'}), ('ar','es'))
        self.assertEqual((language, replacement), ('es', 'niños'))
        # The key snapshot and scope survive manual cycling. The transport
        # still verifies the original focus before Undo/redo on actual input.
        self.assertEqual(word.keys, tuple(keys))
        self.assertEqual(word.scope, scope)
        self.assertEqual(word.text, 'مرحبا')
        self.assertEqual(word.suffix, ' ')

    def test_migration_copies_language_sidecar_and_personal_data(self):
        settings = self.save_and_load(('en','ar'), (('ar','Ctrl+Shift+F4'),))
        personal = UserLexicon(catalog=self.catalog)
        personal.add('words', 'ar', 'كتاب')
        personal.save(self.profile)
        destination = self.root / 'migrated'
        names = migrate_user_data(destination, self.profile)
        self.assertIn('settings_languages.json', names)
        self.assertIn('user_lexicon_packages.json', names)
        self.assertEqual(load_settings(destination / 'settings.json', catalog=self.catalog),
                         settings)
        self.assertTrue(UserLexicon.load(destination, catalog=self.catalog).contains(
            'words', 'ar', 'كتاب'))

    def test_demo_packages_install_as_data_without_any_python_change(self):
        from language_pack_loader import discover_language_packs
        demo_root = Path(__file__).resolve().parent / 'fixtures' / 'r16-demo-packs'
        demo = discover_language_packs(demo_root)
        self.assertEqual(set(demo), {'ar', 'es'})
        for code in ('ar', 'es'):
            self.assertEqual(demo[code].license_id, 'CC0-1.0')
            self.assertEqual(demo[code].normalization, 'casefold')
            self.assertTrue(demo[code].verified_files)


if __name__ == '__main__':
    unittest.main()
