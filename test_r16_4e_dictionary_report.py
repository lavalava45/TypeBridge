"""Offline tests of package-based source diagnostics; no user data or hook."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from dictionary_diagnostics import dictionary_report
from language_registry import LANGUAGE_BY_CODE


class UnifiedDictionaryReportTests(unittest.TestCase):
    def test_bundled_builtin_and_external_report_actual_selected_sources(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / 'data'
            bundled_en = root / 'bundled_language_packs' / 'en'
            external_ar = root / 'language_packs' / 'ar'
            catalog = dict(LANGUAGE_BY_CODE)
            catalog['ar'] = SimpleNamespace(dictionary_file='words.txt',
                                            confident_file='confident.txt')
            engine = SimpleNamespace(
                language_catalog=catalog,
                language_policies={
                    'en': SimpleNamespace(directory=bundled_en),
                    'ar': SimpleNamespace(directory=external_ar),
                },
                active_languages=('en', 'ar'),
                words={'en': {'secret-en'}, 'ar': {'secret-ar'}},
                confident={'en': {'secret-en'}, 'ar': {'secret-ar'}},
            )
            report = '\n'.join(dictionary_report(engine, data, pack_root=root / 'language_packs'))
            self.assertIn(str(bundled_en / 'en.txt'), report)
            self.assertIn(str(external_ar / 'words.txt'), report)
            self.assertNotIn(str(data / 'en.txt'), report)
            self.assertNotIn('secret-en', report)
            self.assertNotIn('secret-ar', report)


if __name__ == '__main__':
    unittest.main()
