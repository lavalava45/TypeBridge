"""Windows ZIP path confinement; no live user directories or keyboard hooks."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import zipfile

from language_package_manager import install_zip
from test_r16_language_packs import write_pack


class WindowsZipConfinementTests(TestCase):
    def setUp(self):
        self.context = TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.home = Path(self.context.name)
        self.root = self.home / 'installed'
        self.root.mkdir()
        self.source = self.home / 'source'
        self.source.mkdir()
        self.valid, _ = write_pack(self.source, 'ar')

    def _archive(self, bad_name):
        archive = self.home / 'fixture.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            for path in self.valid.iterdir():
                target = bad_name if path.name == 'words.txt' else 'ar/' + path.name
                out.writestr(target, path.read_bytes())
        return archive

    def test_unsafe_filename_rejected_before_payload_file_is_opened(self):
        for token in ('ar/C:payload.txt', 'ar/words.txt:stream',
                      'ar/CON.txt', 'ar/COM1.txt', 'ar/aux.md',
                      'ar/trailing .txt ', 'ar/\\server.txt', 'ar/../words.txt'):
            with self.subTest(token=token):
                archive = self._archive(token)
                with patch('language_package_manager.zipfile.ZipFile.open',
                           side_effect=AssertionError('payload opened before validation')):
                    with self.assertRaises(ValueError):
                        install_zip(archive, self.root)
                self.assertFalse((self.root / 'ar').exists())

    def test_valid_ar_zip_still_installs(self):
        result = install_zip(self._archive('ar/words.txt'), self.root)
        self.assertEqual(result, self.root / 'ar')
        self.assertTrue((result / 'words.txt').is_file())
