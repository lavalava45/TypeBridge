"""Frozen R16/4a characterization: no new loader, hook, HKL or user data."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from direct_hotkeys import DEFAULTS
from engine import (HEBREW_NONLETTER_VKS, SAMPLE_HEBREW,
                    ALTERNATIVE_PRIORITY, LanguageEngine, _read_lexical_data,
                    candidate_token_key)
from frequency_rank import normalize_word
from incremental_recognition import build_early_index
from language_registry import LANGUAGE_REGISTRY
from r16_4a_equivalence import (CORPUS, compare_adapters, legacy_decision,
                                legacy_engine_for_case, legacy_prefixes, load_corpus)
from user_lexicon import normalize as normalize_personal


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / 'fixtures' / 'r16-4a-contract'


class BaselinePolicyContractTests(unittest.TestCase):
    def test_frozen_metadata_exhausts_current_registry_and_policy(self):
        expected = {'ru', 'en', 'he'}
        self.assertEqual({description.code for description in LANGUAGE_REGISTRY}, expected)
        self.assertEqual({p.stem.removesuffix('.policy') for p in FIXTURES.glob('*.policy.json')},
                         expected)
        for language in LANGUAGE_REGISTRY:
            with self.subTest(language=language.code):
                snap = json.loads((FIXTURES / (language.code + '.policy.json')).read_text(
                    encoding='utf-8'))
                self.assertEqual(snap['kind'], 'R16_4A_BASELINE_POLICY_NOT_INSTALLABLE')
                self.assertEqual(snap['contract_revision'], 1)
                for field in ('code', 'name', 'langid', 'unicode_script', 'alphabet',
                              'dictionary_file', 'confident_file'):
                    self.assertEqual(snap[field], getattr(language, field), field)
                self.assertEqual(tuple(snap['behavior']['alternatives']),
                                 ALTERNATIVE_PRIORITY[language.code])
                self.assertEqual(snap['behavior']['ambiguous_destinations'],
                                 'ordered_priority')
                self.assertEqual(snap['historical_default_direct_hotkey'],
                                 DEFAULTS[language.code])
                self.assertEqual(snap['additional_sources'], [
                    {'filename': a.filename, 'for_words': a.for_words,
                     'for_confident': a.for_confident,
                     'normalization': {
                         'words': snap['normalization']['dictionary'],
                         'confident': 'casefold'}}
                    for a in language.additions])

    def test_legacy_supplements_and_special_hebrew_keys_are_pinned(self):
        overlays = {code: json.loads((FIXTURES / (code + '.policy.json')).read_text(
            encoding='utf-8')) for code in ('ru','en','he')}
        self.assertEqual(overlays['ru']['legacy_inline_supplements'],
                         {'words': ['а','и','поясни'],
                          'confident': ['а','и','поясни']})
        self.assertEqual(overlays['en']['legacy_inline_supplements'],
                         {'words': ['a','i'], 'confident': []})
        self.assertEqual(set(overlays['he']['legacy_inline_supplements']['words']),
                         SAMPLE_HEBREW)
        self.assertEqual(set(overlays['he']['legacy_inline_supplements']['confident']),
                         SAMPLE_HEBREW)
        self.assertEqual({entry['vk']: entry['character'] for entry in
                          overlays['he']['behavior']['source_nonletter_keys']},
                         HEBREW_NONLETTER_VKS)
        for other in ('ru','en'):
            self.assertEqual(overlays[other]['behavior']['source_nonletter_keys'], [])

    def test_dictionary_normalization_differs_from_personal_normalization(self):
        self.assertEqual(normalize_word('ЁЖИК', 'ru'), 'ежик')
        self.assertEqual(normalize_word('ЁЖИК', 'en'), 'ёжик')
        self.assertEqual(normalize_word('HELLO', 'en'), 'hello')
        self.assertEqual(normalize_word('שָׁלוֹם', 'he'), 'שלום')
        self.assertEqual(normalize_personal('ЁЖИК', 'ru'), 'ёжик')
        self.assertEqual(normalize_personal('ЁЖИК', 'ru', sensitive=True), 'ЁЖИК')
        with self.assertRaises(ValueError):
            normalize_personal('Éclair', 'en')  # Strict ASCII personal EN.
        self.assertEqual(normalize_personal('שלום', 'he'), 'שלום')
        ru = json.loads((FIXTURES / 'ru.policy.json').read_text(encoding='utf-8'))
        self.assertEqual(ru['normalization']['dictionary'],
                         'nfkc_casefold_strip_combining_yo_to_e')
        self.assertEqual(ru['normalization']['personal_input'], 'nfc')
        self.assertEqual(ru['behavior']['early_prefix_aliases'], ['yo_to_e'])

    def test_early_prefix_preserves_russian_yo_alias_without_changing_input(self):
        legacy = SimpleNamespace(
            words={'ru': {'ёлка'}, 'en': {'hello'}, 'he': {'שלום'}},
            active_languages=('ru','en','he'), user_lexicon=None)
        index = build_early_index(legacy)
        self.assertIn('ёлка', index['ru'])
        self.assertIn('елка', index['ru'])
        self.assertNotIn('елка', index['en'])

    def test_legacy_source_supplements_in_selected_language_only(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            data = Path(folder)
            for spec in LANGUAGE_REGISTRY:
                (data / spec.dictionary_file).write_text('дом\n' if spec.code == 'ru'
                    else ('hello\n' if spec.code == 'en' else 'שלום\n'), encoding='utf-8')
                (data / spec.confident_file).write_text('дом\n' if spec.code == 'ru'
                    else ('hello\n' if spec.code == 'en' else 'שלום\n'), encoding='utf-8')
            (data / 'he_custom.txt').write_text('מַלְךְ\n', encoding='utf-8')
            (data / 'exclude.txt').write_text('blacklist\n', encoding='utf-8')
            lexicon = _read_lexical_data(data)
            for word in ('а','и','поясни'):
                self.assertIn(word, lexicon['words']['ru'])
                self.assertIn(word, lexicon['confident']['ru'])
            self.assertIn('a', lexicon['words']['en'])
            self.assertNotIn('a', lexicon['confident']['en'])
            self.assertIn('מַלְךְ', lexicon['confident']['he'])
            self.assertIn('מלך', lexicon['words']['he'])
            self.assertNotIn('מַלְךְ', lexicon['words']['he'])
            self.assertEqual(lexicon['exclusions'], {'blacklist'})

    def test_schema_v2_accepts_example_but_current_loader_rejects_v2(self):
        from tempfile import TemporaryDirectory
        import shutil
        from language_pack_loader import load_language_pack
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest('jsonschema not installed; schema validation requires it')
        schema = json.loads((ROOT / 'schemas' / 'language-pack-v2.schema.json').read_text(
            encoding='utf-8'))
        Draft202012Validator.check_schema(schema)
        example = json.loads((FIXTURES / 'ar.manifest.v2.example.json').read_text(
            encoding='utf-8'))
        validator = Draft202012Validator(schema)
        self.assertEqual(list(validator.iter_errors(example)), [])
        for variation in ('bad_normalizer', 'executable_field', 'bad_policy'):
            mutated = deepcopy(example)
            if variation == 'bad_normalizer':
                mutated['normalization']['dictionary'] = 'python_eval'
            elif variation == 'executable_field':
                mutated['behavior']['script_path'] = 'run.py'
            else:
                mutated['behavior']['ambiguous_destinations'] = 'guess_frequency'
            self.assertTrue(list(validator.iter_errors(mutated)), variation)
        with TemporaryDirectory() as folder:
            target = Path(folder) / 'ar'
            shutil.copytree(ROOT / 'fixtures' / 'r16-demo-packs' / 'ar', target)
            (target / 'manifest.json').write_text(json.dumps(example), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'manifest|Unsupported language pack schema version'):
                load_language_pack(target)

    def test_ambiguity_policy_composition_matches_current_behavior(self):
        case = next(c for c in load_corpus() if c['id'] == 'ambiguous-en-priority-ru')
        engine = legacy_engine_for_case(case)
        matches = [('ru', 'привет'), ('he', 'עינגאמ')]
        self.assertEqual(engine.resolve_ambiguity(matches, 'en'),
                         ('ru', 'привет'))
        engine.active_languages = ('en', 'ru', 'ar')
        engine.language_catalog = {'en': object(), 'ru': object(), 'ar': object()}
        # Existing engine rejects multiple candidates whenever external AR
        # is active. Under future declarative policy, AR can request the
        # strictest reject_multiple mode without treating RU as privileged.
        self.assertIsNone(engine.resolve_ambiguity(
            [('ru', 'привет'), ('ar', 'مرحبا')], 'en'))
        example = json.loads((FIXTURES / 'ar.manifest.v2.example.json').read_text(
            encoding='utf-8'))
        self.assertEqual(example['behavior']['ambiguous_destinations'], 'reject_multiple')


class PinnedBehaviorOracleTests(unittest.TestCase):
    def test_complete_pinned_oracle_matches_current_legacy_engine(self):
        cases = load_corpus()
        self.assertGreaterEqual(len(cases), 18)
        self.assertEqual(len({case['id'] for case in cases}), len(cases))
        for case in cases:
            with self.subTest(case=case['id']):
                self.assertEqual(legacy_decision(case), case['expected'])

    def test_future_comparator_catches_missed_correction_and_changed_reason(self):
        cases = load_corpus()
        self.assertEqual(compare_adapters(cases, legacy_decision, legacy_decision), [])
        first = cases[0]['id']
        def broken_new(candidate):
            result = legacy_decision(candidate)
            if candidate['id'] == first:
                return None
            if result and candidate['id'] == 'ambiguous-en-priority-ru':
                return {**result, 'reason': 'unknown new policy'}
            return result
        changes = compare_adapters(cases, legacy_decision, broken_new)
        self.assertEqual({difference['id'] for difference in changes},
                         {first, 'ambiguous-en-priority-ru'})

    def test_all_pinned_prefixes_match_current_engine_before_space(self):
        document = json.loads((FIXTURES / 'prefix-oracle.json').read_text(encoding='utf-8'))
        self.assertEqual(document['kind'], 'R16_4A_PINNED_LEGACY_PREFIXES')
        cases = load_corpus()
        self.assertEqual({row['id'] for row in document['cases']},
                         {row['id'] for row in cases})
        indexed = {case['id']: case for case in cases}
        self.assertGreaterEqual(sum(len(row['expected_prefixes'])
                                    for row in document['cases']), 100)
        for row in document['cases']:
            with self.subTest(case=row['id']):
                self.assertEqual(legacy_prefixes(indexed[row['id']]),
                                 row['expected_prefixes'])

    def test_prefix_comparator_flags_changed_earliest_correction(self):
        cases = load_corpus()
        self.assertEqual(compare_adapters(cases, legacy_prefixes, legacy_prefixes), [])
        def broken_early(case):
            observed = deepcopy(legacy_prefixes(case))
            if case['id'] == 'en-to-ru':
                observed[4]['decision'] = None  # Missed pre-Space opportunity.
            return observed
        self.assertEqual([mismatch['id'] for mismatch in compare_adapters(
            cases, legacy_prefixes, broken_early)], ['en-to-ru'])


if __name__ == '__main__':
    unittest.main()
