"""R16/4d unified bundled packages, profile v2 and reversible manager tests.

All data, packages and preferences are created inside TemporaryDirectory.
No real HKL, input hook, Explorer, user profile or running TypeBridge touched.
"""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from itertools import combinations, permutations
from unittest import TestCase
from unittest.mock import patch, Mock
import json
import zipfile

from decision_diagnostics import explain_token, explain_early
from direct_hotkeys import compile_bindings, parse_chord
from engine import LanguageEngine
from incremental_recognition import prepare_early, suggest_live_early
from language_pack_v2 import load_v2_pack
from language_pack_loader import available_language_catalog
from language_package_manager import (install_directory, update_zip,
    remove_package, restore_package)
from prepare_bundled_packs import make_bundled_packages
from test_r16_lexicon_cache import LEGACY
from test_r16_dynamic_engine import SyntheticMapper
from test_r16_language_packs import write_pack
from typebridge_app import Settings, save_settings, load_settings, TypeBridgeGui
from user_lexicon import UserLexicon


class MigrationR164dTests(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        # The real developer desktop may have multiple live TypeBridge
        # processes. All tests use only temporary packages and a simulated
        # single-instance GUI; never inspect or disturb user processes.
        from unittest.mock import patch as mock_patch
        self.enterContext(mock_patch('language_package_manager._running_instance_count',
                                     return_value=1))
        self.data = self.root / 'data'
        self.data.mkdir()
        for name, content in LEGACY.items():
            (self.data / name).write_text(content, encoding='utf-8')
        for name in ('DATA-LICENSE.txt', 'RU-DATA-LICENSE.txt'):
            (self.data / name).write_text('CC BY-SA-4.0 test fixture\n', encoding='utf-8')
        self.bundles = make_bundled_packages(self.data, self.root / 'bundled_language_packs')
        self.installed = self.root / 'language_packs'
        self.installed.mkdir()
        self.settings = self.root / 'settings.json'
        self.catalog = available_language_catalog(self.installed)

    def _engine(self, codes, forms, *, backend='unified', cache=None,
                user_lexicon=None):
        with patch('engine.LayoutMapper', return_value=SyntheticMapper(forms)):
            return LanguageEngine(self.data, active_languages=codes,
                                  pack_root=self.installed, engine_backend=backend,
                                  cache_dir=cache, user_lexicon=user_lexicon)

    def _zip(self, folder, filename):
        archive = self.root / filename
        with zipfile.ZipFile(archive, 'w') as handle:
            for source in folder.iterdir():
                handle.write(source, f'{folder.name}/{source.name}')
        return archive

    def test_builtins_use_real_v2_manifests_and_legacy_decisions_match(self):
        forms = {'ru':'руддщ', 'en':'hello', 'he':'אקךךם'}
        new = self._engine(('ru','en','he'), forms)
        old = self._engine(('ru','en','he'), forms, backend='legacy')
        self.assertTrue(all(new.language_policies[code].manifest == 'manifest.json'
                            for code in forms))
        self.assertEqual(new.words, old.words)
        self.assertEqual(new.confident, old.confident)
        self.assertEqual(new.exclusions, old.exclusions)
        for code in ('ru','en','he'):
            self.assertEqual(load_v2_pack(self.bundles / code).description.code, code)
        prepare_early(old)
        prepare_early(new)
        for size in range(1, 6):
            keys = [(65+i, False) for i in range(size)]
            for code in forms:
                with self.subTest(size=size, lang=code):
                    self.assertEqual(old.decide_token(keys, code),
                                     new.decide_token(keys, code))
                    self.assertEqual(suggest_live_early(old, keys, code),
                                     suggest_live_early(new, keys, code))
                    self.assertEqual(explain_token(old, keys, code),
                                     explain_token(new, keys, code))
                    self.assertEqual(new.diagnose_token(keys, code),
                                     old.diagnose_token(keys, code))

    def test_bundled_he_custom_never_redistributes_private_editable_data(self):
        self.assertEqual((self.bundles/'he'/'he_custom.txt').read_text(encoding='utf-8'),
                         'מחשב\n')
        self.assertNotEqual((self.data/'he_custom.txt').read_bytes(),
                            (self.bundles/'he'/'he_custom.txt').read_bytes())
        he = load_v2_pack(self.bundles/'he')
        custom, = (item for item in he.additions if item.filename == 'he_custom.txt')
        self.assertTrue(custom.editable_source)
        supplement, = (item for item in he.additions
                       if item.filename == 'builtin_supplement.txt')
        self.assertFalse(supplement.editable_source)
        self.assertEqual((self.bundles/'he'/'builtin_supplement.txt').read_text(
            encoding='utf-8').count('\n'), len(__import__('engine').SAMPLE_HEBREW))

    def test_corrupted_selected_bundle_fails_closed_without_fallback(self):
        manifest = self.bundles / 'ru' / 'manifest.json'
        manifest.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self._engine(('ru','en'), {'ru':'дом','en':'cat'})
        self.assertTrue((self.data/'ru.txt').exists())

    def test_every_builtin_order_and_mixed_ar_es_decisions_prefixes_and_rejections(self):
        write_pack(self.installed, 'ar', word='مرحبا\n')
        write_pack(self.installed, 'es', langid=0x0C0A,
                   script='LATIN', alphabet='abcñ', word='niños\n')
        catalog = available_language_catalog(self.installed)
        personal = UserLexicon(catalog=catalog)
        personal.add('words','ar','كتاب')
        personal.add('exclusions','ru','привет')
        forms = {'ru':'руддщ', 'en':'hello', 'he':'אקךךם',
                 'ar':'مرحبا','es':'niños'}
        # Exhaustive ordered 1/2/3-language selections over all five
        # available languages: 5 + 20 + 60 = 85. Including external-only,
        # mixed RTL/LTR and shared-script EN/ES contexts (not just the six
        # historical builtin directions).
        selections = [order for size in (1,2,3) for group in
                      combinations(('ru','en','he','ar','es'), size)
                      for order in permutations(group)]
        self.assertEqual(len(selections), 85)
        for codes in selections:
            old = self._engine(codes, forms, backend='legacy',
                               user_lexicon=personal)
            new = self._engine(codes, forms, user_lexicon=personal)
            self.assertEqual(new.words, old.words)
            self.assertEqual(new.confident, old.confident)
            prepare_early(old)
            prepare_early(new)
            for current in codes:
                for length in range(1, 6):
                    keys = [(65+i, False) for i in range(length)]
                    with self.subTest(codes=codes, current=current, prefix=length):
                        self.assertEqual(old.decide_token(keys,current),
                                         new.decide_token(keys,current))
                        self.assertEqual(suggest_live_early(old,keys,current),
                                         suggest_live_early(new,keys,current))
                        self.assertEqual(explain_token(old,keys,current),
                                         explain_token(new,keys,current))
                        self.assertEqual(explain_early(old,keys,current),
                                         explain_early(new,keys,current))

    def test_corrupt_disabled_bundled_package_never_read_for_other_languages(self):
        (self.bundles/'he'/'he.txt').write_text('damaged\n', encoding='utf-8')
        new = self._engine(('ru','en'), {'ru':'дом','en':'cat'})
        self.assertEqual(tuple(new.words), ('ru','en'))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self._engine(('ru','he'), {'ru':'дом','he':'שלום'})

    def test_bundled_he_custom_casefold_keeps_hebrew_marks_in_confident_only(self):
        original = 'מַלְךְ\n'
        (self.data/'he_custom.txt').write_text(original, encoding='utf-8')
        # The previously editable external he_custom remains an overlay;
        # no signed bundled manifest needs to be modified for personal edits.
        new = self._engine(('he',), {'he':'שלום'})
        old = self._engine(('he',), {'he':'שלום'}, backend='legacy')
        self.assertEqual(new.words, old.words)
        self.assertEqual(new.confident, old.confident)
        self.assertIn('מלך', new.words['he'])
        self.assertIn('מַלְךְ', new.confident['he'])

    def test_universal_hotkey_sidecar_keeps_dormant_codes_and_old_exe_projection(self):
        write_pack(self.installed, 'ar', word='مرحبا\n')
        catalog = available_language_catalog(self.installed)
        saved = replace(Settings(), active_languages=('en','ar'),
                        direct_extra=(('ar','Ctrl+Alt+F7'),),
                        language_shortcuts=(('ru','Ctrl+Alt+F1'),
                                            ('en','Ctrl+Alt+F2'),
                                            ('he','Отключено'),
                                            ('ar','Ctrl+Alt+F7'),
                                            ('es','Ctrl+Alt+F8')))
        save_settings(saved, self.settings, catalog=catalog)
        self.assertEqual(load_settings(self.settings, catalog=catalog), saved)
        self.assertEqual(json.loads((self.root/'settings_languages.json').read_text())['version'], 1)
        # Old EXE receives the old format, not new fields or external codes.
        main = json.loads(self.settings.read_text(encoding='utf-8'))
        self.assertEqual(main['language_shortcuts'], [list(row) for row in saved.language_shortcuts])
        self.assertEqual(main['active_languages'], ['en'])
        bindings = compile_bindings(saved, catalog)
        self.assertEqual(bindings[parse_chord('Ctrl+Alt+F7')], 'ar')
        self.assertNotIn(parse_chord('Ctrl+Alt+F8'), bindings)
        # Remove a disabled language from catalog: its inactive shortcut stays.
        changed = replace(saved, active_languages=('ru','en'))
        save_settings(changed, self.settings, catalog=catalog)
        missing = {key:value for key,value in catalog.items() if key != 'ar'}
        loaded = load_settings(self.settings, catalog=missing)
        self.assertIn(('ar','Ctrl+Alt+F7'), loaded.language_shortcuts)
        self.assertEqual(compile_bindings(loaded, missing)[parse_chord('Ctrl+Alt+F1')], 'ru')

    def test_v2_settings_commit_rolls_back_both_files_on_second_write_failure(self):
        import settings_language_profile as journal
        original = replace(Settings(), active_languages=('ru','en'),
            language_shortcuts=(('ru','Ctrl+Alt+F1'), ('en','Ctrl+Alt+F2')))
        save_settings(original, self.settings, catalog=self.catalog)
        before = (self.settings.read_bytes(),
                  (self.root/'settings_languages.json').read_bytes())
        changed = replace(original, active_languages=('he','en'),
                          language_shortcuts=(('ru','Ctrl+Alt+F7'),
                                              ('en','Ctrl+Alt+F2'),
                                              ('he','Ctrl+Alt+F8')))
        writer = journal._atomic_write
        once = {'failed': False}
        def fail_sidecar(path, data):
            if (Path(path).name == 'settings_languages.json'
                    and not once['failed']):
                once['failed'] = True
                raise OSError('simulated interrupted sidecar publication')
            return writer(path, data)
        with patch('settings_language_profile._atomic_write', side_effect=fail_sidecar):
            with self.assertRaisesRegex(OSError, 'simulated interrupted'):
                save_settings(changed, self.settings, catalog=self.catalog)
        self.assertEqual((self.settings.read_bytes(),
                          (self.root/'settings_languages.json').read_bytes()), before)
        self.assertEqual(load_settings(self.settings, catalog=self.catalog), original)

    def test_v1_sidecar_unavailable_inactive_language_keeps_shortcut(self):
        write_pack(self.installed, 'ar', word='مرحبا\n')
        original = replace(Settings(), active_languages=('en','ru'),
                           direct_extra=(('ar', 'Ctrl+Alt+F7'),))
        catalog = available_language_catalog(self.installed)
        save_settings(original, self.settings, catalog=catalog)
        # Remove only our synthetic temporary fixture; untouched user data.
        from shutil import rmtree
        rmtree(self.installed/'ar')
        absent = available_language_catalog(self.installed)
        loaded = load_settings(self.settings, catalog=absent)
        self.assertIn(('ar', 'Ctrl+Alt+F7'), loaded.language_shortcuts)
        self.assertEqual(loaded.active_languages, ('en','ru'))
        self.assertNotIn(parse_chord('Ctrl+Alt+F7'), compile_bindings(loaded, absent))
        save_settings(loaded, self.settings, catalog=absent)
        self.assertEqual(load_settings(self.settings, catalog=absent), loaded)

    def test_update_remove_restore_archive_preserves_settings_bytes(self):
        sources = self.root / 'sources'
        sources.mkdir()
        old,_ = write_pack(sources, 'ar', word='مرحبا\n')
        install_directory(old, self.installed)
        save_settings(replace(Settings(), active_languages=('ru','en')),
                      self.settings, catalog=available_language_catalog(self.installed))
        before = {p.name:p.read_bytes() for p in self.root.glob('settings*.json')}
        personal = UserLexicon(catalog=available_language_catalog(self.installed))
        personal.add('words','ar','كتاب')
        personal.add('exclusions','ar','مرحبا')
        personal.save(self.root)
        user_before = {p.name:p.read_bytes() for p in self.root.glob('user_lexicon*.json')}
        candidate = self.root / 'candidate'
        candidate.mkdir()
        new,_ = write_pack(candidate, 'ar', word='كتاب\n')
        archive = self._zip(new, 'update.zip')
        destination, previous = update_zip(archive, self.installed, self.settings,
                                           running_languages=('ru','en'))
        self.assertIn('كتاب', (destination/'words.txt').read_text(encoding='utf-8'))
        self.assertIn('مرحبا', (previous/'words.txt').read_text(encoding='utf-8'))
        removed = remove_package(self.installed, 'ar', self.settings,
                                 running_languages=('ru','en'))
        self.assertFalse((self.installed/'ar').exists())
        self.assertTrue(removed.is_dir())
        restored = restore_package(self.installed, previous, self.settings,
                                   running_languages=('ru','en'))
        self.assertIn('مرحبا', (restored/'words.txt').read_text(encoding='utf-8'))
        self.assertEqual({p.name:p.read_bytes() for p in
                          self.root.glob('settings*.json')}, before)
        self.assertEqual({p.name:p.read_bytes() for p in
                          self.root.glob('user_lexicon*.json')}, user_before)

    def test_failed_update_renames_previous_package_back(self):
        sources = self.root/'original'
        sources.mkdir()
        old,_ = write_pack(sources,'ar',word='مرحبا\n')
        install_directory(old, self.installed)
        save_settings(replace(Settings(), active_languages=('ru','en')),
                      self.settings, catalog=available_language_catalog(self.installed))
        before = {f.name:f.read_bytes() for f in (self.installed/'ar').iterdir()}
        next_root = self.root/'next'
        next_root.mkdir()
        next_pack,_ = write_pack(next_root,'ar',word='كتاب\n')
        archive = self._zip(next_pack,'failing.zip')
        import language_package_manager as manager
        real_rename = manager.os.rename
        def fail_stage(source, target):
            if str(source).find('typebridge-update') >= 0 and Path(target) == self.installed/'ar':
                raise OSError('intentional publish failure')
            return real_rename(source, target)
        with patch('language_package_manager.os.rename', side_effect=fail_stage):
            with self.assertRaisesRegex(OSError, 'intentional publish failure'):
                update_zip(archive, self.installed, self.settings,
                           running_languages=('ru','en'))
        self.assertEqual({f.name:f.read_bytes() for f in
                          (self.installed/'ar').iterdir()}, before)

    def test_selected_or_running_package_update_remove_are_blocked(self):
        folder = self.root / 'sources'
        folder.mkdir()
        package,_ = write_pack(folder,'ar',word='مرحبا\n')
        install_directory(package, self.installed)
        catalog = available_language_catalog(self.installed)
        save_settings(replace(Settings(), active_languages=('en','ar')),
                      self.settings, catalog=catalog)
        with self.assertRaises(ValueError):
            remove_package(self.installed, 'ar', self.settings,
                           running_languages=('en','ar'))
        archive = self._zip(package,'active.zip')
        with self.assertRaises(ValueError):
            update_zip(archive, self.installed, self.settings,
                       running_languages=('en','ar'))
        save_settings(replace(Settings(), active_languages=('en','ru')),
                      self.settings, catalog=catalog)
        with self.assertRaises(ValueError):
            remove_package(self.installed, 'ar', self.settings,
                           running_languages=('en','ar'))
        self.assertTrue((self.installed/'ar').exists())

    def test_two_running_instances_block_disabled_package_update(self):
        src = self.root / 'src-multiple'
        src.mkdir()
        language, _ = write_pack(src, 'ar', word='مرحبا\n')
        install_directory(language, self.installed)
        save_settings(replace(Settings(), active_languages=('ru','en')),
                      self.settings, catalog=available_language_catalog(self.installed))
        with patch('language_package_manager._running_instance_count', return_value=2):
            with self.assertRaisesRegex(RuntimeError, 'Several TypeBridge'):
                remove_package(self.installed, 'ar', self.settings,
                               running_languages=('ru','en'))
        self.assertTrue((self.installed/'ar').is_dir())

    def test_gui_update_delete_actions_are_nonintrusive_on_model_boundary(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.root = Mock()
        gui.language_catalog = available_language_catalog(self.installed)
        gui.launch_languages = ('ru', 'en')
        gui.hook = Mock()
        with (patch('typebridge_app.filedialog.askopenfilename',
                    return_value=str(self.root/'update.zip')),
              patch('language_package_manager.update_zip',
                    return_value=(self.installed/'ar', self.root/'history'/'ar')) as update,
              patch('typebridge_app.messagebox.showinfo')):
            gui.update_language_package()
        self.assertEqual(update.call_args.kwargs['running_languages'], ('ru','en'))
        gui.language_catalog = {**gui.language_catalog, 'ar': Mock()}
        with (patch('typebridge_app.simpledialog.askstring',return_value='ar'),
              patch('typebridge_app.messagebox.askyesno',return_value=True),
              patch('language_package_manager.remove_package',
                    return_value=self.root/'history'/'ar') as remove,
              patch('typebridge_app.messagebox.showinfo')):
            gui.remove_language_package()
        self.assertEqual(remove.call_args.kwargs['running_languages'], ('ru','en'))
        gui.hook.request_stop.assert_not_called()
        gui.hook.request_config.assert_not_called()


if __name__ == '__main__':
    from unittest import main
    main()
