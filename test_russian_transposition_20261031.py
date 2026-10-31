"""Isolated R13 candidate selection tests; no real keyboard interactions."""

import unittest

from russian_transposition_20261031 import suggest_adjacent_transposition as suggest


class RussianTranspositionResearchTests(unittest.TestCase):
    WORDS = frozenset({"привет", "кто", "кот", "мир", "рим", "ток", "крот"})

    def test_explicit_opt_in_only(self):
        self.assertEqual(suggest("првиет", self.WORDS), ())
        self.assertEqual(suggest("првиет", self.WORDS, mode="auto",
                                 russian_confirmed=True, field_kind="plain_text"), ())
        with self.assertRaises(ValueError):
            suggest("првиет", self.WORDS, mode="unknown")

    def test_simple_single_swap_can_be_suggested(self):
        self.assertEqual(suggest("првиет", self.WORDS, mode="suggest",
                                 russian_confirmed=True, field_kind="plain_text"),
                         ("привет",))

    def test_existing_words_are_not_changed(self):
        for word in ("кто", "кот", "мир", "рим"):
            with self.subTest(word=word):
                self.assertEqual(suggest(word, self.WORDS, mode="suggest",
                                         russian_confirmed=True, field_kind="plain_text"), ())

    def test_ambiguous_swaps_are_suppressed(self):
        # "окт" swaps to both "кот" and "отк" if both are valid words.
        self.assertEqual(suggest("окт", {"кот", "отк"}, mode="suggest",
                                 russian_confirmed=True, field_kind="plain_text"), ())

    def test_sensitive_or_unverified_contexts_are_excluded(self):
        for kind in ("unknown", "password", "url", "code", "path", "email", "search"):
            with self.subTest(kind=kind):
                self.assertEqual(suggest("првиет", self.WORDS, mode="suggest",
                                         russian_confirmed=True, field_kind=kind), ())
        self.assertEqual(suggest("првиет", self.WORDS, mode="suggest",
                                 field_kind="plain_text"), ())

    def test_ineligible_tokens_and_abbreviations(self):
        for token in ("", "а", "аб", "ПРВИЕТ", "Првиет", "прВиет", "првиет.ru",
                      "https://првиет", "C:\\првиет", "првиет_1", "првиет()",
                      "првиет@example.org", "првиет!", "првиет привет", "првиет42",
                      "првиет\n", "првиет" * 20):
            with self.subTest(token=token):
                self.assertEqual(suggest(token, self.WORDS, mode="suggest",
                                         russian_confirmed=True, field_kind="plain_text"), ())

    def test_source_and_candidate_exclusions(self):
        for excluded in ({"првиет"}, {"привет"}):
            self.assertEqual(suggest("првиет", self.WORDS, mode="suggest",
                                     russian_confirmed=True, field_kind="plain_text",
                                     exclusions=excluded), ())
            self.assertEqual(suggest("првиет", self.WORDS, mode="suggest",
                                     russian_confirmed=True, field_kind="plain_text",
                                     protected_words=excluded), ())

    def test_not_layout_conversion_or_other_edit_distance(self):
        for token in ("ghbdtn", "приветт", "првеит", "пивет"):
            with self.subTest(token=token):
                self.assertEqual(suggest(token, self.WORDS, mode="suggest",
                                         russian_confirmed=True, field_kind="plain_text"), ())


if __name__ == "__main__":
    unittest.main()
