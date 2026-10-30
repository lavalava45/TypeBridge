"""Disabled package upgrades cannot silently invalidate saved personal words.

All files are synthetic TempDir fixtures; never touch TypeBridge's real profile.
"""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import zipfile

from language_package_manager import install_directory, update_zip
from language_package_transactions import fingerprint, recover_package_transactions
from language_pack_loader import available_language_catalog
from test_r16_language_packs import write_pack
from test_r16_4g_personal_policy import fixture_v2
from typebridge_app import Settings, save_settings
from user_lexicon import UserLexicon


class PersonalUpdateGuardTests(TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.enterContext(patch('language_package_manager._running_instance_count',
                                return_value=0))
        self.enterContext(patch('language_package_transactions._foreign_typebridge_running',
                                return_value=False))
        self.root = self.home / 'language_packs'
        self.root.mkdir()
        self.src = self.home / 'original'
        self.src.mkdir()
        original, _ = write_pack(self.src, 'fr', langid=0x040C,
                                 script='LATIN', alphabet='abcdé', word='café\n')
        self.old = install_directory(original, self.root)
        self.original_hash = fingerprint(self.old)
        self.catalog = available_language_catalog(self.root)
        self.profile = self.home / 'profile'
        self.profile.mkdir()
        self.settings_path = self.profile / 'settings.json'
        save_settings(replace(Settings(), active_languages=('en',)),
                      self.settings_path, catalog=self.catalog)
        self.lexicon = UserLexicon(catalog=self.catalog)
        self.lexicon.add('words', 'fr', 'café')
        self.lexicon.add('words', 'fr', 'chat')
        self.lexicon.add('exclusions', 'fr', 'été')
        self.lexicon.save(self.profile)
        self.new_src = self.home / 'next'
        self.new_src.mkdir()
        next_pack = fixture_v2(self.new_src, 'fr', 0x040C,
                               'LATIN', 'abcdefghijklmnopqrstuvwxyz', 'chat\n',
                               'ascii_latin_letters')
        self.archive = self.home / 'update.zip'
        with zipfile.ZipFile(self.archive, 'w') as zipped:
            for entry in next_pack.iterdir():
                zipped.write(entry, f'fr/{entry.name}')

    def test_rejects_disabled_incompatible_update_without_changing_package_or_profile(self):
        before = {path.name: path.read_bytes()
                  for path in self.profile.iterdir() if path.is_file()}
        with self.assertRaisesRegex(ValueError, 'personal words or exclusions'):
            update_zip(self.archive, self.root, self.settings_path,
                       running_languages=('en',))
        self.assertEqual(fingerprint(self.old), self.original_hash)
        self.assertEqual(before, {path.name: path.read_bytes()
                                  for path in self.profile.iterdir() if path.is_file()})
        recover_package_transactions(self.root, running_languages=('en',))
        loaded = UserLexicon.load(self.profile,
                                  catalog=available_language_catalog(self.root))
        for kind, word in [('words', 'café'), ('words', 'chat'),
                           ('exclusions', 'été')]:
            self.assertTrue(loaded.contains(kind, 'fr', word))

    def test_valid_upgrade_after_explicit_personal_edit_keeps_other_words(self):
        self.lexicon.remove('words', 'fr', 'café')
        self.lexicon.remove('exclusions', 'fr', 'été')
        self.lexicon.save(self.profile)
        installed, previous = update_zip(self.archive, self.root,
                                          self.settings_path, running_languages=('en',))
        self.assertEqual(installed, self.old)
        self.assertEqual(fingerprint(previous), self.original_hash)
        new_catalog = available_language_catalog(self.root)
        personal = UserLexicon.load(self.profile, catalog=new_catalog)
        self.assertTrue(personal.contains('words', 'fr', 'chat'))
        self.assertNotIn('fr', personal.unavailable['words'])

