"""Deterministic regression tests without hooks, physical input or focus changes."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from engine import LanguageEngine, read_words
from frequency_rank import load_frequencies, normalize_word, resolve_tie
from incremental_recognition import prepare_early, suggest_early
from prepare_frequency_indices import frequency_table
from prepare_frequency_indices import verified_source
from user_lexicon import UserLexicon


class StubMapper:
    def __init__(self, forms):
        self.forms = forms

    def word(self, keys, language):
        return self.forms[language][:len(keys)]


def sample_engine(forms=None):
    forms = forms or {"en": "abc", "ru": "дом", "he": "בית"}
    engine = LanguageEngine.__new__(LanguageEngine)
    engine.mapper = StubMapper(forms)
    engine.words = {"en": set(), "ru": {forms["ru"]}, "he": {forms["he"]}}
    engine.confident = {"en": set(), "ru": {forms["ru"]}, "he": {forms["he"]}}
    engine.exclusions = set()
    engine.user_lexicon = None
    engine.conservative = True
    engine.frequencies = {"en": {}, "ru": {normalize_word(forms["ru"], "ru"): 120.0},
                          "he": {forms["he"]: 2.0}}
    keys = [(65 + index, False) for index in range(len(forms["en"]))]
    return engine, keys


class FrequencyRankTests(unittest.TestCase):
    def test_widely_separated_variants(self):
        engine, keys = sample_engine()
        decision = engine.decide(keys, "en")
        self.assertEqual((decision.language, decision.replacement), ("ru", "дом"))
        self.assertIn("priority", decision.reason)

    def test_close_variants_use_priority(self):
        engine, keys = sample_engine()
        engine.frequencies["he"]["בית"] = 20.0
        self.assertEqual(engine.decide(keys, "en").replacement, "дом")

    def test_absolute_frequency_does_not_change_priority(self):
        engine, keys = sample_engine()
        engine.frequencies["ru"]["дом"] = 1.5
        engine.frequencies["he"]["בית"] = .01
        self.assertEqual(engine.decide(keys, "en").replacement, "дом")

    def test_unknown_frequencies_do_not_change_priority(self):
        engine, keys = sample_engine()
        del engine.frequencies["he"]["בית"]
        self.assertEqual(engine.decide(keys, "en").replacement, "дом")
        engine.frequencies["ru"].clear()
        self.assertEqual(engine.decide(keys, "en").replacement, "дом")

    def test_no_index_required_for_priority_or_unique_choice(self):
        engine, keys = sample_engine()
        engine.frequencies = {}
        self.assertEqual(engine.decide(keys, "en").replacement, "дом")
        engine.words["he"].clear()
        self.assertEqual(engine.decide(keys, "en").replacement, "дом")

    def test_conservative_confidence_still_required(self):
        engine, keys = sample_engine()
        engine.confident["ru"].clear()
        self.assertEqual(engine.decide(keys, "en").language, "he")
        engine.confident["he"].clear()
        self.assertIsNone(engine.decide(keys, "en"))

    def test_known_source_is_not_switched_by_frequency(self):
        engine, keys = sample_engine({"en": "ye", "ru": "ну", "he": "טה"})
        engine.words["en"].add("ye")
        engine.frequencies["ru"]["ну"] = 10000.0
        engine.frequencies["he"]["טה"] = 0.1
        self.assertIsNone(engine.decide(keys, "en"))

    def test_file_exclusions_and_personal_words_take_precedence(self):
        engine, keys = sample_engine()
        engine.exclusions.add("abc")
        self.assertIsNone(engine.decide(keys, "en"))
        engine.exclusions.clear()
        lexicon = UserLexicon()
        lexicon.add("words", "en", "abc")
        engine.user_lexicon = lexicon
        self.assertIsNone(engine.decide(keys, "en"))
        lexicon.remove("words", "en", "abc")
        lexicon.add("exclusions", "ru", "дом")
        self.assertIsNone(engine.decide(keys, "en"))

    def test_frequency_spelling_agrees_with_dictionary_for_yo(self):
        counts, total = frequency_table("ещё 3\nеще 5\nfoo 100\n".encode(), "ru")
        self.assertEqual(total, 108)
        self.assertEqual(counts, {"еще": 8})
        self.assertEqual(normalize_word("Е\u0308ЩЁ", "ru"), "еще")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ru.txt"
            path.write_text("ещё\nеще\n", encoding="utf-8")
            self.assertEqual(read_words(path, "ru"), {"еще"})
        self.assertEqual(resolve_tie([("ru", "ещё"), ("en", "also")],
                                     {"ru": {"еще": 120}, "en": {"also": 1}}),
                         ("ru", "ещё"))

    def test_early_uses_same_priority_only_for_complete_words(self):
        forms = {"en": "abcde", "ru": "слово", "he": "שלוםו"}
        engine, keys = sample_engine(forms)
        engine.frequencies = {"ru": {"слово": 120}, "he": {"שלוםו": 2}, "en": {}}
        prepare_early(engine)
        self.assertEqual(suggest_early(engine, keys, "en").language, "ru")
        engine.frequencies["he"].clear()
        self.assertEqual(suggest_early(engine, keys, "en").language, "ru")
        engine.frequencies["he"]["שלוםו"] = 2
        engine.words["he"] = {"שלוםות"}
        prepare_early(engine)
        self.assertIsNone(suggest_early(engine, keys, "en"))

    def test_corrupt_index_disables_all_frequency_ties(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            metadata = {"format": 1, "unit": "occurrences_per_million_full_corpus",
                        "languages": {}}
            for lang, word in (("ru", "дом"), ("en", "home"), ("he", "בית")):
                content = (word + "\t3.000000000000\n").encode()
                (base / f"frequency_{lang}.tsv").write_bytes(content)
                metadata["languages"][lang] = {
                    "index_sha256": hashlib.sha256(content).hexdigest(),
                    "full_corpus_total": 100, "indexed_words": 1}
            (base / "FREQUENCY-SOURCES.json").write_text(json.dumps(metadata))
            self.assertEqual(len(load_frequencies(base)), 3)
            (base / "frequency_ru.tsv").write_text("corrupted", encoding="utf-8")
            self.assertEqual(load_frequencies(base), {})
            (base / "frequency_ru.tsv").unlink()
            self.assertEqual(load_frequencies(base), {})

    def test_rejects_unpinned_frequency_source(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "ru_full.txt").write_bytes(b"word 999\n")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                verified_source("ru", Path(folder))


class ActualPreparedDictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("TYPEBRIDGE_DICTIONARY_TEST_DIR")
        if not path:
            raise unittest.SkipTest("Set TYPEBRIDGE_DICTIONARY_TEST_DIR to verified staged open data")
        cls.engine = LanguageEngine(Path(path))
        if hasattr(cls.engine, 'frequencies'):
            raise AssertionError('Runtime engine must not load numeric frequency tables')

    def keys(self, word):
        return [(ord(letter.upper()), letter.isupper()) for letter in word]

    def test_cure_from_russian_layout_uses_english_priority(self):
        decision = self.engine.decide_token(self.keys('cure'), 'ru')
        self.assertEqual((decision.typed, decision.replacement, decision.language), ('сгку', 'cure', 'en'))

    def test_all_six_correction_directions(self):
        for typed, source, target, expected in (
            ("hello", "ru", "en", "hello"),
            ("ghjdthrf", "en", "ru", "проверка"),
            ("akuo", "en", "he", "שלום"),
            ("ghjdthrf", "he", "ru", "проверка"),
            ("hello", "he", "en", "hello"),
            ("akuo", "ru", "he", "שלום"),
        ):
            with self.subTest(source=source, target=target):
                result = self.engine.decide(self.keys(typed), source)
                self.assertIsNotNone(result)
                self.assertEqual((result.language, result.replacement), (target, expected))

    def test_zvc_retains_confident_hebrew_over_rare_russian(self):
        keys = self.keys("zvc")
        engine = self.engine
        self.assertEqual(engine.mapper.word(keys, "ru"), "ямс")
        self.assertEqual(engine.mapper.word(keys, "he"), "זהב")
        self.assertTrue(engine._known("ru", "ямс"))
        self.assertTrue(engine._known("he", "זהב"))
        self.assertFalse(engine._confident("ru", "ямс"))
        self.assertTrue(engine._confident("he", "זהב"))
        result = engine.decide(keys, "en")
        self.assertEqual((result.language, result.replacement), ("he", "זהב"))

    def test_known_english_word_ye_remains_known(self):
        keys = self.keys("ye")
        self.assertTrue(self.engine._known("en", "ye"))
        self.assertIsNone(self.engine.decide(keys, "en"))


if __name__ == "__main__":
    unittest.main()
