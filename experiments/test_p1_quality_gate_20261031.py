"""Isolation, split and scoring invariants for independent P1 gate."""
import unittest

from p1_quality_gate_20261031 import (aggregate, bucket, evaluate,
                                      holdout_rows, summarize)


class DummyPolicy:
    packs_as_catalog = None
    words = {'ru': set(), 'en': set(), 'he': set()}
    confident = {'ru': set(), 'en': set(), 'he': set()}

    def normalize(self, language, word):
        return word

    def decide_token(self, keys, current):
        return None

    def known(self, lang, word):
        return False

    def excluded(self, lang, word):
        return False


class GateTests(unittest.TestCase):
    def test_holdout_disjoint_from_training_and_deduplicated(self):
        fixture = {'kind': 'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT',
                   'languages': {lang: {'titles': []}
                                 for lang in ('ru', 'en', 'he')}}
        fixture['languages']['en']['titles'] = [
            {'pageid': 3, 'title': 'alpha'},
            {'pageid': 4, 'title': 'bravo'},
            {'pageid': 4, 'title': 'bravo'},
            {'pageid': 5, 'title': 'charlie'}]
        tables = {'en': {letter: letter for letter in 'abcdefghijklmnopqrstuvwxyz'},
                  'ru': {}, 'he': {}}
        import p1_quality_gate_20261031 as module
        previous = module.script_ok
        try:
            module.script_ok = lambda word, lang, catalog: True
            rows = holdout_rows(fixture, DummyPolicy(), tables,
                                train_page_ids={('en', 5)})
        finally:
            module.script_ok = previous
        self.assertEqual(3, len(rows))
        self.assertEqual({4}, {row['pageid'] for row in rows})
        self.assertEqual(2, sum(row['expected'] is not None for row in rows))

    def test_stratified_false_switch_and_miss(self):
        class Policy:
            def decide_token(self, keys, current):
                return type('Answer', (), {'language': 'ru'})() if current == 'en' else None
        rows = [{'kind': 'heldout_positive', 'source': 'en',
                 'target': 'ru', 'expected': 'ru', 'physical': 'abcd'},
                {'kind': 'heldout_negative', 'source': 'en',
                 'target': 'en', 'expected': None, 'physical': 'abcd'},
                {'kind': 'heldout_positive', 'source': 'he',
                 'target': 'ru', 'expected': 'ru', 'physical': 'abcd'}]
        groups, latency = evaluate(rows, Policy())
        self.assertEqual(3, latency['n'])
        self.assertEqual(1, aggregate(groups, lambda x: True)['miss'])
        self.assertEqual(1, aggregate(groups, lambda x: True)['false_switch'])
        self.assertEqual('2-4', bucket(4))

    def test_empty_latency(self):
        self.assertEqual({'n': 0}, summarize([]))

    def test_in_vocabulary_split_and_collision_exclusion(self):
        fixture = {'kind': 'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT',
                   'languages': {lang: {'titles': []} for lang in ('ru', 'en', 'he')}}
        fixture['languages']['en']['titles'] = [
            {'pageid': 4, 'title': 'alpha'}, {'pageid': 5, 'title': 'bravo'}]
        tables = {lang: {letter: letter for letter in 'abcdefghijklmnopqrstuvwxyz'}
                  for lang in ('ru', 'en', 'he')}
        class Policy(DummyPolicy):
            words = {'en': {'alpha'}, 'ru': set(), 'he': set()}
            confident = {'en': {'alpha'}, 'ru': set(), 'he': set()}
            def known(self, lang, word):
                return word in self.words[lang]
            def excluded(self, lang, word):
                return False
        import p1_quality_gate_20261031 as module
        previous = module.script_ok
        try:
            module.script_ok = lambda word, lang, catalog: True
            rows = holdout_rows(fixture, Policy(), tables)
        finally:
            module.script_ok = previous
        self.assertEqual(2, sum(r['kind'] == 'heldout_iv_confident_positive' for r in rows))
        self.assertEqual(2, sum(r['kind'] == 'heldout_oov_positive' for r in rows))
        self.assertEqual(2, sum(r['kind'] == 'heldout_negative' for r in rows))


if __name__ == '__main__':
    unittest.main()
