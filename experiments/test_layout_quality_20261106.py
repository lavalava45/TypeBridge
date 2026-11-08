"""Offline scoring and corpus invariants; no dictionaries or Windows calls."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from layout_quality_20261106 import FrozenMapper, KEYMAP, build_rows, classify, evaluate, compare


class QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))

    def test_captured_oem_and_case_roundtrip_and_hebrew_direction(self):
        for lang, word in [('ru','яблоко'), ('ru','Ёлка'), ('en','Hello,'),
                           ('he','שלום'), ('he','צה"ל')]:
            keys = self.mapper.keys_for(word, lang)
            self.assertIsNotNone(keys)
            self.assertEqual(self.mapper.word(keys, lang), word)
        self.assertEqual(self.mapper.word(self.mapper.keys_for('z,kj', 'en'), 'ru'), 'ябло')
        self.assertEqual(self.mapper.word(self.mapper.keys_for('גיאהז', 'he'), 'ru'), 'время')

    def test_score_checks_exact_text_and_language_not_only_destination(self):
        self.assertEqual(classify('abc','привет','en','ru','привет','ru'), 'correct')
        self.assertEqual(classify('abc','привт','en','ru','привет','ru'), 'wrong_replacement')
        self.assertEqual(classify('abc','abc','en','en','привет','ru'), 'miss')
        self.assertEqual(classify('hello','руддщ','en','ru','hello','en'), 'false_switch')
        self.assertEqual(classify('abc','привет','en','he','привет','ru'), 'wrong_replacement')

    def test_frozen_rows_keep_training_ids_out_and_do_not_drop_collisions(self):
        snapshot = {'kind':'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT',
                    'languages': {lang:{'titles':[]} for lang in ('ru','en','he')}}
        snapshot['languages']['en']['titles'] = [
            {'pageid':3,'title':'hello','source_url':'fixture'},
            {'pageid':4,'title':'world','source_url':'fixture'},
            {'pageid':4,'title':'WORLD','source_url':'fixture'}]
        first, _ = build_rows(snapshot, self.mapper)
        second, _ = build_rows(snapshot, self.mapper)
        self.assertEqual(first, second)
        titles = [row for row in first if row['kind']=='reused_title']
        self.assertEqual(len(titles), 3)
        self.assertEqual({row['origin']['pageid'] for row in titles}, {4})
        self.assertEqual(len({row['id'] for row in first}), len(first))

    def test_evaluator_keeps_both_mode_scores_and_counts_bad_text(self):
        policy = SimpleNamespace(known=lambda *args: False, certain=lambda *args: False)
        engine = SimpleNamespace(mapper=self.mapper, _policy_engine=policy,
            decide_token=lambda keys, source: SimpleNamespace(
                replacement='привт', language='ru', reason='fixture'))
        rows = [dict(id='example',kind='fixture',source='en',target='ru',
                    expected='привет',keys=self.mapper.keys_for('ghbdtn','en'),origin=None)]
        records, groups, timings = evaluate(rows, engine)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(row['verdict']=='wrong_replacement' for row in records))
        self.assertEqual(sum(row['wrong_replacement'] for row in groups), 2)
        self.assertEqual(set(timings), {'conservative','extended'})

    def test_comparison_reports_regressions_even_with_more_improvements(self):
        previous = [dict(mode='conservative', id=str(i), actual='old',
                         verdict='correct' if i==0 else 'miss') for i in range(3)]
        current = [dict(mode='conservative', id=str(i), actual='new',
                       verdict='false_switch' if i==0 else 'correct') for i in range(3)]
        result = compare(dict(corpus_sha256='fixed',outcomes=previous),
                         dict(corpus_sha256='fixed',records=current))
        self.assertFalse(result['no_new_regressions'])
        self.assertEqual(len(result['regressions']), 1)
        self.assertEqual(len(result['improvements']), 2)
        with self.assertRaises(ValueError):
            compare(dict(corpus_sha256='a'), dict(corpus_sha256='b'))


if __name__ == '__main__':
    unittest.main()
