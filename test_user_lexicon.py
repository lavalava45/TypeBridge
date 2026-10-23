from pathlib import Path
import tempfile
import threading
import unittest

from user_lexicon import UserLexicon, normalize


class UserLexiconTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()

    def test_roundtrip_all_languages_and_modes(self):
        lex = UserLexicon()
        for lang, word in (("ru", "Привет"), ("en", "Hello"), ("he", "שלום")):
            self.assertTrue(lex.add("words", lang, word))
            self.assertFalse(lex.add("words", lang, word.lower()))
            self.assertTrue(lex.add("exclusions", lang, word, sensitive=True))
            self.assertTrue(lex.contains("words", lang, word.upper() if lang != "he" else word))
        lex.save(self.folder)
        loaded = UserLexicon.load(self.folder)
        self.assertTrue(loaded.contains("exclusions", "en", "Hello"))
        self.assertFalse(loaded.contains("exclusions", "en", "hello"))
        self.assertTrue(loaded.remove("words", "ru", "ПРИВЕТ"))
        self.assertFalse(loaded.remove("words", "ru", "ПРИВЕТ"))
        words, exclusions = loaded.engine_view()
        self.assertIn("hello", words["en"])
        self.assertNotIn("hello", exclusions)

    def test_nfc_and_invalid_inputs(self):
        self.assertEqual(normalize("Е\u0308ж", "ru"), "ёж")
        for lang, word in (("en", "../escape"), ("en", "hello world"),
                           ("ru", "abc"), ("he", "א1"), ("ru", ""),
                           ("en", "a" * 81), ("en", "h\u200dello")):
            with self.subTest(lang=lang, word=word), self.assertRaises(ValueError):
                normalize(word, lang)
        self.assertEqual(normalize("HELLO", "en"), "hello")

    def test_corrupt_and_unknown_schema_fail_closed(self):
        target = self.folder / "user_lexicon.json"
        for payload in (b"{", b"\xff", b"{}", b'{"version":2,"entries":{}}',
                        b'{"version":1,"entries":[]}'):
            target.write_bytes(payload)
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                UserLexicon.load(self.folder)

    def test_directory_and_symlink_refusal(self):
        with self.assertRaises(ValueError):
            UserLexicon().save(self.folder / "..")
        with self.assertRaises(ValueError):
            UserLexicon().save(Path("relative"))
        other = self.folder / "other.json"
        other.write_text("untouched")
        link = self.folder / "user_lexicon.json"
        try:
            link.symlink_to(other)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable in this environment")
        with self.assertRaises(ValueError):
            UserLexicon().save(self.folder)
        with self.assertRaises(ValueError):
            UserLexicon.load(self.folder)
        self.assertEqual(other.read_text(), "untouched")

    def test_rejects_symlink_parent_without_touching_destination(self):
        real = self.folder / "real"
        real.mkdir()
        link = self.folder / "alias"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlink unavailable")
        with self.assertRaises(ValueError):
            UserLexicon().save(link)
        self.assertFalse((real / "user_lexicon.json").exists())

    def test_truncated_snapshot_and_duplicate_rejected_without_mutation(self):
        lex = UserLexicon()
        lex.add("words", "en", "Hello")
        lex.save(self.folder)
        target = self.folder / "user_lexicon.json"
        original = target.read_bytes()
        doc = __import__("json").loads(original)
        doc["entries"]["words"]["en"]["insensitive"].append("hello")
        target.write_text(__import__("json").dumps(doc), encoding="utf-8")
        with self.assertRaises(ValueError):
            UserLexicon.load(self.folder)
        self.assertEqual(__import__("json").loads(target.read_text()), doc)
        target.write_bytes(original[:10])
        with self.assertRaises(ValueError):
            UserLexicon.load(self.folder)

    def test_threaded_atomic_reads_never_observe_torn_json(self):
        errors = []
        def writer(prefix):
            try:
                for i in range(40):
                    lex = UserLexicon()
                    lex.add("words", "en", f"{prefix}{chr(97 + i % 26)}")
                    lex.save(self.folder)
            except Exception as exc:
                errors.append(exc)
        def reader():
            try:
                for _ in range(80):
                    UserLexicon.load(self.folder)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=writer, args=(letter,)) for letter in ("a", "b")]
        threads += [threading.Thread(target=reader) for _ in range(2)]
        for th in threads: th.start()
        for th in threads: th.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
