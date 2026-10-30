"""Crash-stage replay tests: isolated synthetic packages only."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import os

from language_package_transactions import (package_lock, start, mark,
                                           fingerprint, recover_package_transactions)
from test_r16_language_packs import write_pack
from language_package_manager import install_directory, install_zip, update_zip
from typebridge_app import Settings, save_settings
from language_pack_loader import available_language_catalog
import zipfile


class PackageTransactionRecoveryTests(TestCase):
    def setUp(self):
        self.enterContext(patch('language_package_transactions._foreign_typebridge_running',
                                return_value=False))
        self.enterContext(TemporaryDirectory())
        self.temp = self.enterContext(TemporaryDirectory())
        self.parent = Path(self.temp)
        self.root = self.parent / 'language_packs'
        self.root.mkdir()
        source = self.parent / 'source'
        source.mkdir()
        original, _ = write_pack(source, 'ar', word='مرحبا\n')
        self.old = self.root / 'ar'
        original.rename(self.old)
        self.history = self.parent / 'language_pack_history' / 'ar-update-test'
        self.history.mkdir(parents=True)
        self.backup = self.history / 'ar'

    def _update(self, *, moved_old=False, published=False, marked=False):
        with package_lock(self.root) as storage:
            location, data = start(storage, self.root, 'ar', 'update',
                                   original=fingerprint(self.old), backup=self.backup)
            stage = location / 'typebridge-update-new'
            stage.mkdir()
            source = self.parent / 'replacement'
            source.mkdir()
            replacement, _ = write_pack(source, 'ar', word='كتاب\n')
            replacement.rename(stage / 'ar')
            data['replacement'] = fingerprint(stage / 'ar')
            mark(location, data, 'ready')
            if moved_old:
                self.old.rename(self.backup)
            if marked:
                mark(location, data, 'archived')
            if published:
                (stage / 'ar').rename(self.old)
        recover_package_transactions(self.root)

    def test_clean_startup_does_not_create_transaction_metadata(self):
        storage = self.parent / 'language_pack_transactions'
        self.assertFalse(storage.exists())
        recover_package_transactions(self.root)
        self.assertFalse(storage.exists())

    def test_update_crash_before_old_rename_keeps_original(self):
        self._update()
        self.assertIn('مرحبا', (self.old / 'words.txt').read_text(encoding='utf-8'))
        self.assertFalse(self.backup.exists())

    def test_update_crash_between_renames_rolls_back(self):
        self._update(moved_old=True)
        self.assertIn('مرحبا', (self.old / 'words.txt').read_text(encoding='utf-8'))
        self.assertFalse(self.backup.exists())

    def test_update_crash_after_archived_mark_rolls_back(self):
        self._update(moved_old=True, marked=True)
        self.assertIn('مرحبا', (self.old / 'words.txt').read_text(encoding='utf-8'))

    def test_update_crash_after_publish_keeps_new_and_history(self):
        self._update(moved_old=True, published=True)
        self.assertIn('كتاب', (self.old / 'words.txt').read_text(encoding='utf-8'))
        self.assertIn('مرحبا', (self.backup / 'words.txt').read_text(encoding='utf-8'))

    def test_remove_crash_after_move_retains_archived_bytes(self):
        with package_lock(self.root) as storage:
            start(storage, self.root, 'ar', 'remove',
                  original=fingerprint(self.old), backup=self.backup)
            self.old.rename(self.backup)
        recover_package_transactions(self.root)
        self.assertFalse(self.old.exists())
        self.assertTrue(self.backup.is_dir())

    def test_restore_crash_after_move_keeps_restored_bytes(self):
        self.old.rename(self.backup)
        with package_lock(self.root) as storage:
            start(storage, self.root, 'ar', 'restore',
                  original=fingerprint(self.backup), backup=self.backup)
            self.backup.rename(self.old)
        recover_package_transactions(self.root)
        self.assertTrue(self.old.is_dir())
        self.assertFalse(self.backup.exists())

    def test_tampered_archive_fails_closed(self):
        with package_lock(self.root) as storage:
            start(storage, self.root, 'ar', 'update',
                  original=fingerprint(self.old), replacement={}, backup=self.backup)
            self.old.rename(self.backup)
        (self.backup / 'words.txt').write_text('corruption', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'integrity failure'):
            recover_package_transactions(self.root)
        self.assertFalse(self.old.exists())
        self.assertTrue(self.backup.exists())

    def test_recovery_refuses_package_in_running_hook(self):
        with package_lock(self.root) as storage:
            start(storage, self.root, 'ar', 'update',
                  original=fingerprint(self.old), replacement={}, backup=self.backup)
            self.old.rename(self.backup)
        with self.assertRaisesRegex(RuntimeError, 'running hook'):
            recover_package_transactions(self.root, running_languages=('en', 'ar'))
        self.assertFalse(self.old.exists())
        recover_package_transactions(self.root, running_languages=('ru', 'en'))
        self.assertTrue(self.old.exists())

    def test_foreign_running_instance_blocks_mutating_recovery(self):
        with package_lock(self.root) as storage:
            start(storage, self.root, 'ar', 'update',
                  original=fingerprint(self.old), replacement={}, backup=self.backup)
            self.old.rename(self.backup)
        with patch('language_package_transactions._foreign_typebridge_running',
                   return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'another TypeBridge'):
                recover_package_transactions(self.root, running_languages=('ru', 'en'))
        self.assertFalse(self.old.exists())
        recover_package_transactions(self.root)
        self.assertTrue(self.old.is_dir())

    def _install_fixture(self):
        folder = self.parent / 'for-install'
        folder.mkdir()
        self.old.rename(folder / 'ar')
        return folder / 'ar'

    def test_install_manager_rename_failure_leaves_no_partial_package(self):
        source = self._install_fixture()
        real_rename = os.rename
        def failed_rename(src, dst):
            if Path(dst) == self.old:
                raise OSError('injected rename failure')
            return real_rename(src, dst)
        with patch('language_package_manager.os.rename', side_effect=failed_rename):
            with self.assertRaisesRegex(OSError, 'injected rename failure'):
                install_directory(source, self.root)
        self.assertFalse(self.old.exists())
        recover_package_transactions(self.root)
        self.assertFalse(self.old.exists())

    def test_install_creates_absent_external_pack_root(self):
        source = self._install_fixture()
        new_root = self.parent / 'fresh_language_packs'
        self.assertFalse(new_root.exists())
        installed = install_directory(source, new_root)
        self.assertEqual(installed, new_root / 'ar')
        self.assertEqual(fingerprint(installed), fingerprint(source))

    def test_install_manager_post_rename_sync_failure_committed(self):
        source = self._install_fixture()
        with patch('language_package_manager._sync_directory',
                   side_effect=OSError('injected directory sync')):
            result = install_directory(source, self.root)
        self.assertEqual(result, self.old)
        self.assertEqual(fingerprint(result), fingerprint(source))

    def test_install_manager_after_publish_mark_failure_committed(self):
        source = self._install_fixture()
        from language_package_manager import mark as real_mark
        def fail_published(location, data, stage):
            if stage == 'published':
                raise OSError('injected published mark')
            return real_mark(location, data, stage)
        with patch('language_package_manager.mark', side_effect=fail_published):
            result = install_directory(source, self.root)
        self.assertEqual(result, self.old)
        recover_package_transactions(self.root)

    def test_install_zip_manager_after_publish_mark_failure_committed(self):
        source = self._install_fixture()
        archive = self.parent / 'installation.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            for item in source.iterdir():
                out.write(item, 'ar/' + item.name)
        from language_package_manager import mark as real_mark
        def fail_published(location, data, stage):
            if stage == 'published':
                raise OSError('injected published mark')
            return real_mark(location, data, stage)
        with patch('language_package_manager.mark', side_effect=fail_published):
            result = install_zip(archive, self.root)
        self.assertEqual(result, self.old)
        recover_package_transactions(self.root)

    def _update_fixture(self):
        candidate = self.parent / 'candidate'
        candidate.mkdir()
        next_pack, _ = write_pack(candidate, 'ar', word='كتاب\n')
        archive = self.parent / 'next.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            for item in next_pack.iterdir():
                out.write(item, 'ar/' + item.name)
        settings = self.parent / 'settings.json'
        save_settings(Settings(), settings,
                      catalog=available_language_catalog(self.root))
        return archive, settings

    def test_update_manager_archive_sync_failure_rolls_back(self):
        archive, settings = self._update_fixture()
        from language_package_manager import _sync_directory as real_sync
        def fail_first_sync(path):
            if Path(path) == self.root and not self.old.exists():
                raise OSError('injected archive sync')
            return real_sync(path)
        with patch('language_package_manager._running_instance_count', return_value=1), \
             patch('language_package_manager._sync_directory', side_effect=fail_first_sync):
            with self.assertRaisesRegex(OSError, 'injected archive sync'):
                update_zip(archive, self.root, settings,
                           running_languages=('ru', 'en'))
        self.assertIn('مرحبا', (self.old / 'words.txt').read_text(encoding='utf-8'))

    def test_update_manager_post_publish_mark_failure_is_committed(self):
        archive, settings = self._update_fixture()
        from language_package_manager import mark as real_mark
        def fail_published(location, data, stage):
            if stage == 'published':
                raise OSError('injected update published mark')
            return real_mark(location, data, stage)
        with patch('language_package_manager._running_instance_count', return_value=1), \
             patch('language_package_manager.mark', side_effect=fail_published):
            destination, backup = update_zip(archive, self.root, settings,
                                             running_languages=('ru', 'en'))
        self.assertIn('كتاب', (destination / 'words.txt').read_text(encoding='utf-8'))
        self.assertIn('مرحبا', (backup / 'words.txt').read_text(encoding='utf-8'))

