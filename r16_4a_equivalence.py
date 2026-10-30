"""R16/4a: read-only behavioral characterization and future parity harness.

Never instantiated by the running application. No hook, global HKL, GUI,
filesystem mutation or physical input. The mapper is deliberately synthetic.
This is a contract for R16/4b, NOT a new language engine implementation.
"""

from dataclasses import asdict
import json
from pathlib import Path

from engine import ALTERNATIVE_PRIORITY, LanguageEngine
from frequency_rank import normalize_word


CORPUS = Path(__file__).resolve().parent / 'fixtures' / 'r16-4a-contract' / 'decision-oracle.json'


class SyntheticMapper:
    def __init__(self, forms):
        self.forms = forms

    def word(self, keys, language):
        # A deterministic partial-keyboard word for each physical prefix.
        return self.forms[language][:len(keys)]

    def char(self, vk, language, shift=False):
        return ''


def load_corpus(path=CORPUS):
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    if (document.get('kind') != 'R16_4A_PINNED_LEGACY_DECISIONS'
            or document.get('version') != 1 or not isinstance(document.get('cases'), list)):
        raise ValueError('Invalid R16/4a oracle corpus')
    seen = set()
    for case in document['cases']:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str)
                or case['id'] in seen or not isinstance(case.get('forms'), dict)
                or set(case['forms']) != {'ru', 'en', 'he'}):
            raise ValueError('Malformed R16/4a oracle case')
        seen.add(case['id'])
    return document['cases']


def legacy_engine_for_case(case):
    """Construct legacy decision state with a mocked mapping, no OS APIs."""
    forms = case['forms']
    engine = LanguageEngine.__new__(LanguageEngine)
    engine.mapper = SyntheticMapper(forms)
    engine.language_catalog = None
    engine.active_languages = ('ru', 'en', 'he')
    engine.language_priority = ALTERNATIVE_PRIORITY
    engine.conservative = case.get('conservative', True)
    engine.user_lexicon = None
    engine.words = {code: set() for code in forms}
    engine.confident = {code: set() for code in forms}
    for code in case.get('known', ()):
        engine.words[code].add(normalize_word(forms[code], code))
    for code in case.get('confident', ()):
        engine.confident[code].add(normalize_word(forms[code], code))
    engine.exclusions = set(case.get('excluded', ()))
    return engine


def legacy_decision(case):
    """Run today's real LanguageEngine on deterministic artificial lexicons."""
    engine = legacy_engine_for_case(case)
    physical = case['physical']
    keys = [(ord(char.upper()), False) for char in physical]
    if case.get('leading_comma_vk'):
        keys[0] = (0xBC, False)
    choice = engine.decide_token(keys, case['source'],
                                 allow_rare=case.get('allow_rare', False))
    return asdict(choice) if choice is not None else None


def legacy_prefixes(case):
    """Every pre-Space prefix, not just the final successful correction."""
    from incremental_recognition import prepare_early, suggest_live_early
    engine = legacy_engine_for_case(case)
    prepare_early(engine)
    result = []
    for length in range(1, len(case['physical']) + 1):
        keys = [(ord(char.upper()), False) for char in case['physical'][:length]]
        choice = suggest_live_early(engine, keys, case['source'])
        result.append({'length': length,
                       'decision': asdict(choice) if choice is not None else None})
    return result


def compare_adapters(cases, reference, candidate):
    """Compare ALL decisions, including rejections and reason text.

    In R16/4b supply a truly independent candidate engine adapter. Invoking
    this with the same implementation twice is ONLY a harness self-test,
    not evidence of old/new equivalence.
    """
    differences = []
    for case in cases:
        old = reference(case)
        new = candidate(case)
        if old != new:
            differences.append({'id': case['id'], 'legacy': old, 'candidate': new})
    return differences
