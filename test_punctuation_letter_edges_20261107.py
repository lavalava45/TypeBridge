"""Whole-word vs literal-punctuation ambiguity; offline key map, no Win32 input."""
import json
from types import SimpleNamespace
import unittest

from engine import LanguageEngine, ALTERNATIVE_PRIORITY
from language_decision_v2 import PolicyDecisionEngine
from language_registry import LANGUAGE_BY_CODE
from experiments.layout_quality_20261106 import FrozenMapper, KEYMAP
from word_cycle import remember_word


class LetterEdgeTests(unittest.TestCase):
    def setUp(self):
        self.mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
        words = {'en': {'hello'}, 'ru': {'привет', 'белый'}, 'he': {'יולי', 'בית'}}
        behavior = SimpleNamespace(source_nonletter_keys=(),
            ambiguous_destinations='ordered_priority', early_prefix_aliases=())
        self.policy = PolicyDecisionEngine(
            packs={code: SimpleNamespace(normalization='casefold',
                description=LANGUAGE_BY_CODE[code], behavior=behavior) for code in words},
            active=('ru', 'en', 'he'), mapper=self.mapper,
            words=words, confident={code:set(bucket) for code,bucket in words.items()},
            exclusions=set())

    def keys(self, text):
        return self.mapper.keys_for(text, 'en')

    def test_unknown_trailing_letter_is_not_rewritten_as_punctuation(self):
        for text in ('hukh,', 'hukh,!', '(hukh,)'):
            with self.subTest(text=text):
                self.assertIsNone(self.policy.decide_token(self.keys(text), 'en'))
        self.assertEqual(self.policy.diagnose_token(self.keys('hukh,'), 'en').reason,
                         'ambiguous-letter-punctuation-edge')

    def test_leading_and_trailing_russian_letter_edges_are_also_ambiguous(self):
        for text in (',ghbdtn', 'ghbdtn,', 'ghbdtn.', '[ghbdtn],'):
            with self.subTest(text=text):
                self.assertIsNone(self.policy.decide_token(self.keys(text), 'en'))

    def test_known_whole_word_still_wins_with_letter_on_punctuation_key(self):
        self.policy.words['he'].add('יולית')
        self.policy.confident['he'].add('יולית')
        for text, expected in [('hukh,','יולית'), ('(hukh,)','(יולית)'), (',tksq','белый')]:
            with self.subTest(text=text):
                result = self.policy.decide_token(self.keys(text), 'en')
                self.assertEqual(result.replacement, expected)

    def test_ch_comma_is_one_complete_hebrew_word_at_space_boundary(self):
        # Real EN key sequence for HE בית (house). EN comma is a Hebrew
        # letter, not a literal punctuation boundary in this context.
        keys = self.keys('ch,')
        self.assertEqual(self.mapper.word(keys, 'he'), 'בית')
        result = self.policy.decide_token(keys, 'en')
        self.assertIsNotNone(result)
        self.assertEqual((result.replacement, result.language), ('בית', 'he'))

    def test_unambiguous_literal_punctuation_keeps_working(self):
        for text, expected in [('ghbdtn!', 'привет!'), ('(ghbdtn)', '(привет)'),
                               ('((ghbdtn))!!', '((привет))!!'),
                               ('[ghbdtn]', '[привет]'), ('"ghbdtn"', '"привет"'),
                               ('["ghbdtn"]', '["привет"]')]:
            with self.subTest(text=text):
                result = self.policy.decide_token(self.keys(text), 'en')
                self.assertEqual(result.replacement, expected)

    def test_manual_cycle_keeps_every_physical_key_and_spaces(self):
        word = remember_word(self.keys('hukh,'), 'hukh,', 'en', '  ', (), whole=True,
                             active_languages=('en', 'he', 'ru'))
        self.assertEqual(word.next_variant(self.mapper, ('en', 'he', 'ru')), ('he', 'יולית'))
        self.assertEqual(word.suffix, '  ')

    def test_legacy_and_unified_backends_share_the_edge_rule(self):
        legacy = LanguageEngine.__new__(LanguageEngine)
        legacy._policy_engine = None
        legacy.mapper = self.mapper
        legacy.active_languages = self.policy.active
        legacy.words, legacy.confident = self.policy.words, self.policy.confident
        legacy.conservative = True
        legacy.user_lexicon = None
        legacy.exclusions = set()
        legacy.language_catalog = None
        legacy.language_priority = ALTERNATIVE_PRIORITY
        for text in ('hukh,', ',ghbdtn', 'ghbdtn!', '(ghbdtn)', '[ghbdtn]', ',tksq'):
            with self.subTest(text=text):
                first = legacy.decide_token(self.keys(text), 'en')
                second = self.policy.decide_token(self.keys(text), 'en')
                self.assertEqual(first.replacement if first else None,
                                 second.replacement if second else None)


if __name__ == '__main__':
    unittest.main()
