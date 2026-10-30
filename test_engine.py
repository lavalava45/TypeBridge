import unittest
import os
import subprocess
import sys
from pathlib import Path

from engine import LanguageEngine, source_data_dir
from typebridge_app import correction_inputs


class LanguagePrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = LanguageEngine(source_data_dir())

    @staticmethod
    def keys(text):
        return [(ord(char.upper()), char.isupper()) for char in text]

    def test_english_typed_under_russian_layout(self):
        decision = self.engine.decide(self.keys("hello"), "ru")
        self.assertEqual((decision.typed, decision.replacement, decision.language),
                         ("руддщ", "hello", "en"))

    def test_hebrew_typed_under_english_layout(self):
        decision = self.engine.decide(self.keys("akuo"), "en")
        self.assertEqual((decision.typed, decision.replacement, decision.language),
                         ("akuo", "שלום", "he"))

    def test_correct_word_stays_unchanged(self):
        self.assertIsNone(self.engine.decide(self.keys("hello"), "en"))

    def test_cas_and_hebrew_rkh_use_priority_only_for_unknown_source(self):
        # Both alternatives are real words. The current policy selects the
        # configured destination priority, but never overwrites a known source.
        keys = [(ord(char), False) for char in "RFC"]
        self.assertEqual(self.engine.mapper.word(keys, "ru"), "кас")
        self.assertEqual(self.engine.mapper.word(keys, "he"), "רכב")
        from unittest.mock import patch
        with patch.dict(self.engine.words, {
            "en": self.engine.words["en"] - {"rfc"},
            "ru": self.engine.words["ru"] | {"кас"},
            "he": self.engine.words["he"] | {"רכב"},
        }), patch.dict(self.engine.confident, {
            "ru": self.engine.confident["ru"] | {"кас"},
            "he": self.engine.confident["he"] | {"רכב"},
        }):
            result = self.engine.decide(keys, "en")
            self.assertIsNotNone(result)
            self.assertEqual((result.language, result.replacement, result.reason),
                             ("ru", "кас", "configured language priority"))
            self.assertIsNone(self.engine.decide(keys, "ru"))
            self.assertIsNone(self.engine.decide(keys, "he"))

    def test_hebrew_standard_real_fail_case_remains_unique(self):
        keys = [(ord(char), False) for char in "GHJDTHRF"]
        self.assertEqual(self.engine.mapper.word(keys, "he"), "עיחגאירכ")
        result = self.engine.decide(keys, "he")
        self.assertIsNotNone(result)
        self.assertEqual((result.typed, result.replacement, result.language),
                         ("עיחגאירכ", "проверка", "ru"))

    def test_unique_english_one_letter_word_is_corrected_at_boundary(self):
        result = self.engine.decide(self.keys("a"), "ru")
        self.assertEqual((result.language, result.replacement), ('en', 'a'))

    def test_curated_russian_imperative_without_relaxing_confidence(self):
        result = self.engine.decide_token(self.keys('gjzcyb'), 'en')
        self.assertEqual((result.language, result.replacement), ('ru', 'поясни'))

    def test_unicode_replacement_emits_backspaces_letters_and_space(self):
        events = correction_inputs(4, "שלום")
        self.assertEqual(len(events), (4 + 4 + 1) * 2)
        self.assertEqual(events[0].u.ki.wVk, 8)
        self.assertEqual(events[-2].u.ki.wVk, 32)

    def test_conservative_priority_still_requires_high_confidence(self):
        engine = self.engine
        keys = self.keys("hello")
        self.assertEqual(engine.decide(keys, "ru").replacement, "hello")
        # A competing Hebrew dictionary entry no longer vetoes the preferred
        # English candidate, but disabling EN confidence must still veto it.
        from unittest.mock import patch
        with patch.dict(engine.words, {
            "he": engine.words["he"] | {engine.mapper.word(keys, "he").casefold()},
        }):
            result = engine.decide(keys, "ru")
            self.assertIsNotNone(result)
            self.assertEqual((result.language, result.replacement, result.reason),
                             ("en", "hello", "configured language priority"))
            with patch.dict(engine.confident, {
                "en": engine.confident["en"] - {"hello"},
            }):
                self.assertIsNone(engine.decide(keys, "ru"))

    def test_self_test_handles_legacy_windows_console_encoding(self):
        env = os.environ | {"PYTHONIOENCODING": "cp1251"}
        result = subprocess.run(
            [sys.executable, "typebridge_app.py", "--self-test"],
            cwd=Path(__file__).parent, env=env,
            # On a cold start the complete RU/EN/HE dictionaries (>1.8M words)
            # and frozen-spawn-compatible caret helper can exceed 15 seconds.
            # Assert the actual self-test result, not an unrealistically short
            # local-disk performance deadline (measured 36.7 s on this host).
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
