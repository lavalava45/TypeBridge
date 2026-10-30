"""R16/4b/1: real independent v2 parser and lexicon vs legacy snapshots.

Only disposable TemporaryDirectory packages; no production user data/HKL.
"""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine import _read_lexical_data
from language_decision_v2 import PolicyDecisionEngine
from language_lexicon_v2 import build_v2_lexicon, load_selected_v2_lexicon
from language_pack_v2 import load_v2_pack, load_v2_selection, normalize_v2
from language_registry import LANGUAGE_REGISTRY
from r16_4a_equivalence import (SyntheticMapper, compare_adapters,
                                legacy_decision, legacy_prefixes, load_corpus)
from test_r16_lexicon_cache import LEGACY


ROOT = Path(__file__).resolve().parent
CONTRACT = ROOT / 'fixtures' / 'r16-4a-contract'


def make_v2_fixture(root, data_dir, code):
    """Export old data as a test-only v2 package; no production packaging."""
    from language_registry import LANGUAGE_BY_CODE
    description = LANGUAGE_BY_CODE[code]
    contract = json.loads((CONTRACT / f'{code}.policy.json').read_text(encoding='utf-8'))
    directory = root / code
    directory.mkdir()
    copied = {}
    for name in (description.dictionary_file, description.confident_file):
        copied[name] = (data_dir / name).read_bytes()
    additions = []
    for record in contract['additional_sources']:
        copied[record['filename']] = (data_dir / record['filename']).read_bytes()
        additions.append(dict(record))
    supplement = contract['legacy_inline_supplements']
    if supplement['words'] or supplement['confident']:
        # Old inline supplements are reproducibly represented as data files.
        # Every member in current RU and HE belongs to both roles; EN words
        # are words-only. Requiring identical sets when both roles are used
        # prevents accidentally changing per-role semantics during export.
        if supplement['confident'] and supplement['words'] != supplement['confident']:
            raise ValueError('Fixture exporter cannot merge non-identical supplements')
        copied['legacy_supplement.txt'] = ('\n'.join(supplement['words']) + '\n').encode('utf-8')
        additions.append({
            'filename': 'legacy_supplement.txt', 'for_words': True,
            'for_confident': bool(supplement['confident']),
            'normalization': {'words': contract['normalization']['dictionary'],
                              'confident': 'casefold'},
        })
    copied['LICENSE.txt'] = b'R16 isolated synthetic test data - no redistribution\n'
    manifest = {
        'schema_version': 2, 'package_version': '0.0.1',
        'code': code, 'name': description.name, 'langid': description.langid,
        'unicode_script': description.unicode_script,
        'alphabet': description.alphabet,
        'normalization': {'dictionary': contract['normalization']['dictionary']},
        'dictionary_file': description.dictionary_file,
        'confident_file': description.confident_file,
        'additions': additions,
        'alternatives': list(description.alternatives),
        'behavior': {key: value for key, value in contract['behavior'].items()
                     if key != 'alternatives'},
        'source': {'name': 'Synthetic unit-test inputs only', 'url': '',
                   'license_id': 'TEST-ONLY', 'license_file': 'LICENSE.txt'},
        'files': {name: {'bytes': len(raw),
                         'sha256': hashlib.sha256(raw).hexdigest()}
                  for name, raw in copied.items()},
    }
    for name, raw in copied.items():
        (directory / name).write_bytes(raw)
    (directory / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return directory


class ParallelV2Tests(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.data = base / 'legacy'
        self.root = base / 'packs'
        self.data.mkdir()
        self.root.mkdir()
        for name, body in LEGACY.items():
            (self.data / name).write_text(body, encoding='utf-8')
        self.exclusions_file = self.data / 'exclude.txt'
        for description in LANGUAGE_REGISTRY:
            make_v2_fixture(self.root, self.data, description.code)

    def test_three_legacy_languages_equal_independent_v2_lexicon(self):
        legacy = _read_lexical_data(self.data)
        new = load_selected_v2_lexicon(self.root, ('ru', 'en', 'he'),
                                       exclusions_file=self.exclusions_file)
        self.assertEqual(legacy, new)
        self.assertIn('еще', new['words']['ru'])
        self.assertIn('מלך', new['words']['he'])
        self.assertIn('מלך', new['confident']['he'])

    def test_generated_fixture_manifests_also_match_v2_json_schema(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((ROOT / 'schemas' / 'language-pack-v2.schema.json').read_text(
            encoding='utf-8'))
        checker = Draft202012Validator(schema)
        for code in ('ru', 'en', 'he'):
            with self.subTest(code=code):
                doc = json.loads((self.root / code / 'manifest.json').read_text(
                    encoding='utf-8'))
                self.assertEqual(list(checker.iter_errors(doc)), [])

    def test_all_one_two_three_language_subsets_match_legacy_reader(self):
        from itertools import combinations, permutations
        for size in (1, 2, 3):
            for selected in combinations(('ru', 'en', 'he'), size):
                for order in permutations(selected):
                    with self.subTest(order=order):
                        reference = _read_lexical_data(self.data, (
                            entry for entry in LANGUAGE_REGISTRY if entry.code in order))
                        actual = load_selected_v2_lexicon(
                            self.root, order, exclusions_file=self.exclusions_file)
                        self.assertEqual(reference, actual)
                        self.assertEqual(tuple(actual['words']), order)

    def test_he_custom_has_separate_words_and_confident_normalizers(self):
        # Pronunciation marks reveal the otherwise hidden difference.
        hebrew = self.root / 'he'
        manifest = json.loads((hebrew / 'manifest.json').read_text(encoding='utf-8'))
        vocalized = 'מַלְךְ\n'
        (hebrew / 'he_custom.txt').write_text(vocalized, encoding='utf-8')
        raw = (hebrew / 'he_custom.txt').read_bytes()
        manifest['files']['he_custom.txt'] = {
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        (hebrew / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False),
                                              encoding='utf-8')
        (self.data / 'he_custom.txt').write_text(vocalized, encoding='utf-8')
        actual = load_selected_v2_lexicon(self.root, ('he',),
                                          exclusions_file=self.exclusions_file)
        reference = _read_lexical_data(self.data, (LANGUAGE_REGISTRY[2],))
        self.assertEqual(actual, reference)
        self.assertIn('מלך', actual['words']['he'])
        self.assertIn('מַלְךְ', actual['confident']['he'])
        self.assertNotIn('מַלְךְ', actual['words']['he'])

    def test_unicode_line_separators_match_legacy_splitlines_semantics(self):
        # A plain `for line in file` would treat these as ONE token, unlike
        # legacy `str.splitlines()` and thus silently change the dictionary.
        special = 'дом\u2028#skip\u0085мир\u001eтест\n'
        target = self.data / 'ru.txt'
        target.write_text(special, encoding='utf-8')
        package = self.root / 'ru'
        (package / 'ru.txt').write_bytes(target.read_bytes())
        manifest_file = package / 'manifest.json'
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        raw = (package / 'ru.txt').read_bytes()
        manifest['files']['ru.txt'] = {'bytes': len(raw),
                                      'sha256': hashlib.sha256(raw).hexdigest()}
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        legacy = _read_lexical_data(self.data, (LANGUAGE_REGISTRY[0],))
        new = load_selected_v2_lexicon(self.root, ('ru',),
                                       exclusions_file=self.exclusions_file)
        self.assertEqual(new, legacy)
        self.assertIn('мир', new['words']['ru'])
        self.assertIn('тест', new['words']['ru'])
        self.assertNotIn('#skip', new['words']['ru'])

    def test_corrupted_unused_language_is_not_read(self):
        (self.root / 'he' / 'he.txt').write_bytes(b'bad words\n')
        selected = load_v2_selection(self.root, ['ru', 'en'])
        self.assertEqual(set(selected), {'ru', 'en'})
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            load_v2_selection(self.root, ['he'])

    def test_invalid_manifest_and_payload_are_rejected(self):
        from unittest.mock import patch
        good = self.root / 'ru'
        raw = (good / 'manifest.json').read_text(encoding='utf-8')
        cases = (
            ('unknown executable field', lambda d: d.update(script='evil.py')),
            ('unsafe normalizer', lambda d: d['normalization'].update(dictionary='python_eval')),
            ('unsafe role script', lambda d: d['additions'][0]['normalization'].update(words='exec')),
            ('unsafe key', lambda d: d['behavior'].update(
                source_nonletter_keys=[{'vk': 65, 'character': '!'}])),
            ('hebrew nonletter on RU', lambda d: d['behavior'].update(
                source_nonletter_keys=[{'vk': 81, 'character': '/'}])),
            ('hebrew personal alphabet on RU', lambda d: d['behavior'].update(
                personal_alphabet_policy='hebrew_letters')),
            ('duplicate addition', lambda d: d['additions'].append(deepcopy(d['additions'][0]))),
            ('missing file declaration', lambda d: d['files'].pop('ru.txt')),
            ('boolean file size', lambda d: d['files']['ru.txt'].update(bytes=True)),
            ('bogus priority', lambda d: d['alternatives'].append('ru')),
            ('unsafe name', lambda d: d.update(dictionary_file='../secrets.txt')),
            ('wrong schema', lambda d: d.update(schema_version=1)),
            ('same lang code', lambda d: d.update(code='he')),
        )
        for name, mutator in cases:
            with self.subTest(name=name):
                doc = json.loads(raw)
                mutator(doc)
                (good / 'manifest.json').write_text(json.dumps(doc, ensure_ascii=False),
                                                    encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_v2_pack(good)
        (good / 'manifest.json').write_text(raw, encoding='utf-8')
        for code in ('../bad', '', 'Ru'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                load_v2_selection(self.root, [code])
        for invalid in ([['ru']], ['ru', 'ru'], ['ru', 'en', 'he', 'es']):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                load_v2_selection(self.root, invalid)

    def test_parser_rejects_duplicate_json_fields_and_undeclared_file(self):
        ru = self.root / 'ru'
        manifest = ru / 'manifest.json'
        original = manifest.read_bytes()
        manifest.write_bytes(b'{"schema_version":2,"schema_version":2}')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON field'):
            load_v2_pack(ru)
        manifest.write_bytes(original)
        (ru / 'injected.py').write_text('print(123)', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            load_v2_pack(ru)

    def test_second_validation_rejects_post_validation_edit(self):
        packs = load_v2_selection(self.root, ('ru',))
        (self.root / 'ru' / 'ru.txt').write_text('injected\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed since validation'):
            build_v2_lexicon(packs, exclusions_file=self.exclusions_file)

    def test_v2_normalizers_match_legacy_public_examples(self):
        from frequency_rank import normalize_word
        inputs = ['ЁЖИК', 'ещё', 'Straße', 'HELLO', 'שָׁלוֹם', 'Ångström']
        for lang, mode in (('ru','nfkc_casefold_strip_combining_yo_to_e'),
                           ('en','nfkc_casefold_strip_combining'),
                           ('he','nfkc_casefold_strip_combining')):
            for word in inputs:
                with self.subTest(lang=lang, word=word):
                    self.assertEqual(normalize_v2(word, mode), normalize_word(word, lang))
        self.assertEqual(normalize_v2('Mַלְךְ', 'casefold'), 'mַלְךְ')

    def _policy_candidate(self, case, packages=None):
        selected = ('ru', 'en', 'he')
        packages = packages if packages is not None else load_v2_selection(self.root, selected)
        forms = case['forms']
        words = {lang: set() for lang in selected}
        confident = {lang: set() for lang in selected}
        for lang in case.get('known', ()):
            words[lang].add(normalize_v2(forms[lang], packages[lang].normalization))
        for lang in case.get('confident', ()):
            confident[lang].add(normalize_v2(forms[lang], packages[lang].normalization))
        return PolicyDecisionEngine(
            packs=packages, active=selected, mapper=SyntheticMapper(forms),
            words=words, confident=confident,
            exclusions=set(case.get('excluded', ())),
            conservative=case.get('conservative', True))

    def test_independent_policy_decisions_identical_to_pinned_old_engine(self):
        cases = load_corpus()
        def independent(case):
            engine = self._policy_candidate(case)
            keys = [(ord(char.upper()), False) for char in case['physical']]
            if case.get('leading_comma_vk'):
                keys[0] = (0xBC, False)
            result = engine.decide_token(keys, case['source'],
                                         allow_rare=case.get('allow_rare', False))
            return asdict(result) if result is not None else None
        self.assertEqual(compare_adapters(cases, legacy_decision, independent), [])
        for case in cases:
            with self.subTest(case=case['id']):
                self.assertEqual(independent(case), case['expected'])

    def test_independent_prefix_policy_identical_to_133_frozen_prefixes(self):
        cases = load_corpus()
        corpus = json.loads((CONTRACT / 'prefix-oracle.json').read_text(encoding='utf-8'))
        stored = {item['id']: item['expected_prefixes'] for item in corpus['cases']}
        def independent(case):
            engine = self._policy_candidate(case)
            engine.prepare_early()
            return [{'length': i,
                     'decision': (asdict(result) if result is not None else None)}
                    for i in range(1, len(case['physical']) + 1)
                    for result in [engine.suggest_live_early(
                        [(ord(char.upper()), False) for char in case['physical'][:i]],
                        case['source'])]]
        differences = compare_adapters(cases, legacy_prefixes, independent)
        self.assertEqual(differences, [])
        for case in cases:
            with self.subTest(case=case['id']):
                self.assertEqual(independent(case), stored[case['id']])

    def test_reject_multiple_is_package_declared_not_builtin_switch(self):
        import shutil
        # Both v1 Arabic samples and v2 example are test-only, not real AR QA.
        target = self.root / 'ar'
        shutil.copytree(ROOT / 'fixtures' / 'r16-demo-packs' / 'ar', target)
        new_manifest = (CONTRACT / 'ar.manifest.v2.example.json').read_bytes()
        (target / 'manifest.json').write_bytes(new_manifest)
        packs = load_v2_selection(self.root, ('en', 'ru', 'ar'))
        forms = {'en': 'hello', 'ru': 'привет', 'ar': 'مرحبا'}
        engine = PolicyDecisionEngine(packs=packs, active=('en', 'ru', 'ar'),
                                      mapper=SyntheticMapper(forms),
                                      words={'en': set(), 'ru': {'привет'}, 'ar': {'مرحبا'}},
                                      confident={'en': set(), 'ru': {'привет'},
                                                 'ar': {'مرحبا'}}, exclusions=set())
        self.assertIsNone(engine.resolve_ambiguity(
            [('ru', 'привет'), ('ar', 'مرحبا')], 'en'))
        self.assertEqual(packs['ar'].behavior.ambiguous_destinations, 'reject_multiple')

    def test_guard_detects_a_deliberately_broken_new_policy(self):
        cases = load_corpus()
        def broken(case):
            if case['id'] == 'ru-to-en':
                return None
            return legacy_decision(case)
        self.assertEqual([item['id'] for item in compare_adapters(
            cases, legacy_decision, broken)], ['ru-to-en'])

    def test_deterministic_randomized_old_new_decision_and_prefix_parity(self):
        import random
        rng = random.Random(1642)
        packages = load_v2_selection(self.root, ('ru', 'en', 'he'))
        scripts = {'ru': 'абвгдеёжзи', 'en': 'abcedfghij', 'he': 'אבגדהוזחטי'}
        from r16_4a_equivalence import legacy_engine_for_case
        from incremental_recognition import prepare_early, suggest_live_early
        from dataclasses import asdict
        for index in range(160):
            size = rng.randint(1, 12)
            forms = {code: ''.join(rng.choices(alphabet, k=size))
                     for code, alphabet in scripts.items()}
            known = [code for code in forms if rng.randrange(3) != 0]
            confident = [code for code in forms if rng.randrange(2) != 0]
            excluded = ([forms[rng.choice(tuple(forms))].casefold()]
                        if rng.randrange(5) == 0 else [])
            case = {'id': f'generated-{index:03}', 'source': rng.choice(tuple(forms)),
                    'forms': forms, 'physical': 'a'*size,
                    'known': known, 'confident': confident,
                    'excluded': excluded, 'conservative': bool(rng.randrange(2)),
                    'allow_rare': bool(rng.randrange(2))}
            old = legacy_engine_for_case(case)
            new = self._policy_candidate(case, packages)
            keys = [(65, False)]*size
            legacy_choice = old.decide_token(keys, case['source'],
                                             allow_rare=case['allow_rare'])
            v2_choice = new.decide_token(keys, case['source'],
                                         allow_rare=case['allow_rare'])
            with self.subTest(case=case['id'], gate='full-token'):
                self.assertEqual(asdict(legacy_choice) if legacy_choice else None,
                                 asdict(v2_choice) if v2_choice else None)
            prepare_early(old)
            new.prepare_early()
            for length in range(1, size + 1):
                prefix = keys[:length]
                legacy_early = suggest_live_early(old, prefix, case['source'])
                new_early = new.suggest_live_early(prefix, case['source'])
                with self.subTest(case=case['id'], prefix=length):
                    self.assertEqual(asdict(legacy_early) if legacy_early else None,
                                     asdict(new_early) if new_early else None)


if __name__ == '__main__':
    unittest.main()
