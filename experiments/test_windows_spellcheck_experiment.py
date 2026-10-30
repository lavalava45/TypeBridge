"""Unit-only controls for the independent COM benchmark's synthetic mapper."""

import unittest

from windows_spellcheck_benchmark import (
    LANGUAGES, PHYSICAL, SyntheticMapper, WithheldWords,
    keyboard_maps, keys_for, summarize_latency,
)


class SpellcheckExperimentTests(unittest.TestCase):
    def test_reference_physical_words(self):
        tables = keyboard_maps()
        mapper = SyntheticMapper(tables)
        for lang, word, physical in [
            ('en', 'hello', 'hello'),
            ('ru', 'привет', 'ghbdtn'),
            ('he', 'שלום', 'akuo'),
        ]:
            with self.subTest(lang=lang):
                self.assertEqual(keys_for(word, lang, tables), physical)
                keys = [(ord(char.upper()), False) for char in physical]
                self.assertEqual(mapper.word(keys, lang), word)
        for lang in LANGUAGES:
            self.assertEqual(len(tables[lang]), len(PHYSICAL))

    def test_synthetic_views_do_not_change_live_sets(self):
        base = {'hello', 'world'}
        view = WithheldWords(base, {'world'})
        self.assertIn('hello', view)
        self.assertNotIn('world', view)
        self.assertIn('world', base)

    def test_latency_percentile_is_bounded_by_observations(self):
        measured = summarize_latency([0.5, 0.2, 1.3, 0.1])
        self.assertEqual(measured['n'], 4)
        self.assertEqual(measured['max_ms'], 1.3)
        self.assertLessEqual(measured['p50_ms'], measured['p99_ms'])


if __name__ == '__main__':
    unittest.main()
