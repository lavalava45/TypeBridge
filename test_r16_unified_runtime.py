"""R16/4c integration tests; never access installed user profile or HKL."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import hashlib
import json
import zipfile

from engine import LanguageEngine
from incremental_recognition import prepare_early, suggest_live_early, cached_early_index
from language_package_manager import install_directory, install_zip, save_active_selection
from language_pack_loader import available_language_catalog
from test_r16_4b_parallel import make_v2_fixture
from test_r16_lexicon_cache import LEGACY
from test_r16_dynamic_engine import SyntheticMapper
from test_r16_language_packs import write_pack
from typebridge_app import Settings, save_settings, load_settings, LiveHook, TypeBridgeGui
from user_lexicon import UserLexicon


class UnifiedRuntimeTests(TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.data = self.folder / 'data'
        self.data.mkdir()
        for name, value in LEGACY.items():
            (self.data / name).write_text(value, encoding='utf-8')
        self.packs = self.folder / 'installed'
        self.packs.mkdir()
        self.cache = self.folder / 'cache'
        self.settings = self.folder / 'settings.json'

    def engine(self, active, forms, *, backend='unified', lexicon=None, cache=True):
        with patch('engine.LayoutMapper', return_value=SyntheticMapper(forms)):
            return LanguageEngine(self.data, active_languages=active,
                pack_root=self.packs, engine_backend=backend, user_lexicon=lexicon,
                cache_dir=self.cache if cache else None)

    def test_actual_engine_selects_unified_for_builtins_and_legacy_rolls_back(self):
        forms = {'ru':'руддщ','en':'hello','he':'אקךךם'}
        new = self.engine(('ru','en','he'), forms)
        old = self.engine(('ru','en','he'), forms, backend='legacy')
        self.assertEqual(new.words, old.words)
        self.assertEqual(new.confident, old.confident)
        self.assertEqual(new.exclusions, old.exclusions)
        self.assertIsNotNone(new._policy_engine)
        self.assertIsNone(old._policy_engine)
        keys = [(65+i, False) for i in range(5)]
        for code in ('ru','en','he'):
            with self.subTest(code=code):
                self.assertEqual(new.decide_token(keys, code), old.decide_token(keys, code))
        prepare_early(new)
        prepare_early(old)
        for code in ('ru','en','he'):
            self.assertEqual(suggest_live_early(new, keys, code),
                             suggest_live_early(old, keys, code))

    def test_cache_hit_and_source_change_rebuild_with_active_only(self):
        forms = {'en':'hello','ru':'дом'}
        one = self.engine(('en','ru'), forms)
        two = self.engine(('en','ru'), forms)
        self.assertEqual((one.cache_status, two.cache_status), ('built','hit'))
        self.assertNotIn('he', two.words)
        (self.data / 'ru.txt').write_text('дом\nновый\n', encoding='utf-8')
        changed = self.engine(('en','ru'), forms)
        self.assertEqual(changed.cache_status, 'built')
        self.assertIn('новый', changed.words['ru'])
        self.assertNotIn('he', changed.words)

    def test_install_v1_and_v2_without_code_or_user_profile_mutation(self):
        source = self.folder / 'sources'
        source.mkdir()
        write_pack(source, 'ar', word='مرحبا\n')
        self.assertEqual(install_directory(source / 'ar', self.packs).name, 'ar')
        with self.assertRaises(FileExistsError):
            install_directory(source / 'ar', self.packs)
        # Use an independently valid v2 package from AR sample, leaving
        # the v1 package undisturbed. A new language may use any v2 code.
        spanish = self.folder / 'spanish-source'
        spanish.mkdir()
        write_pack(spanish, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='niños\n')
        doc = json.loads((spanish / 'es' / 'manifest.json').read_text(encoding='utf-8'))
        doc['schema_version'] = 2
        doc['normalization'] = {'dictionary': 'casefold'}
        doc['behavior'] = {
            'ambiguous_destinations': 'reject_multiple',
            'early_prefix_aliases': [], 'source_nonletter_keys': [],
            'personal_alphabet_policy':'unicode_script_name'}
        for entry in doc['additions']:
            entry['normalization'] = {'words':'casefold','confident':'casefold'}
        (spanish / 'es' / 'manifest.json').write_text(
            json.dumps(doc, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(install_directory(spanish / 'es', self.packs).name, 'es')
        catalog = available_language_catalog(self.packs)
        self.assertEqual(set(catalog), {'ru','en','he','ar','es'})
        with patch('engine.LayoutMapper', return_value=SyntheticMapper({
                'en':'xxxxx','es':'niños'})):
            engine = LanguageEngine(self.data, active_languages=('en','es'),
                                    pack_root=self.packs, cache_dir=self.cache)
        self.assertEqual(engine.decide_token([(65+i, False) for i in range(5)],
                                             'en').replacement, 'niños')

    def test_corrupt_unselected_es_does_not_block_en_ar(self):
        write_pack(self.packs, 'ar', word='مرحبا\n')
        es, _ = write_pack(self.packs, 'es', langid=0x0C0A,
                           script='LATIN', alphabet='abcñ', word='niños\n')
        (es / 'words.txt').write_text('damaged\n', encoding='utf-8')
        catalog = available_language_catalog(self.packs, tolerant=True)
        self.assertIn('ar', catalog)
        self.assertNotIn('es', catalog)
        engine = self.engine(('en','ar'), {'en':'xxxxx','ar':'مرحبا'})
        self.assertEqual(engine.decide_token([(65+i, False) for i in range(5)],
                                             'en').language, 'ar')
        with self.assertRaises(ValueError):
            self.engine(('en','es'), {'en':'xxxxx','es':'niños'})

    def test_manifest_signed_wrong_script_word_is_still_rejected(self):
        # A correctly SHA256-signed Arabic package cannot carry Latin words.
        write_pack(self.packs, 'ar', word='English\n')
        with self.assertRaisesRegex(ValueError, 'Invalid script'):
            self.engine(('en','ar'), {'en':'xxxxx','ar':'مرحبا'}, cache=False)

    def test_hot_settings_preserve_dormant_words_and_hook_model(self):
        write_pack(self.packs, 'ar', word='مرحبا\n')
        catalog = available_language_catalog(self.packs)
        lexicon = UserLexicon(catalog=catalog)
        lexicon.add('words','ar','كتاب')
        lexicon.add('words','ru','Привет')
        lexicon.save(self.folder)
        lexicon_files = {p.name:p.read_bytes() for p in
                         self.folder.glob('user_lexicon*.json')}
        save_settings(replace(Settings(), active_languages=('en','ar')),
                      self.settings, catalog=catalog)
        with patch('language_package_manager.save_active_selection') as unused:
            unused.assert_not_called()
        with patch('engine.check_active_layouts_loaded') as hkl:
            changed = save_active_selection(self.settings, self.packs,
                                            ('ru','en','he'))
        self.assertEqual(changed, ('ru','en','he'))
        hkl.assert_called_once()
        self.assertEqual(load_settings(self.settings, catalog=catalog).active_languages,
                         ('ru','en','he'))
        self.assertEqual(lexicon_files, {p.name:p.read_bytes() for p in
                                         self.folder.glob('user_lexicon*.json')})
        self.assertTrue(UserLexicon.load(self.folder, catalog=catalog).contains(
            'words','ar','كتاب'))
        engine = self.engine(('en','ar'), {'en':'xxxxx','ar':'مرحبا'}, lexicon=lexicon)
        prepare_early(engine)
        with (patch('typebridge_app.ctypes.WinDLL', return_value=Mock()) as dll,
              patch.object(LiveHook, '_declare_api')):
            hook = LiveHook(engine, replace(Settings(), active_languages=('en','ar')))
        self.assertEqual(hook._language_from_id(0x0401), 'ar')
        self.assertNotIn('ru', engine.words)
        dll.return_value.SetWindowsHookExW.assert_not_called()

    def test_safe_zip_install_blocks_undeclared_script(self):
        staging = self.folder / 'sources'
        staging.mkdir()
        package, _ = write_pack(staging,'ar',word='مرحبا\n')
        archive = self.folder / 'ar.zip'
        with zipfile.ZipFile(archive,'w') as out:
            for file in package.iterdir():
                out.write(file, f'ar/{file.name}')
        self.assertEqual(install_zip(archive, self.packs).name, 'ar')
        bad = self.folder / 'bad.zip'
        with zipfile.ZipFile(bad,'w') as out:
            out.writestr('es/manifest.json','{}')
            out.writestr('es/__init__.py','raise RuntimeError("executed")')
        with self.assertRaises(ValueError):
            install_zip(bad, self.packs)
        self.assertFalse((self.packs/'es').exists())

    def test_gui_install_action_keeps_running_catalog_and_hook_untouched(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.root = Mock()
        gui.language_catalog = {'en': object()}
        gui.hook = Mock()
        old_catalog = gui.language_catalog
        old_hook = gui.hook
        with (patch('typebridge_app.filedialog.askopenfilename',
                    return_value=str(self.folder / 'language.zip')),
              patch('language_package_manager.install_zip',
                    return_value=self.packs / 'ar') as install,
              patch('typebridge_app.messagebox.showinfo') as info):
            gui.install_language_package()
        install.assert_called_once()
        info.assert_called_once()
        self.assertIs(gui.language_catalog, old_catalog)
        self.assertIs(gui.hook, old_hook)
        gui.hook.request_config.assert_not_called()

    def test_mixed_v1_languages_and_personal_words_match_legacy_at_every_prefix(self):
        write_pack(self.packs, 'ar', word='مرحبا\n')
        write_pack(self.packs, 'es', langid=0x0C0A,
                   script='LATIN', alphabet='abcñ', word='niños\n')
        (self.data / 'ru.txt').write_text('книга\nещё\n', encoding='utf-8')
        (self.data / 'confident_ru.txt').write_text('книга\nещё\n', encoding='utf-8')
        catalog = available_language_catalog(self.packs)
        personal = UserLexicon(catalog=catalog)
        personal.add('words', 'ar', 'كتاب', sensitive=False)
        personal.add('words', 'ru', 'Привет', sensitive=True)
        personal.add('exclusions', 'es', 'años')
        forms = {'en': 'xxxxx', 'ru': 'книга', 'he': 'שלוםם',
                 'ar': 'مرحبا', 'es': 'niños'}
        selections = [
            ('ru','en','he'), ('en','ar'), ('en','es'), ('ru','ar'),
            ('en','ru','es'), ('en','ar','es'), ('en','he','ar'),
            ('ru','he','ar'), ('he','ru','en'),
        ]
        for selection in selections:
            old = self.engine(selection, forms, backend='legacy', lexicon=personal,
                              cache=False)
            new = self.engine(selection, forms, backend='unified', lexicon=personal,
                              cache=False)
            self.assertEqual(old.words, new.words)
            self.assertEqual(old.confident, new.confident)
            prepare_early(old)
            prepare_early(new)
            for source in selection:
                for length in range(1, 6):
                    keys = [(65+i, False) for i in range(length)]
                    with self.subTest(selection=selection, source=source,
                                      length=length):
                        self.assertEqual(suggest_live_early(old, keys, source),
                                         suggest_live_early(new, keys, source))
                        self.assertEqual(old.decide_token(keys, source),
                                         new.decide_token(keys, source))

    def test_live_configuration_changes_conservative_and_personal_policy(self):
        forms = {'en':'xxxxx','ru':'книга'}
        (self.data / 'ru.txt').write_text('книга\n', encoding='utf-8')
        (self.data / 'confident_ru.txt').write_text('книга\n', encoding='utf-8')
        engine = self.engine(('en','ru'), forms, cache=False)
        keys = [(65+i, False) for i in range(5)]
        self.assertEqual(engine.decide_token(keys,'en').language, 'ru')
        personal = UserLexicon()
        personal.add('exclusions', 'ru', 'книга')
        engine.user_lexicon = personal
        self.assertIsNone(engine.decide_token(keys,'en'))
        prepare_early(engine)
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))
        engine.user_lexicon = None
        self.assertEqual(engine.decide_token(keys,'en').language, 'ru')


if __name__ == '__main__':
    from unittest import main
    main()
