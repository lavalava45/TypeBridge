"""Offline unit tests of source provenance, OOV filtering and noise sampling."""

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from collect_independent_spellcheck_corpus import valid_title
from independent_spellcheck_benchmark import (make_samples, script_ok,
                                               wilson_upper)
from windows_spellcheck_benchmark import keyboard_maps


class IndependentResearchTests(unittest.TestCase):
    def test_public_title_is_not_cross_script_or_multitoken(self):
        self.assertTrue(valid_title('Барнаул', 'ru'))
        self.assertTrue(valid_title('Jerusalem', 'en'))
        self.assertTrue(valid_title('ירושלים', 'he'))
        self.assertFalse(valid_title('Jerusalem עיר', 'he'))
        self.assertFalse(valid_title('Русская Википедия', 'ru'))
        self.assertFalse(valid_title('שלוםworld', 'en'))

    def test_filter_external_titles_using_typebridge_membership(self):
        snapshot = {'kind': 'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT',
                    'languages': {
                        'ru': {'titles': [{'title': 'Завгородний', 'pageid': 1,
                                           'source_url': 'https://ru.wikipedia.org/?curid=1'}]},
                        'en': {'titles': [{'title': 'Peverell', 'pageid': 2,
                                           'source_url': 'https://en.wikipedia.org/?curid=2'},
                                          {'title': 'Knownword', 'pageid': 3,
                                           'source_url': 'https://en.wikipedia.org/?curid=3'}]},
                        'he': {'titles': [{'title': 'ירושלמים', 'pageid': 4,
                                           'source_url': 'https://he.wikipedia.org/?curid=4'}]},
                    }}
        catalog = {lang: SimpleNamespace(unicode_script=script)
                   for lang, script in [('ru','CYRILLIC'), ('en','LATIN'),
                                        ('he','HEBREW')]}
        fake_policy = SimpleNamespace(
            packs_as_catalog=catalog,
            normalize=lambda lang, word: word.casefold(),
            words={'ru': set(), 'en': {'knownword'}, 'he': set()},
            confident={'ru': set(), 'en': set(), 'he': set()})
        self.assertTrue(script_ok('peverell', 'en', catalog))
        self.assertFalse(script_ok('привет', 'en', catalog))
        cases, stats = make_samples(snapshot, fake_policy, keyboard_maps(),
                                    noise_per_language=4, typo_per_language=1,
                                    seed=123)
        self.assertEqual(stats['en']['independently_attested_OOV_titles'], 1)
        self.assertEqual(stats['ru']['independently_attested_OOV_titles'], 1)
        self.assertEqual(stats['he']['independently_attested_OOV_titles'], 1)
        kinds = Counter(row['kind'] for row in cases)
        self.assertEqual(kinds['attested_OOV_wrong_layout'], 6)
        self.assertEqual(kinds['attested_OOV_source'], 3)
        self.assertEqual(kinds['attested_OOV_single_key_typo'], 3)
        self.assertEqual(kinds['random_key_noise'], 12)
        self.assertEqual(cases, make_samples(snapshot, fake_policy, keyboard_maps(),
                         noise_per_language=4, typo_per_language=1, seed=123)[0])

    def test_reject_missing_language_and_wilson_bound(self):
        with self.assertRaises(ValueError):
            make_samples({'kind': 'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT',
                          'languages': {'en': {'titles': []}}}, None,
                         keyboard_maps())
        self.assertIsNone(wilson_upper(0, 0))
        self.assertGreater(wilson_upper(0, 100), 0)
        self.assertGreater(wilson_upper(10, 100), .1)


if __name__ == '__main__':
    unittest.main()
