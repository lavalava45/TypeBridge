"""Compare production v2 policy vs COM spellcheck on independent public titles.

Only public Wikimedia title metadata, curated acronym seed strings and
synthetic random input. No user text, TypeBridge.exe, actual HKLs, keyboard
hooks, active GUI, settings or per-user dictionaries. The frozen source
corpus is collected BEFORE looking up any TypeBridge or Windows spell results.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import time
import unicodedata

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from windows_spellcheck_probe import WindowsSpellProbe
from windows_spellcheck_benchmark import (LANGUAGES, LOCALES, PHYSICAL,
                                         SyntheticMapper, keyboard_maps,
                                         keys_for, summarize_latency)
from language_unified_runtime import prepare_selected
from language_decision_v2 import PolicyDecisionEngine


TITLE_CORPUS = HERE / 'fixtures' / 'windows_spellcheck_wikipedia_titles_20261030.json'
REPORT = ROOT / 'build' / 'windows-spellcheck-independent-20261030.json'
SEED = 20261030
ACRONYMS = {
    # Manually curated independent of the program and Windows; not a randomly
    # sampled population. No inferred 'spell-correct' labels from spellcheck.
    'en': ('NASA', 'UNESCO', 'NATO', 'HTTP', 'HTML', 'SQL', 'PDF', 'GPS',
           'USB', 'CPU', 'GPU', 'SSD', 'RAM', 'URL', 'XML', 'JSON', 'API',
           'DNS', 'TCP', 'UDP', 'JPEG', 'FBI', 'CIA', 'FAQ'),
    'ru': ('ООН', 'НАТО', 'МФТИ', 'РАН', 'ВДНХ', 'ГИБДД', 'МЧС', 'СССР',
           'РСФСР', 'ФСБ', 'ВТБ', 'РЖД', 'МГУ', 'ЕГЭ', 'ГЭС', 'ДНК',
           'РНК', 'ГМО', 'БРИКС', 'СМИ', 'НИИ', 'США'),
    # Hebrew initialisms use punctuation that cannot be faithfully represented
    # by our letter-only synthetic keyboard: report literal COM results only.
    'he': ('צה"ל', 'בג"ץ', 'שב"כ', 'נתב"ג', 'רש"י', 'קק"ל',
           'תנ"ך', 'חו"ל', 'רה"מ', 'צה"ל', 'אחה"צ'),
}


def script_ok(value: str, lang: str, catalog) -> bool:
    script = catalog[lang].unicode_script
    return bool(value and value.isalpha() and all(
        unicodedata.name(ch, '').startswith(script + ' ') for ch in value))


def make_samples(snapshot: dict, policy, tables, *, noise_per_language=240,
                 typo_per_language=85, seed=SEED):
    if (snapshot.get('kind') != 'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT'
            or set(snapshot.get('languages', ())) != set(LANGUAGES)):
        raise ValueError('Incomplete or unverified independent title snapshot')
    rng = random.Random(seed)
    samples = []
    statistics = {}
    for code in LANGUAGES:
        lang_rows = snapshot['languages'][code]['titles']
        unseen = set()
        eligible = []
        for page in lang_rows:
            word = page['title'].casefold()
            if (not script_ok(word, code, policy.packs_as_catalog)
                    or not 4 <= len(word) <= 18
                    or not keys_for(word, code, tables)):
                continue
            norm = policy.normalize(code, word)
            if (norm in policy.words[code] or norm in policy.confident[code]
                    or norm in unseen):
                continue
            unseen.add(norm)
            eligible.append((word, page))
        statistics[code] = {'source_articles': len(lang_rows),
                            'independently_attested_OOV_titles': len(eligible)}
        for word, page in eligible:
            physical = keys_for(word, code, tables)
            origin = {'pageid': page['pageid'], 'source_url': page['source_url'],
                      'attested_title': page['title']}
            samples.append(dict(kind='attested_OOV_source', source=code,
                                expected=None, physical=physical,
                                word=word, origin=origin))
            for source in LANGUAGES:
                if source != code:
                    samples.append(dict(kind='attested_OOV_wrong_layout',
                                        source=source, expected=code,
                                        physical=physical, word=word, origin=origin))
        rng.shuffle(eligible)
        for word, page in eligible[:typo_per_language]:
            physical = keys_for(word, code, tables)
            index = rng.randrange(len(physical))
            replacement = rng.choice([ch for ch in PHYSICAL if ch != physical[index]])
            changed = physical[:index] + replacement + physical[index + 1:]
            samples.append(dict(kind='attested_OOV_single_key_typo', source=code,
                                expected=None, physical=changed, word=word,
                                origin={'pageid': page['pageid'],
                                        'source_url': page['source_url']}))
        for token in ACRONYMS[code]:
            physical = keys_for(token.casefold(), code, tables)
            if physical:
                samples.append(dict(kind='curated_acronym', source=code,
                                    expected=None, physical=physical,
                                    word=token, origin={'type': 'manual_acronym_seed'}))
        for _ in range(noise_per_language):
            # Deliberately do not reject random keys because they accidentally
            # form known source or target words; such collisions ARE the hazard.
            physical = ''.join(rng.choice(PHYSICAL) for _ in range(rng.randint(3, 9)))
            samples.append(dict(kind='random_key_noise', source=code,
                                expected=None, physical=physical,
                                word=None, origin={'seed': seed}))
    rng.shuffle(samples)
    return samples, statistics


def wilson_upper(k, n, z=1.96):
    if not n:
        return None
    p = k/n
    return round((p + z*z/(2*n) + z*((p*(1-p)/n + z*z/(4*n*n)) ** .5)) /
                 (1 + z*z/n), 4)


def run(snapshot, *, noise_per_language=240, typo_per_language=85):
    data, cache, catalog, languages = prepare_selected(
        ROOT / 'data', None, LANGUAGES, cache_dir=None)
    tables = keyboard_maps()
    mapper = SyntheticMapper(tables)
    policy = PolicyDecisionEngine(
        packs=languages, active=LANGUAGES, mapper=mapper,
        words={code: set(data['words'][code]) for code in LANGUAGES},
        confident={code: set(data['confident'][code]) for code in LANGUAGES},
        exclusions=set(data['exclusions']), conservative=True)
    # A plain catalog is only used to validate Unicode script identity.
    policy.packs_as_catalog = catalog
    samples, corpus_stats = make_samples(snapshot, policy, tables,
                                         noise_per_language=noise_per_language,
                                         typo_per_language=typo_per_language)
    spell = WindowsSpellProbe()
    availability = {lang: spell.supported(LOCALES[lang]) for lang in LANGUAGES}
    if not all(availability.values()):
        return {'availability': availability, 'error': 'Not all locales available'}
    for lang, word in {'ru': 'привет', 'en': 'hello', 'he': 'שלום'}.items():
        spell.check(LOCALES[lang], word)

    metrics = defaultdict(Counter)
    by_destination = defaultdict(Counter)
    examples = defaultdict(list)
    latencies = defaultdict(list)
    per_token_ms = []
    started = time.perf_counter()
    for sample in samples:
        keys = [(ord(x.upper()), False) for x in sample['physical']]
        source, kind, expected = (sample['source'], sample['kind'], sample['expected'])
        variants = {lang: mapper.word(keys, lang) for lang in LANGUAGES}
        original = variants[source]
        baseline = policy.decide_token(keys, source)
        baseline_target = baseline.language if baseline is not None else None
        spells = {}
        token_ms = 0.0
        for lang, candidate in variants.items():
            if not script_ok(candidate, lang, catalog):
                spells[lang] = False
                continue
            ok, ms = spell.check(LOCALES[lang], candidate)
            spells[lang] = ok
            latencies[lang].append(ms)
            token_ms += ms
        per_token_ms.append(token_ms)
        alternatives = [lang for lang in LANGUAGES
                        if lang != source and spells[lang]]
        simple = (alternatives[0] if len(alternatives) == 1 and
                  not spells[source] else None)
        # Rescue operates only where current source was *not* a known word,
        # where physical layout and scripting are admissible, and no explicit
        # global exclusions exist. Never replace an existing baseline decision.
        guard = bool(2 <= len(keys) <= 40
                     and policy.valid_typed(keys, original, source)
                     and not policy.known(source, original)
                     and not policy.excluded(source, original))
        guarded = (simple if guard and simple is not None
                   and not policy.excluded(simple, variants[simple]) else None)
        after = baseline_target if baseline_target else guarded
        group = (kind, source)
        row = metrics[group]
        row['n'] += 1
        row['source_known'] += int(policy.known(source, original))
        row['source_spell_ok'] += int(spells[source])
        if expected is not None:
            row['target_spell_ok'] += int(spells[expected])
            row['target_OOV'] += int(not policy.known(expected, variants[expected]))
            destination = by_destination[expected]
            destination['n'] += 1
            destination['windows_positive'] += int(spells[expected])
            destination['guarded_rescued'] += int(after == expected and
                                                   baseline_target is None)
        for name, answer in (('baseline', baseline_target),
                             ('spell_only', simple),
                             ('guarded_rescue_alone', guarded),
                             ('baseline_plus_guarded', after)):
            row[name + '_correct'] += int(answer == expected)
            row[name + '_switched'] += int(answer is not None)
            row[name + '_wrong_switch'] += int(answer is not None and answer != expected)
            row[name + '_miss'] += int(expected is not None and answer != expected)
        if (after != baseline_target and len(examples[kind]) < 12):
            examples[kind].append({
                'source': source, 'expected': expected,
                'source_text': original, 'suggested': variants[after] if after else None,
                'selected': after, 'baseline': baseline_target,
                'origin': sample['origin'], 'source_known': policy.known(source, original),
            })

    groups = []
    for (kind, lang), values in sorted(metrics.items()):
        groups.append({'kind': kind, 'source': lang, **dict(values)})
    overall = defaultdict(Counter)
    for group in groups:
        overall[group['kind']].update({k: v for k, v in group.items()
                                      if isinstance(v, int)})
    direct_acronyms = defaultdict(Counter)
    for lang, terms in ACRONYMS.items():
        for token in sorted(set(terms)):
            ok, ms = spell.check(LOCALES[lang], token)
            direct_acronyms[lang]['n'] += 1
            direct_acronyms[lang]['spell_ok'] += int(ok)
            latencies[lang].append(ms)
    # Wikipedia title capitalization differs from synthetic lower-case key
    # mapping. This independent, direct COM sensitivity test explicitly
    # quantifies that confounder without pretending to model shifted HKLs.
    titlecase = defaultdict(Counter)
    for item in samples:
        if item['kind'] != 'attested_OOV_source':
            continue
        lang = item['source']
        original = item['origin']['attested_title']
        lowercase = item['word']
        as_title, _ = spell.check(LOCALES[lang], original)
        as_lower, _ = spell.check(LOCALES[lang], lowercase)
        titlecase[lang]['n'] += 1
        titlecase[lang]['title_no_errors'] += int(as_title)
        titlecase[lang]['lowercase_no_errors'] += int(as_lower)
        titlecase[lang]['capitalization_changed_result'] += int(as_title != as_lower)
    # Deliberately nonsensical uppercase strings: false acceptance here
    # demonstrates why spellcheck's empty error enumeration is not an
    # affirmative validation of acronyms or language identity.
    case_controls = {}
    for lang, pairs in {
        'en': [('NASA', 'nasa'), ('ZZQXZQ', 'zzqxzq')],
        'ru': [('ООН', 'оон'), ('ФЙЦЫЩЪ', 'фйцыщъ')],
        'he': [('צה"ל', 'צה"ל'), ('שק"ץ', 'שקץ')],
    }.items():
        case_controls[lang] = [
            {'first': first, 'first_no_errors': spell.check(LOCALES[lang], first)[0],
             'second': second, 'second_no_errors': spell.check(LOCALES[lang], second)[0]}
            for first, second in pairs]
    return {
        'kind': 'INDEPENDENT_PUBLIC_TITLE_TYPEBRIDGE_SPELLCHECK_AB',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'source_snapshot_sha256': hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest(),
        'input_provenance': 'Random Wikipedia main namespace single-token titles; curated acronyms; seeded random keys',
        'sampling_limitations': ('Only article-title proxies externally attested, not semantic '
                                 'proof of intended spelling; negative controls intentionally generated; '
                                 'no real user typing, no native HKL, no No01 acceptance.'),
        'seed': SEED, 'sample_count': len(samples), 'source_stats': corpus_stats,
        'availability': availability,
        'elapsed_s': round(time.perf_counter() - started, 3),
        'latency_by_lang': {lang: summarize_latency(latencies[lang])
                            for lang in LANGUAGES},
        'latency_per_token_sum': summarize_latency(per_token_ms),
        'groups': groups,
        'group_totals': {kind: dict(values) for kind, values in overall.items()},
        'oov_wrong_layout_by_target': {lang: dict(values)
                                       for lang, values in by_destination.items()},
        'literal_acronym_check': {lang: dict(values) for lang, values in direct_acronyms.items()},
        'title_capitalization_sensitivity': {lang: dict(values)
                                             for lang, values in titlecase.items()},
        'case_sensitivity_controls': case_controls,
        'examples_guarded_rescue': dict(examples),
        'negative_control_false_positive_upper95': {
            kind: wilson_upper(values['baseline_plus_guarded_wrong_switch'], values['n'])
            for kind, values in overall.items() if kind != 'attested_OOV_wrong_layout'},
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--corpus', type=Path, default=TITLE_CORPUS)
    parser.add_argument('--output', type=Path, default=REPORT)
    parser.add_argument('--noise-per-lang', type=int, default=240)
    parser.add_argument('--typo-per-lang', type=int, default=85)
    args = parser.parse_args()
    if args.output.resolve().parent != ROOT / 'build' or args.output.exists():
        parser.error('New, non-existing output under build/ required')
    if args.corpus.resolve().parent != HERE / 'fixtures':
        parser.error('Corpus must be a local frozen project research fixture')
    with args.corpus.open(encoding='utf-8') as stream:
        corpus = json.load(stream)
    result = run(corpus, noise_per_language=args.noise_per_lang,
                 typo_per_language=args.typo_per_lang)
    args.output.parent.mkdir(exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    public = {key: result[key] for key in ('availability', 'sample_count',
              'source_stats', 'group_totals', 'literal_acronym_check',
              'latency_per_token_sum', 'negative_control_false_positive_upper95')
              if key in result}
    print(json.dumps(public, ensure_ascii=False, indent=2), flush=True)
    print(f'REPORT={args.output.resolve()}', flush=True)
