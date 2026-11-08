"""Offline context state and candidate invariants; no actual UI or input."""
import json
from types import SimpleNamespace
import unittest

from language_registry import LANGUAGE_BY_CODE
from language_decision_v2 import PolicyDecisionEngine
from r06_context_20261107 import ContextWindow, EVENTS, FIXTURE, propose, feed
from layout_quality_20261106 import FrozenMapper, KEYMAP


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
        words = {'en': {'tot','the','little','hello'},
                 'ru': {'нужно','сделать','еще','ток'},
                 'he': {'אני','קונה','מחר'}}
        behavior = SimpleNamespace(source_nonletter_keys=(),
            ambiguous_destinations='ordered_priority',early_prefix_aliases=())
        self.policy = PolicyDecisionEngine(packs={code:SimpleNamespace(
            description=LANGUAGE_BY_CODE[code],normalization='casefold',behavior=behavior)
            for code in words},active=('ru','en','he'),mapper=self.mapper,
            words=words,confident={code:set(values) for code,values in words.items()},exclusions=set())
        self.scope = ('A','field-A')

    def make_history(self, words):
        history = ContextWindow(self.policy)
        now = feed(history, words, 'en')
        return history, now

    def answer(self, text, words, override=False):
        history, now = self.make_history(words)
        keys = self.mapper.keys_for(text,'en')
        baseline = self.policy.decide_token(keys,'en')
        return propose(self.policy,keys,'en',baseline,history,scope=self.scope,now=now,
                       override_known=override)

    def test_two_votes_resolve_destination_without_phrase_specific_rules(self):
        result, _ = self.answer('njr',['אני','קונה'])
        self.assertEqual((result.language,result.replacement),('he','מחר'))
        result, _ = self.answer('njr',['нужно','сделать'])
        self.assertEqual((result.language,result.replacement),('ru','ток'))

    def test_known_source_requires_explicit_unsafe_variant(self):
        result, reason = self.answer('tot',['нужно','сделать'])
        self.assertIsNone(result)
        self.assertEqual(reason,'known-source-kept')
        result, _ = self.answer('tot',['нужно','сделать'],override=True)
        self.assertEqual((result.language,result.replacement),('ru','еще'))

    def test_empty_one_unknown_and_mixed_context_fall_back(self):
        for words in ([], ['אני'], ['אני','hello'], ['אני','קונה','xqzvbn'],
                      ['אני','קונה','a']):
            with self.subTest(words=words):
                result, reason = self.answer('njr',words)
                self.assertEqual(result.language,'ru')
                self.assertEqual(reason,'no-context-hint')

    def test_each_reset_event_erases_hint_and_requires_two_new_words(self):
        for event in EVENTS:
            with self.subTest(event=event):
                history, now = self.make_history(['אני','קונה'])
                self.assertEqual(history.hint(self.scope,'en',now),'he')
                history.reset(event)
                self.assertIsNone(history.hint(self.scope,'en',now))
                history.observe('אני',self.scope,'en',now+1)
                self.assertIsNone(history.hint(self.scope,'en',now+1))
                history.observe('קונה',self.scope,'en',now+2)
                self.assertEqual(history.hint(self.scope,'en',now+2),'he')

    def test_unannounced_scope_or_layout_change_also_erases_hint(self):
        for scope, layout in [(('B','field-A'),'en'),(('A','field-B'),'en'),
                              (self.scope,'ru')]:
            with self.subTest(scope=scope,layout=layout):
                history, now = self.make_history(['אני','קונה'])
                self.assertIsNone(history.hint(scope,layout,now))
                self.assertIsNone(history.hint(self.scope,'en',now))  # No A→B→A resurrection.

    def test_age_and_clock_reversal_cannot_reuse_old_hint(self):
        for now in (99,110.001):
            history, _ = self.make_history(['אני','קונה'])
            self.assertIsNone(history.hint(self.scope,'en',now))

    def test_exclusion_and_confidence_are_not_bypassed(self):
        self.policy.exclusions.add('מחר')
        result, reason = self.answer('njr',['אני','קונה'])
        self.assertIsNone(result)  # The base policy also refuses this exclusion.
        self.assertEqual(reason,'excluded-candidate')
        self.policy.exclusions.clear()
        self.policy.confident['he'].remove('מחר')
        result, reason = self.answer('njr',['אני','קונה'])
        self.assertEqual(result.language,'ru')
        self.assertEqual(reason,'hint-has-no-eligible-candidate')

    def test_curated_fixture_is_fixed_and_keyboard_representable(self):
        data=json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.assertEqual(len({row['id'] for row in data['cases']}),len(data['cases']))
        for row in data['cases']:
            self.assertIsNotNone(self.mapper.keys_for(row['physical'],'en'))
        self.assertEqual(set(data['reset_events']),set(EVENTS))


if __name__ == '__main__':
    unittest.main()
