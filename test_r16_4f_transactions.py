"""R16/4f journal integrity, isolation and actual manager crash boundaries."""

import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from tempfile import TemporaryDirectory

from language_package_transactions import (package_lock, start, mark, fingerprint,
                                           recover_package_transactions)
from language_package_manager import install_directory
from test_r16_language_packs import write_pack


class Package4fTests(TestCase):
    def setUp(self):
        self.enterContext(patch('language_package_transactions._foreign_typebridge_running',
                                return_value=False))
        self.parent = Path(self.enterContext(TemporaryDirectory()))
        self.root = self.parent / 'language_packs'
        self.root.mkdir()
        self.source = self.parent / 'source'
        self.source.mkdir()
        self.pack, _ = write_pack(self.source, 'ar', word='مرحبا\n')

    def test_clean_startup_no_transactions_even_with_unknown_other_process(self):
        with patch('language_package_transactions._foreign_typebridge_running',
                   side_effect=AssertionError('Should not inspect unrelated process')):
            recover_package_transactions(self.root)

    def test_corrupted_checksum_refuses_replay_without_deleting_bytes(self):
        with package_lock(self.root) as storage:
            location, _ = start(storage, self.root, 'ar', 'install',
                                replacement=fingerprint(self.pack))
        journal = location / 'journal.json'
        data = json.loads(journal.read_text(encoding='utf-8'))
        data['stage'] = 'forged'
        journal.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            recover_package_transactions(self.root)
        self.assertTrue(journal.exists())

    def test_legacy_v1_journal_preserved_with_actionable_recovery_error(self):
        with package_lock(self.root) as storage:
            location, data = start(storage, self.root, 'ar', 'install',
                                   replacement=fingerprint(self.pack))
        old = {key: value for key, value in data.items()
               if key not in ('root', 'checksum')}
        journal = location / 'journal.json'
        journal.write_text(json.dumps(old), encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'Legacy R16/4e.*manual recovery'):
            recover_package_transactions(self.root)
        self.assertEqual(json.loads(journal.read_text(encoding='utf-8')), old)
        self.assertTrue(self.pack.is_dir())

    def test_cross_root_replay_forbidden(self):
        with package_lock(self.root) as storage:
            location, data = start(storage, self.root, 'ar', 'install',
                                   replacement=fingerprint(self.pack))
            other = self.parent / 'another_root'
            other.mkdir()
            data['root'] = str(other)
            mark(location, data, 'ready')
        with self.assertRaisesRegex(ValueError, 'another installation root'):
            recover_package_transactions(self.root)
        self.assertTrue(location.exists())

    def test_extra_directory_content_refuses_destructive_cleanup(self):
        with package_lock(self.root) as storage:
            location, _ = start(storage, self.root, 'ar', 'install',
                                replacement=fingerprint(self.pack))
        extra = location / 'personal-file.txt'
        extra.write_text('do not delete', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unknown content'):
            recover_package_transactions(self.root)
        self.assertEqual(extra.read_text(encoding='utf-8'), 'do not delete')

    def test_install_real_publish_rename_fault_recover_clean_startup(self):
        from language_package_manager import os as manager_os
        real = manager_os.rename
        def fault(src, dst):
            if Path(dst) == self.root / 'ar':
                raise OSError('crash before commit')
            return real(src, dst)
        with patch('language_package_manager.os.rename', side_effect=fault):
            with self.assertRaisesRegex(OSError, 'crash before commit'):
                install_directory(self.pack, self.root)
        recover_package_transactions(self.root)
        self.assertFalse((self.root / 'ar').exists())
        self.assertTrue((self.pack / 'manifest.json').is_file())

    def test_unchanged_source_installs_without_nested_journal(self):
        published = install_directory(self.pack, self.root)
        self.assertTrue(published.is_dir())
        storage = self.parent / 'language_pack_transactions'
        self.assertEqual({p.name for p in storage.iterdir()}, {'.lock'})
