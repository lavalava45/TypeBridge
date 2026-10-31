"""Read-only, reproducible RU/EN/HE layout-quality gate; no hooks or COM.

Public Wikipedia page IDs divisible by 3 were used in earlier training;
this evaluator uses ONLY the complementary held-out pages. Synthetic known
word controls are auxiliary and must not be presented as independent text.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

from independent_spellcheck_benchmark import TITLE_CORPUS, script_ok
from windows_spellcheck_benchmark import (LANGUAGES, SyntheticMapper,
                                          keyboard_maps, keys_for, cases)
from language_unified_runtime import prepare_selected
from language_decision_v2 import PolicyDecisionEngine

SEED = 20261031
MIN_KNOWN_RECALL = 0.97
MAX_NEGATIVE_SWITCH_RATE = 0.01
MAX_P95_DECISION_MS = 10.0


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def holdout_rows(snapshot, policy, tables, *, train_page_ids=()):
    """Select frozen independent targets; never use them to tune thresholds."""
    if snapshot.get('kind') != 'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT':
        raise ValueError('Unexpected corpus provenance')
    if set(snapshot.get('languages', {})) != set(LANGUAGES):
        raise ValueError('Missing language in frozen snapshot')
    seen = set()
    rows = []
    for target in LANGUAGES:
        for page in snapshot['languages'][target]['titles']:
            pageid = page['pageid']
            if pageid % 3 == 0 or (target, pageid) in train_page_ids:
                continue
            word = page['title'].casefold()
            if not (4 <= len(word) <= 18 and script_ok(word, target,
                    policy.packs_as_catalog)):
                continue
            physical = keys_for(word, target, tables)
            normalized = policy.normalize(target, word)
            if not physical:
                continue
            if (target, normalized) in seen:
                continue
            seen.add((target, normalized))
            known = normalized in policy.words[target]
            confident = normalized in policy.confident[target]
            for source in LANGUAGES:
                if source != target and known:
                    typed = ''.join(tables[source].get(key, '') for key in physical)
                    # Keep an unambiguous dictionary-positive subset; collisions
                    # and exclusions are reported separately as negative controls.
                    if (len(typed) != len(physical) or
                            policy.known(source, typed) or
                            policy.excluded(source, typed)):
                        continue
                rows.append(dict(kind=('heldout_negative' if source == target
                                  else ('heldout_iv_confident_positive' if confident else
                                        'heldout_iv_ordinary_positive' if known else
                                        'heldout_oov_positive')), source=source,
                                 expected=None if source == target else target,
                                 target=target, physical=physical,
                                 pageid=pageid, target_confident=confident))
    return rows


def bucket(length):
    if length <= 4:
        return '2-4'
    if length <= 8:
        return '5-8'
    if length <= 12:
        return '9-12'
    return '13+'


def summarize(values):
    if not values:
        return {'n': 0}
    ordered = sorted(values)
    return {'n': len(values), 'p50_ms': round(ordered[(len(ordered)-1)//2], 5),
            'p95_ms': round(ordered[int((len(ordered)-1)*.95)], 5),
            'p99_ms': round(ordered[int((len(ordered)-1)*.99)], 5),
            'mean_ms': round(statistics.mean(values), 5)}


def evaluate(rows, policy):
    counters = defaultdict(lambda: defaultdict(int))
    delays = defaultdict(list)
    for row in rows:
        keys = [(ord(k.upper()), False) for k in row['physical']]
        start = time.perf_counter_ns()
        result = policy.decide_token(keys, row['source'])
        elapsed_ms = (time.perf_counter_ns()-start)/1_000_000
        predicted = result.language if result is not None else None
        desired = row['expected']
        group = (row['kind'], row['source'], row['target'],
                 bucket(len(row['physical'])))
        count = counters[group]
        count['n'] += 1
        count['correct'] += predicted == desired
        count['miss'] += desired is not None and predicted != desired
        count['false_switch'] += desired is None and predicted is not None
        count['wrong_destination'] += predicted is not None and predicted != desired
        delays[group].append(elapsed_ms)
    result = []
    for key in sorted(counters):
        counts = counters[key]
        result.append(dict(zip(('kind','source','target','length'), key),
                           **counts, latency=summarize(delays[key])))
    return result, summarize([v for samples in delays.values() for v in samples])


def aggregate(groups, predicate):
    selected = [g for g in groups if predicate(g)]
    return {key: sum(g.get(key, 0) for g in selected)
            for key in ('n', 'correct', 'miss', 'false_switch', 'wrong_destination')}


def run():
    data, _, catalog, languages = prepare_selected(
        ROOT / 'data', None, LANGUAGES, cache_dir=None)
    tables = keyboard_maps()
    mapper = SyntheticMapper(tables)
    policy = PolicyDecisionEngine(
        packs=languages, active=LANGUAGES, mapper=mapper,
        words={lang: set(data['words'][lang]) for lang in LANGUAGES},
        confident={lang: set(data['confident'][lang]) for lang in LANGUAGES},
        exclusions=set(data['exclusions']), conservative=True)
    policy.packs_as_catalog = catalog
    snapshot = json.loads(TITLE_CORPUS.read_text(encoding='utf-8'))
    independent = holdout_rows(snapshot, policy, tables)
    control = cases(data, tables, per_direction=50, per_known=50,
                    per_typo=50, seed=SEED)
    auxiliary = [dict(kind='synthetic_' + item.kind,
                      source=item.source, target=item.intended or item.source,
                      expected=item.intended, physical=item.physical)
                 for item in control]
    groups, timings = evaluate(independent + auxiliary, policy)
    known = aggregate(groups, lambda g: g['kind'] == 'synthetic_wrong_layout')
    negatives = aggregate(groups, lambda g: g['kind'] in
                          ('heldout_negative', 'synthetic_known_source',
                           'synthetic_synthetic_typo'))
    heldout = aggregate(groups, lambda g: g['kind'] == 'heldout_oov_positive')
    independent_iv = aggregate(groups, lambda g: g['kind'].startswith('heldout_iv_'))
    independent_confident = aggregate(groups, lambda g: g['kind'] == 'heldout_iv_confident_positive')
    gates = {
        'known_recall_min_0_97': bool(known['n']) and known['correct']/known['n'] >= MIN_KNOWN_RECALL,
        'independent_confident_recall_min_0_97': (bool(independent_confident['n']) and
            independent_confident['correct']/independent_confident['n'] >= MIN_KNOWN_RECALL),
        'negative_false_switch_max_0_01': bool(negatives['n']) and negatives['false_switch']/negatives['n'] <= MAX_NEGATIVE_SWITCH_RATE,
        'decision_p95_max_10ms': timings['p95_ms'] <= MAX_P95_DECISION_MS,
    }
    return {'scope': 'source-only synthetic mapper; public title holdout; no real typing',
            'seed': SEED, 'languages': list(LANGUAGES),
            'split': 'Wikipedia pageid % 3 != 0; preexisting train pageid % 3 == 0',
            'sha256': {'public_fixture': digest(TITLE_CORPUS),
                       'policy_source': digest(ROOT/'language_decision_v2.py'),
                       'data_source': digest(ROOT/'language_unified_runtime.py'),
                       **{f'data/{name}': digest(ROOT/'data'/name) for name in (
                           'ru.txt', 'en.txt', 'he.txt', 'confident_ru.txt',
                           'confident_en.txt', 'confident_he.txt', 'exclude.txt')}},
            'thresholds': {'known_recall_min': MIN_KNOWN_RECALL,
                           'negative_false_switch_max': MAX_NEGATIVE_SWITCH_RATE,
                           'decision_p95_ms_max': MAX_P95_DECISION_MS},
            'known_positive': known, 'independent_iv_positive': independent_iv,
            'independent_confident_positive': independent_confident,
            'heldout_oov_positive_diagnostic': heldout,
            'independent_iv_by_direction': [
                {'source': source, 'target': target,
                 **aggregate(groups, lambda g: g['kind'].startswith('heldout_iv_')
                             and g['source'] == source and g['target'] == target)}
                for source in LANGUAGES for target in LANGUAGES if source != target],
            'negative_controls': negatives, 'decision_latency': timings,
            'gate': gates, 'pass': all(gates.values()), 'strata': groups}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = json.dumps(run(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + '\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
