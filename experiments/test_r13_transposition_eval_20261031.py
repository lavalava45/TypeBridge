"""Small deterministic checks for the offline lexicon evaluation method."""

import unittest

from r13_transposition_eval_20261031 import evaluate, swapped_tokens


class OfflineEvaluationTests(unittest.TestCase):
    def test_swaps_are_distinct_and_adjacent_only(self):
        self.assertEqual(swapped_tokens("алла"), {"лала", "алал"})

    def test_known_words_and_ambiguity_are_counted_as_suppressed(self):
        # Both candidates are valid, so the typo must not be suggested.
        words = {"абвг", "бавг", "авбг", "бваг"}
        outcome = evaluate(words, {"абвг"}, limit=1)["counts"]
        self.assertEqual(outcome["sampled_originals"], 1)
        self.assertGreater(outcome["swaps_already_words"], 0)
        self.assertEqual(outcome["single_wrong"], 0)


if __name__ == "__main__":
    unittest.main()
