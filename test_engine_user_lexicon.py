"""Optional user lexicon integration preserving exact-case semantics."""
from pathlib import Path
import tempfile
import unittest

from engine import LanguageEngine, source_data_dir
from user_lexicon import UserLexicon


class EngineLexiconTests(unittest.TestCase):
    def setUp(self):
        self.base = LanguageEngine(source_data_dir())
        self.keys = [(ord(ch), False) for ch in "HELLO"]
        self.assertEqual(self.base.decide(self.keys, "ru").replacement, "hello")

    def test_case_sensitive_exclusions_and_load(self):
        lex = UserLexicon()
        lex.add("exclusions", "en", "Hello", sensitive=True)
        engine = LanguageEngine(source_data_dir(), user_lexicon=lex)
        self.assertEqual(engine.decide(self.keys, "ru").replacement, "hello")
        upper = [(ord(ch), True) for ch in "HELLO"]
        # The mapped target has uppercase letters, not the exact exception.
        self.assertIsNotNone(engine.decide(upper, "ru"))
        with tempfile.TemporaryDirectory() as tmp:
            lex.save(Path(tmp).resolve())
            loaded = LanguageEngine(source_data_dir(), user_lexicon_dir=Path(tmp).resolve())
            self.assertEqual(loaded.decide(self.keys, "ru").replacement, "hello")

    def test_case_sensitive_user_words_and_insensitive_exclusions(self):
        lex = UserLexicon()
        lex.add("words", "ru", "Руддщ", sensitive=True)
        engine = LanguageEngine(source_data_dir(), user_lexicon=lex)
        self.assertIsNotNone(engine.decide(self.keys, "ru"))
        upper = [(ord(ch), True) for ch in "HELLO"]
        self.assertIsNotNone(engine.decide(upper, "ru"))
        lex.add("exclusions", "en", "HELLO", sensitive=False)
        self.assertIsNone(engine.decide(self.keys, "ru"))

    def test_invalid_optional_arguments(self):
        with self.assertRaises(ValueError):
            LanguageEngine(source_data_dir(), user_lexicon=object())
        with self.assertRaises(ValueError):
            LanguageEngine(source_data_dir(), user_lexicon=UserLexicon(),
                           user_lexicon_dir=Path.cwd())

    def test_nfc_normalization_and_exact_case_with_engine_api(self):
        lex = UserLexicon()
        lex.add("words", "ru", "Ёж", sensitive=True)
        lex.add("exclusions", "en", "Hello", sensitive=True)
        engine = LanguageEngine(source_data_dir(), user_lexicon=lex)
        self.assertTrue(engine._known("ru", "Е\u0308ж"))
        self.assertFalse(lex.contains("words", "ru", "ёж"))
        self.assertTrue(engine._excluded("en", "Hello"))
        self.assertFalse(engine._excluded("en", "hello"))


if __name__ == "__main__":
    unittest.main()
