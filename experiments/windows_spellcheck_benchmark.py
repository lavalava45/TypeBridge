"""Offline, reproducible A/B exploration of Windows spelling evidence.

No TypeBridge.exe, user lexicons, settings, physical keyboard or Win32 HKL APIs.
Production word/confidence files and v2 decision policy are read only. A
synthetic standard-keyboard mapper substitutes for the OS keyboard mapping;
therefore the benchmark is NOT native No01/typing acceptance or an unbiased
real-world accuracy estimate. No personal input is collected or logged.

Use: python experiments/windows_spellcheck_benchmark.py
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import random
import statistics
import sys
import time
from pathlib import Path

from windows_spellcheck_probe import WindowsSpellProbe


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from language_decision_v2 import PolicyDecisionEngine  # noqa: E402
from language_unified_runtime import prepare_selected  # noqa: E402


# Standard physical Latin key positions, NOT the user's installed layouts.
PHYSICAL = 'qwertyuiopasdfghjklzxcvbnm'
OUTPUTS = {
    'en': PHYSICAL,
    'ru': 'йцукенгшщзфывапролдячсмить',
    'he': "/'קראטוןםפשדגכעיחלךזסבהנמצ",
}
LOCALES = {'ru': 'ru-RU', 'en': 'en-US', 'he': 'he-IL'}
LANGUAGES = ('ru', 'en', 'he')


def keyboard_maps():
    if any(len(OUTPUTS[lang]) != len(PHYSICAL) for lang in LANGUAGES):
        raise ValueError('Invalid synthetic keyboard map length')
    return {lang: dict(zip(PHYSICAL, OUTPUTS[lang])) for lang in LANGUAGES}


class SyntheticMapper:
    """Immutable, offline physical-key mapper. No user32 or HKL calls."""

    def __init__(self, tables):
        self.tables = tables

    def char(self, vk, language, shift=False):
        key = chr(vk).lower()
        return self.tables[language].get(key, '')

    def word(self, keys, language):
        return ''.join(self.char(vk, language, shift) for vk, shift in keys)


class WithheldWords:
    """Read-only view simulating words absent from the TypeBridge dictionary."""

    def __init__(self, words, withheld):
        self.words = words
        self.withheld = withheld

    def __contains__(self, word):
        return word not in self.withheld and word in self.words


@dataclass(frozen=True)
class Sample:
    kind: str
    source: str
    intended: str | None
    physical: str
    typed: str
    correct_word: str | None


def keys_for(word, language, tables):
    inverse = {value: key for key, value in tables[language].items()}
    return ''.join(inverse[letter] for letter in word) if all(
        ch in inverse for ch in word) else None


def cases(data, tables, *, per_direction, per_known, per_typo, seed):
    rng = random.Random(seed)
    indexed = {}
    for lang in LANGUAGES:
        entries = [word for word in data['confident'][lang]
                   if word.isalpha() and word == word.casefold() and 5 <= len(word) <= 12
                   and keys_for(word, lang, tables)]
        entries.sort()
        rng.shuffle(entries)
        indexed[lang] = entries
    all_cases = []
    for target in LANGUAGES:
        for source in LANGUAGES:
            if source == target:
                continue
            chosen = 0
            for word in indexed[target]:
                physical = keys_for(word, target, tables)
                typed = ''.join(tables[source][key] for key in physical)
                if not typed.isalpha() or typed == word:
                    continue
                # Only examine unambiguous bona-fide wrong-layout examples.
                if typed in data['words'][source] or typed in data['exclusions']:
                    continue
                all_cases.append(Sample('wrong_layout', source, target,
                                        physical, typed, word))
                chosen += 1
                if chosen == per_direction:
                    break
            if chosen != per_direction:
                raise RuntimeError(f'Insufficient {target}->{source} cases: {chosen}')
    for source in LANGUAGES:
        valid = 0
        typo = 0
        for word in indexed[source]:
            physical = keys_for(word, source, tables)
            if valid < per_known:
                all_cases.append(Sample('known_source', source, None,
                                        physical, word, word))
                valid += 1
            if typo < per_typo:
                # Very obvious but not guaranteed invalid synthetic typo.
                mutated = word[:len(word)//2] + word[len(word)//2] * 3 + word[len(word)//2:]
                mutated_keys = keys_for(mutated, source, tables)
                if (mutated_keys and mutated not in data['words'][source]
                        and 5 <= len(mutated) <= 40):
                    all_cases.append(Sample('synthetic_typo', source, None,
                                            mutated_keys, mutated, None))
                    typo += 1
            if valid == per_known and typo == per_typo:
                break
        if valid != per_known or typo != per_typo:
            raise RuntimeError(f'Insufficient control samples for {source}')
    rng.shuffle(all_cases)
    return all_cases


def summarize_latency(values):
    if not values:
        return None
    ordered = sorted(values)
    def pct(percent):
        return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1)
                                                 * percent))], 4)
    return {'n': len(values), 'p50_ms': pct(.50), 'p95_ms': pct(.95),
            'p99_ms': pct(.99), 'max_ms': round(ordered[-1], 4),
            'mean_ms': round(statistics.mean(values), 4)}


def run(*, per_direction=70, per_known=100, per_typo=100, seed=20261030):
    data, cache, catalog, languages = prepare_selected(
        ROOT / 'data', None, LANGUAGES, cache_dir=None)
    tables = keyboard_maps()
    mapper = SyntheticMapper(tables)
    policy = PolicyDecisionEngine(
        packs=languages, active=LANGUAGES, mapper=mapper,
        words={key: set(data['words'][key]) for key in LANGUAGES},
        confident={key: set(data['confident'][key]) for key in LANGUAGES},
        exclusions=set(data['exclusions']), conservative=True)
    corpus = cases(data, tables, per_direction=per_direction,
                   per_known=per_known, per_typo=per_typo, seed=seed)
    # Simulate previously unknown target words by withholding half of the
    # positive samples from the decision model. This is NOT a real OOV dataset;
    # it tests only whether Windows could rescue a missing lexical hit.
    withheld_samples = [case for i, case in enumerate(
        case for case in corpus if case.kind == 'wrong_layout') if i % 2 == 0]
    withheld = {code: set() for code in LANGUAGES}
    for case in withheld_samples:
        withheld[case.intended].add(case.correct_word)
        corpus.append(Sample('simulated_out_of_vocabulary', case.source,
                             case.intended, case.physical,
                             case.typed, case.correct_word))
    heldout_policy = PolicyDecisionEngine(
        packs=languages, active=LANGUAGES, mapper=mapper,
        words={code: WithheldWords(policy.words[code], withheld[code])
               for code in LANGUAGES},
        confident={code: WithheldWords(policy.confident[code], withheld[code])
                   for code in LANGUAGES},
        exclusions=policy.exclusions, conservative=True)
    api = WindowsSpellProbe()
    available = {code: api.supported(tag) for code, tag in LOCALES.items()}
    # Do not run a partial quality experiment if even one language unavailable.
    if not all(available.values()):
        return {'availability': available, 'quality': 'not evaluated: missing language'}
    for code, tag in LOCALES.items():
        api.check(tag, {'en': 'hello', 'ru': 'привет', 'he': 'שלום'}[code])

    score = defaultdict(Counter)
    latency = defaultdict(list)
    token_latency = []
    examples = defaultdict(list)
    t0 = time.perf_counter()
    for case in corpus:
        keys = [(ord(ch.upper()), False) for ch in case.physical]
        forms = {code: mapper.word(keys, code) for code in LANGUAGES}
        # The exact production v2 decision implementation, synthetic mapper.
        model = heldout_policy if case.kind == 'simulated_out_of_vocabulary' else policy
        baseline = model.decide_token(keys, case.source)
        checks = {}
        total_check_ms = 0.0
        for code, word in forms.items():
            # Always enforce the candidate's script. A COM "no errors" answer
            # for a different writing system is NOT language identification.
            if not word.isalpha() or not policy.one_script(word):
                checks[code] = False
                continue
            ok, ms = api.check(LOCALES[code], word)
            checks[code] = ok
            latency[code].append(ms)
            total_check_ms += ms
        token_latency.append(total_check_ms)
        candidates = [code for code in LANGUAGES if code != case.source
                      and checks[code]]
        pure_spell = (candidates[0] if len(candidates) == 1 and
                      not checks[case.source] else None)
        baseline_target = baseline.language if baseline else None
        confirmed = (baseline_target if baseline_target and
                     checks[baseline_target] and not checks[case.source]
                     else None)
        add_on = baseline_target or pure_spell
        expected = case.intended
        group = (case.kind, case.source,
                 case.intended if expected is not None else '-')
        score[group]['source_spell_no_errors'] += int(checks[case.source])
        if expected is not None:
            score[group]['target_spell_no_errors'] += int(checks[expected])
            score[group]['other_target_spell_no_errors'] += int(any(
                checks[lang] for lang in LANGUAGES
                if lang not in (case.source, expected)))
        for name, candidate in [('baseline', baseline_target),
                                ('spell_only', pure_spell),
                                ('spell_veto', confirmed),
                                ('baseline_plus_spell_rescue', add_on)]:
            score[group][f'{name}_correct'] += int(candidate == expected)
            score[group][f'{name}_switches'] += int(candidate is not None)
            score[group][f'{name}_wrong_switches'] += int(
                candidate is not None and candidate != expected)
            score[group][f'{name}_misses'] += int(
                expected is not None and candidate != expected)
        score[group]['n'] += 1
        if pure_spell and not baseline and len(examples[case.kind]) < 8:
            examples[case.kind].append({'from': case.source, 'to': pure_spell,
                                        'expected': expected,
                                        'typed': case.typed,
                                        'proposed': forms[pure_spell]})

    groups = []
    for (kind, source, target), row in sorted(score.items()):
        groups.append({'kind': kind, 'source': source, 'target': target,
                       **dict(row)})
    totals = defaultdict(Counter)
    for row in groups:
        totals[row['kind']].update({key: value for key, value in row.items()
                                   if isinstance(value, int)})
    return {
        'experiment': 'offline_spellcheck_three_languages',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'seed': seed, 'language_tags': LOCALES,
        'availability': available, 'cache': cache,
        'scenario': {'per_direction': per_direction,
                     'per_known_language': per_known, 'per_typo_language': per_typo},
        'sample_count': len(corpus),
        'quality_comparison': ('synthetic cases, not natural typing; validated '
                               'v2 decision logic; OOV simulated by withholding '
                               'some words, not verified genuine OOV'),
        'total_elapsed_s': round(time.perf_counter() - t0, 3),
        'api_latency_by_language': {code: summarize_latency(latency[code])
                                    for code in LANGUAGES},
        'api_latency_per_token_sum': summarize_latency(token_latency),
        'group_totals': {kind: dict(row) for kind, row in totals.items()},
        'groups': groups,
        'public_dictionary_examples': dict(examples),
    }


def main():
    arg = argparse.ArgumentParser(description=__doc__)
    arg.add_argument('--per-direction', type=int, default=70)
    arg.add_argument('--known-per-lang', type=int, default=100)
    arg.add_argument('--typo-per-lang', type=int, default=100)
    arg.add_argument('--seed', type=int, default=20261030)
    arg.add_argument('--output', type=Path,
                     default=ROOT / 'build' / 'windows-spellcheck-research-20261030.json')
    opts = arg.parse_args()
    for count in (opts.per_direction, opts.known_per_lang, opts.typo_per_lang):
        if count < 1 or count > 500:
            arg.error('Sample count must be 1..500')
    result = run(per_direction=opts.per_direction,
                 per_known=opts.known_per_lang, per_typo=opts.typo_per_lang,
                 seed=opts.seed)
    out = opts.output.resolve()
    if ROOT / 'build' != out.parent:
        arg.error('Only project build/ report path is allowed')
    if out.exists():
        arg.error('Refusing to overwrite existing research report')
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8')
    print(json.dumps({key: result[key] for key in
                      ('availability', 'sample_count', 'total_elapsed_s',
                       'api_latency_by_language', 'group_totals')
                      if key in result}, ensure_ascii=False, indent=2))
    print(f'REPORT={out}')


if __name__ == '__main__':
    main()
