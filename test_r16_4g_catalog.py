"""Isolated bundled-v2 catalog discovery and compatibility tests."""

import shutil
import tempfile
import unittest
from pathlib import Path

from language_pack_loader import available_language_catalog
from language_registry import LANGUAGE_BY_CODE, validate_active_languages


class BundledCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.installed = self.home / 'language_packs'
        self.installed.mkdir()
        self.bundled = self.home / 'bundled_language_packs'

    def test_old_distribution_without_bundle_uses_legacy_inventory(self):
        self.assertEqual(available_language_catalog(self.installed), LANGUAGE_BY_CODE)

    def test_authenticated_bundled_catalog_uses_manifest_objects(self):
        source = Path(__file__).resolve().parent / 'bundled_language_packs'
        if not source.exists():
            self.skipTest('bundled v2 fixture unavailable')
        shutil.copytree(source, self.bundled)
        catalog = available_language_catalog(self.installed)
        self.assertEqual(set(catalog), set(LANGUAGE_BY_CODE))
        self.assertEqual(validate_active_languages(tuple(catalog), catalog), tuple(catalog))

    def test_selected_broken_bundle_fails_closed_but_inactive_can_isolate(self):
        source = Path(__file__).resolve().parent / 'bundled_language_packs'
        if not source.exists():
            self.skipTest('bundled v2 fixture unavailable')
        shutil.copytree(source, self.bundled)
        (self.bundled / 'he' / 'manifest.json').write_text('{}', encoding='utf-8')
        issues = {}
        catalog = available_language_catalog(self.installed, tolerant=True,
                                              errors=issues, selected_codes=('ru', 'en'))
        self.assertEqual(set(catalog), {'ru', 'en'})
        self.assertIn('he', issues)
        with self.assertRaises((ValueError, OSError)):
            available_language_catalog(self.installed, tolerant=True,
                                       selected_codes=('he',))


if __name__ == '__main__':
    unittest.main()
