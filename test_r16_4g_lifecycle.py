"""R16/4g: external fourth-language lifecycle, all paths isolated."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import zipfile

from decision_diagnostics import explain_token
from direct_hotkeys import compile_bindings, parse_chord
from engine import LanguageEngine
from incremental_recognition import prepare_early, suggest_live_early
from language_pack_loader import available_language_catalog
from language_package_manager import (install_directory, update_zip,
                                      remove_package, restore_package)
from language_package_transactions import recover_package_transactions
from language_package_transactions import package_lock, start, fingerprint
from test_r16_dynamic_engine import SyntheticMapper
from test_r16_language_packs import write_pack
from typebridge_app import Settings, load_settings, save_settings


class ArabicLifecycleTests(TestCase):
    def setUp(self):
        self.enterContext(patch('language_package_manager._running_instance_count', return_value=0))
        self.enterContext(patch('language_package_transactions._foreign_typebridge_running', return_value=False))
        self.home = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.data = self.home / 'data'
        self.data.mkdir()
        (self.data / 'en.txt').write_text('hello\n', encoding='utf-8')
        (self.data / 'confident_en.txt').write_text('hello\n', encoding='utf-8')
        (self.data / 'exclude.txt').write_text('', encoding='utf-8')
        self.root = self.home / 'packages'
        self.root.mkdir()
        self.sources = self.home / 'sources'
        self.sources.mkdir()
        self.pack, _ = write_pack(self.sources, 'ar', word='مرحبا\n')
        self.settings = self.home / 'settings.json'

    def _engine(self, active):
        mapper = SyntheticMapper({'en': 'xxxxx', 'ar': 'مرحبا'})
        with patch('engine.LayoutMapper', return_value=mapper):
            return LanguageEngine(self.data, pack_root=self.root,
                                  active_languages=active,
                                  cache_dir=self.home / 'cache')

    def _archive(self, pack):
        archive = self.home / 'replacement.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            for entry in pack.iterdir():
                zipped.write(entry, f'ar/{entry.name}')
        return archive

    def test_fourth_language_install_discover_activate_engine_diagnostics_hotkeys_update_remove_restore(self):
        installed = install_directory(self.pack, self.root)
        self.assertEqual(installed, self.root / 'ar')
        catalog = available_language_catalog(self.root)
        self.assertIn('ar', catalog)
        self.assertEqual(catalog['ar'].langid, 0x0401)
        chosen = replace(Settings(), active_languages=('en', 'ar'),
                         direct_extra=(('ar', 'Ctrl+Alt+F7'),))
        save_settings(chosen, self.settings, catalog=catalog)
        self.assertEqual(load_settings(self.settings, catalog=catalog), chosen)
        self.assertEqual(compile_bindings(chosen, catalog)[parse_chord('Ctrl+Alt+F7')], 'ar')
        engine = self._engine(('en', 'ar'))
        self.assertIn('مرحبا', engine.words['ar'])
        self.assertIn('ar', engine.confident)
        self.assertIsInstance(engine.exclusions, set)
        keys = [(65+i, False) for i in range(5)]
        self.assertEqual(engine.decide_token(keys, 'en').language, 'ar')
        prepare_early(engine)
        self.assertEqual(suggest_live_early(engine, keys, 'en').language, 'ar')
        self.assertIsNotNone(explain_token(engine, keys, 'en'))
        self.assertTrue((self.home / 'cache').exists())
        # An active package cannot be changed by a running hook.
        with self.assertRaises(ValueError):
            remove_package(self.root, 'ar', self.settings,
                           running_languages=('en', 'ar'))
        disabled = replace(chosen, active_languages=('ru', 'en'))
        save_settings(disabled, self.settings, catalog=catalog)
        self.assertNotIn(parse_chord('Ctrl+Alt+F7'), compile_bindings(disabled, catalog))
        replacement_root = self.home / 'replacement_source'
        replacement_root.mkdir()
        replacement, _ = write_pack(replacement_root, 'ar', word='كتاب\n')
        current, previous = update_zip(self._archive(replacement), self.root,
                                       self.settings, running_languages=('ru', 'en'))
        self.assertIn('كتاب', (current / 'words.txt').read_text(encoding='utf-8'))
        self.assertIn('مرحبا', (previous / 'words.txt').read_text(encoding='utf-8'))
        removed = remove_package(self.root, 'ar', self.settings,
                                 running_languages=('ru', 'en'))
        self.assertFalse(current.exists())
        self.assertTrue(removed.exists())
        recover_package_transactions(self.root, running_languages=('ru', 'en'))
        restored = restore_package(self.root, previous, self.settings,
                                   running_languages=('ru', 'en'))
        self.assertIn('مرحبا', (restored / 'words.txt').read_text(encoding='utf-8'))

    def test_corrupt_installed_disabled_package_and_missing_package(self):
        install_directory(self.pack, self.root)
        disabled = self._engine(('en',))
        self.assertNotIn('ar', disabled.words)
        (self.root / 'ar' / 'words.txt').write_text('tampered\n', encoding='utf-8')
        self.assertEqual(tuple(self._engine(('en',)).words), ('en',))
        with self.assertRaises(ValueError):
            self._engine(('en', 'ar'))
        with self.assertRaises(FileExistsError):
            install_directory(self.pack, self.root)

    def test_replay_interrupted_ar_update_restores_prior_install(self):
        installed = install_directory(self.pack, self.root)
        previous = fingerprint(installed)
        history = self.home / 'language_pack_history' / 'ar-update-synthetic'
        history.mkdir(parents=True)
        backup = history / 'ar'
        with package_lock(self.root) as storage:
            start(storage, self.root, 'ar', 'update', original=previous,
                  backup=backup)
            installed.rename(backup)
        self.assertFalse(installed.exists())
        recover_package_transactions(self.root, running_languages=('ru', 'en'))
        self.assertEqual(fingerprint(installed), previous)
        self.assertFalse(backup.exists())
        self.assertIn('ar', available_language_catalog(self.root))

