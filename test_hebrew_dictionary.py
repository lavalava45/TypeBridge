"""Offline guardrails for the generated Hebrew corpus."""

import re
import unittest

from build_hebrew_dictionary import min_frequency, normalized_hebrew
from engine import LanguageEngine, read_words, source_data_dir


class HebrewCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hebrew = read_words(source_data_dir() / "he.txt")

    def test_has_broad_hebrew_coverage(self):
        self.assertGreater(len(self.hebrew), 150000)
        for word in ("שלום", "תודה", "בבקשה", "רציתי", "אותך", "לדעת", "עכשיו"):
            with self.subTest(word=word):
                self.assertIn(word, self.hebrew)

    def test_all_entries_are_plain_hebrew_words(self):
        expression = re.compile(r"[\u05d0-\u05ea]{2,40}\Z")
        self.assertTrue(all(expression.fullmatch(word) for word in self.hebrew))

    def test_custom_words_remain_independently_loaded(self):
        self.assertIn("מחשב", LanguageEngine(source_data_dir()).words["he"])
        self.assertIn("מחשב", read_words(source_data_dir() / "he_custom.txt"))

    def test_quarantined_forms_do_not_block_russian_da(self):
        self.assertNotIn("ךכ", self.hebrew)
        self.assertIn("כך", self.hebrew)
        engine = LanguageEngine(source_data_dir())
        # Isolate the Hebrew quarantine from the independent, valid EN "lf".
        engine.words['en'].discard('lf')
        result = engine.decide_token([(ord('L'), False), (ord('F'), False)], "en")
        self.assertEqual((result.language, result.replacement), ("ru", "да"))

    def test_normalizer_rejects_internal_final_forms(self):
        for word in ("ךכ", "ךיא", "ךירצ"):
            self.assertEqual(normalized_hebrew(word), "")
        for word in ("כך", "איך", "צריך"):
            self.assertEqual(normalized_hebrew(word), word)

    def test_normalizer_drops_marks_but_rejects_mixed_scripts(self):
        self.assertEqual(normalized_hebrew("שָׁלוֹם"), "שלום")
        self.assertEqual(normalized_hebrew("123שלום"), "")
        self.assertEqual(normalized_hebrew("hello"), "")
        self.assertGreater(min_frequency(2), min_frequency(3))
        self.assertGreater(min_frequency(3), min_frequency(4))


if __name__ == "__main__":
    unittest.main()
