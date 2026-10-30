"""Source-selection regressions without opening user language directories."""

from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import language_unified_runtime as runtime


class CatalogSourceTests(TestCase):
    def test_verified_package_description_is_catalog_source(self):
        from language_registry import LANGUAGE_BY_CODE
        ru = LANGUAGE_BY_CODE['ru']
        package = SimpleNamespace(description=ru, directory=Path('bundled/ru'))
        with patch.object(runtime, '_compat_builtin_source_plan', return_value=(
                dict(LANGUAGE_BY_CODE), {'ru': package}, ())):
            catalog, verified, fallback, _, bundled = runtime._selected_package_sources(
                Path('synthetic/data'), None, ('ru',), None)
        self.assertIs(catalog['ru'], package.description)
        self.assertIs(verified['ru'], package)
        self.assertEqual(set(catalog), {'ru'})
        self.assertEqual(fallback, ())
        self.assertEqual(bundled, {'ru': package})

    def test_external_langid_cannot_override_disabled_builtin(self):
        from dataclasses import replace
        from language_registry import LANGUAGE_BY_CODE
        installed = SimpleNamespace(description=replace(
            LANGUAGE_BY_CODE['ru'], code='zz'))
        with patch.object(runtime, '_compat_builtin_source_plan', return_value=(
                dict(LANGUAGE_BY_CODE), {}, ())):
            with self.assertRaisesRegex(ValueError, 'LANGID'):
                runtime._selected_package_sources(Path('fixture/data'), Path('packs'),
                                                  ('zz',), {'zz': installed})

    def test_only_selected_codes_are_requested_from_installed_loader(self):
        from dataclasses import replace
        from language_registry import LANGUAGE_BY_CODE
        installed = SimpleNamespace(description=replace(
            LANGUAGE_BY_CODE['ru'], code='zz', langid=0x0422))
        with patch.object(runtime, '_compat_builtin_source_plan', return_value=(
                dict(LANGUAGE_BY_CODE), {}, ())), patch(
                    'language_pack_loader.discover_language_packs',
                    return_value={'zz': installed}) as discover:
            catalog, packages, _, _, _ = runtime._selected_package_sources(
                Path('fixture/data'), Path('packs'), ('zz',), None)
        discover.assert_called_once_with(Path('packs'), selected_codes=('zz',))
        self.assertIs(catalog['zz'], installed.description)
        self.assertEqual(set(catalog), {'zz'})
        self.assertEqual(set(packages), {'zz'})
