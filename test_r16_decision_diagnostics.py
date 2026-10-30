"""Deterministic exact-policy diagnostics, with no live input or user data."""

from types import SimpleNamespace
from unittest import TestCase

from decision_diagnostics import explain_token, explain_early
from language_decision_v2 import PolicyDecisionEngine


class Mapper:
    def __init__(self, forms):
        self.forms = forms

    def word(self, keys, language):
        return self.forms.get(language, '')[:len(keys)]


class DecisionDiagnosticsTests(TestCase):
    def setUp(self):
        behavior = SimpleNamespace(source_nonletter_keys=frozenset(),
                                   ambiguous_destinations='reject_multiple',
                                   early_prefix_aliases=())
        self.packs = {
            'en': SimpleNamespace(normalization='casefold',
                                  description=SimpleNamespace(unicode_script='LATIN',
                                      alternatives=('ru', 'he')),
                                  behavior=behavior),
            'ru': SimpleNamespace(normalization='casefold',
                                  description=SimpleNamespace(unicode_script='CYRILLIC',
                                      alternatives=('en', 'he')),
                                  behavior=behavior),
            'he': SimpleNamespace(normalization='casefold',
                                  description=SimpleNamespace(unicode_script='HEBREW',
                                      alternatives=('en', 'ru')),
                                  behavior=behavior),
        }
        self.keys = [(65, False), (66, False), (67, False)]

    def make_engine(self, forms=None, words=None, confident=None,
                    exclusions=(), conservative=True):
        forms = forms or {'en': 'abc', 'ru': 'кот', 'he': 'שלום'}
        words = words if words is not None else {'en': set(), 'ru': {'кот'}, 'he': set()}
        confident = confident if confident is not None else {'en': set(), 'ru': {'кот'}, 'he': set()}
        return PolicyDecisionEngine(packs=self.packs, active=('en', 'ru', 'he'),
                                    mapper=Mapper(forms), words=words,
                                    confident=confident, exclusions=set(exclusions),
                                    conservative=conservative)

    def test_accepted_and_parity_without_trace(self):
        policy = self.make_engine()
        before = policy.decide_token(self.keys, 'en')
        diagnostic = policy.diagnose_token(self.keys, 'en')
        self.assertTrue(diagnostic.accepted)
        self.assertEqual((diagnostic.destination, diagnostic.replacement),
                         (before.language, before.replacement))
        self.assertEqual(before, policy.decide_token(self.keys, 'en'))

    def test_actual_rejection_branches(self):
        scenarios = [
            ('inactive-source-language', self.make_engine(), self.keys, 'fr'),
            ('invalid-key-length', self.make_engine(), [], 'en'),
            ('known-source-untrimmed-token', self.make_engine(words={
                'en': {'abc'}, 'ru': {'кот'}, 'he': set()}), self.keys, 'en'),
            ('excluded-untrimmed-token', self.make_engine(exclusions={'abc'}), self.keys, 'en'),
            ('known-other-language-untrimmed-token', self.make_engine(exclusions={'кот'}), self.keys, 'en'),
            ('known-other-language-untrimmed-token', self.make_engine(confident={
                'en': set(), 'ru': set(), 'he': set()}), self.keys, 'en'),
            ('no-matching-destination', self.make_engine(words={
                'en': set(), 'ru': set(), 'he': set()}), self.keys, 'en'),
            ('known-other-language-untrimmed-token', self.make_engine(words={
                'en': set(), 'ru': {'кот'}, 'he': {'שלו'}}), self.keys, 'en'),
        ]
        for expected, policy, keys, source in scenarios:
            with self.subTest(expected=expected):
                self.assertIsNone(policy.decide_token(keys, source))
                self.assertEqual(policy.diagnose_token(keys, source).reason, expected)

    def test_unicode_symbols_and_punctuation_fallback_reasons(self):
        # A non-letter physical symbol and a mixed-script Unicode source must
        # be rejected before candidate discovery, even when destination exists.
        for source, reason in (('ab!', 'no-matching-punctuation-destination'),
                               ('aбc', 'invalid-source-script-or-symbol')):
            with self.subTest(source=source):
                policy = self.make_engine(forms={'en': source, 'ru': 'кот', 'he': 'שלום'},
                                          words={'en': set(), 'ru': set(), 'he': set()})
                self.assertIsNone(policy.decide_token(self.keys, 'en'))
                self.assertEqual(policy.diagnose_token(self.keys, 'en').reason,
                                 reason)

        # Literal punctuation is eligible for stripping but no center matches.
        keys = [(65, False), (66, False), (67, False), (68, False)]
        policy = self.make_engine(forms={'en': '(abc', 'ru': 'котя', 'he': 'שלום'},
                                  words={'en': set(), 'ru': set(), 'he': set()})
        self.assertIsNone(policy.decide_token(keys, 'en'))
        self.assertEqual(policy.diagnose_token(keys, 'en').reason,
                         'no-matching-punctuation-destination')

        # The loop's center rejection is authoritative, not an old direct
        # candidate reason. The center is an already-known source word.
        policy = self.make_engine(forms={'en': '(abc', 'ru': 'котя', 'he': 'שלום'},
                                  words={'en': {'abc'}, 'ru': set(), 'he': set()})
        self.assertIsNone(policy.decide_token(keys, 'en'))
        self.assertEqual(policy.diagnose_token(keys, 'en').reason,
                         'known-source-punctuation-center')

    def test_mixed_policy_conflict_does_not_change_verdict(self):
        policy = self.make_engine(words={'en': set(), 'ru': {'кот'}, 'he': {'שלו'}})
        self.assertIsNone(policy.decide_token(self.keys, 'en'))
        self.assertEqual(policy.diagnose_token(self.keys, 'en').reason,
                         'known-other-language-untrimmed-token')

    def test_explicit_adapter_preserves_default_legacy_diagnostics(self):
        policy = self.make_engine()
        facade = SimpleNamespace(_policy_engine=policy)
        self.assertEqual(explain_token(facade, self.keys, 'en', exact_policy=True),
                         policy.diagnose_token(self.keys, 'en'))

    def test_exact_early_rejections_and_equivalence(self):
        policy = self.make_engine()
        policy.prepare_early()
        cases = [(self.keys, 'fr', 'inactive-source-language'),
                 (self.keys, 'en', 'prefix-below-confidence-threshold'),
                 ([(65+i, False) for i in range(5)], 'en',
                  'invalid-source-script-or-symbol'),
                 ([(65+i, True) for i in range(5)], 'en',
                  'invalid-physical-key-or-shift')]
        for keys, source, expected in cases:
            with self.subTest(reason=expected):
                before = policy.suggest_live_early(keys, source)
                explanation = policy.diagnose_early(keys, source)
                self.assertEqual(explanation.accepted, before is not None)
                self.assertEqual(explanation.reason, expected)
                self.assertEqual(policy.suggest_live_early(keys, source), before)
                facade = SimpleNamespace(_policy_engine=policy)
                self.assertEqual(explain_early(facade, keys, source,
                                               exact_policy=True), explanation)

