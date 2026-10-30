"""Independent, source-only A/B: wordfreq vocabulary tiers and character n-grams.

Does not import TypeBridge UI, instantiate Windows HKLs, use keyboard hooks,
write personal dictionaries or build EXEs. Third-party frequency data is
installed ONLY to build/_spellcheck_research_deps and never redistributed.

Wordfreq word-frequency evidence is NOT a dictionary validity proof; Wikipedia
titles are attested encyclopedia entries, NOT verified lexical lemmas.
Training-source in-sample results are separated from held-out Wikipedia tests.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
DEPS = ROOT / 'build' / '_spellcheck_research_deps'
sys.path[:0] = [str(DEPS), str(HERE), str(ROOT)]

from wordfreq import top_n_list, zipf_frequency
from importlib.metadata import version

wordfreq_version = version('wordfreq')

from independent_spellcheck_benchmark import (TITLE_CORPUS, make_samples,
                                                script_ok, ACRONYMS)
from windows_spellcheck_benchmark import (SyntheticMapper, keyboard_maps,
                                         keys_for, cases)
from language_unified_runtime import prepare_selected
from language_decision_v2 import PolicyDecisionEngine


LANGUAGES = ('ru', 'en', 'he')
NGRAM_N = (3, 4, 5)
SEED = 20261030


def create_policy(data, languages, mapper):
    return PolicyDecisionEngine(
        packs=languages, active=LANGUAGES, mapper=mapper,
        words={lang: set(data['words'][lang]) for lang in LANGUAGES},
        confident={lang: set(data['confident'][lang]) for lang in LANGUAGES},
        exclusions=set(data['exclusions']), conservative=True)


def collect_frequency_oov(policy, tables, *, target=1000, holdout=200,
                          rank_limit=175000):
    result, heldout, meta = {}, {}, {}
    for lang in LANGUAGES:
        inspected = 0
        selected = []
        seen = set()
        for rank, word in enumerate(top_n_list(lang, rank_limit), 1):
            inspected += 1
            if (not 4 <= len(word) <= 16 or not script_ok(word, lang,
                        {lang: policy.packs[lang].description})
                    or keys_for(word, lang, tables) is None):
                continue
            normalized = policy.normalize(lang, word)
            if normalized in policy.words[lang] or normalized in seen:
                continue
            seen.add(normalized)
            selected.append({'word': word, 'rank': rank,
                             'zipf': zipf_frequency(word, lang)})
            if len(selected) == target + holdout:
                break
        if len(selected) != target + holdout:
            raise RuntimeError(f'Insufficient {lang} frequency OOV: {len(selected)}')
        result[lang] = selected[:target]
        heldout[lang] = selected[target:]
        meta[lang] = {'selected': target, 'heldout': holdout,
                      'last_inspected_rank': inspected,
                      'zipf_min': round(min(row['zipf'] for row in selected[:target]), 2),
                      'zipf_ge_3': sum(row['zipf'] >= 3 for row in selected[:target])}
        print('FREQUENCY', lang, meta[lang], flush=True)
    return result, heldout, meta


def split_wikipedia_oov(snapshot, policy, tables):
    # Split articles by a stable page id, BEFORE choosing rules or measuring.
    train = {lang: set() for lang in LANGUAGES}
    test_ids = set()
    stats = {}
    for lang in LANGUAGES:
        words = []
        for row in snapshot['languages'][lang]['titles']:
            word = row['title'].casefold()
            if (not script_ok(word, lang, {lang: policy.packs[lang].description})
                    or not 4 <= len(word) <= 18 or keys_for(word, lang, tables) is None
                    or policy.normalize(lang, word) in policy.words[lang]):
                continue
            words.append((word, row['pageid']))
        for word, pageid in words:
            if pageid % 3 == 0:
                train[lang].add(word)
            else:
                test_ids.add((lang, pageid))
        stats[lang] = {'train_names': len(train[lang]),
                       'holdout_names': len(words) - len(train[lang])}
    return train, test_ids, stats


class CharacterNgramModel:
    """Character 3-5 gram smoothed log likelihood for a single writing system.

    Counts come only from wordfreq lists, not from evaluation examples,
    personal data or Windows spelling. This is a diagnostic model, NOT a
    calibrated probability or a proof of lexical validity.
    """

    def __init__(self, words, *, ns=NGRAM_N):
        self.ns = ns
        self.grams = {n: Counter() for n in ns}
        self.contexts = {n: Counter() for n in ns}
        self.vocabulary = set()
        for word in words:
            self.vocabulary.update(word)
            for n in ns:
                padded = '^' * (n-1) + word + '$'
                for i in range(len(padded) - n + 1):
                    piece = padded[i:i+n]
                    self.grams[n][piece] += 1
                    self.contexts[n][piece[:-1]] += 1
        self.vsize = max(25, len(self.vocabulary) + 2)

    def score(self, word, *, n=4, alpha=.3):
        padded = '^' * (n-1) + word + '$'
        value, count = 0.0, 0
        for i in range(len(padded) - n + 1):
            gram = padded[i:i+n]
            numerator = self.grams[n][gram] + alpha
            denominator = self.contexts[n][gram[:-1]] + alpha * self.vsize
            value += math.log(numerator / denominator)
            count += 1
        return value / count if count else -100.0


def create_ngram_models(*, top=30000):
    models, metadata = {}, {}
    for lang in LANGUAGES:
        words = [word for word in top_n_list(lang, top)
                 if 3 <= len(word) <= 25 and word.isalpha()]
        models[lang] = CharacterNgramModel(words)
        metadata[lang] = {'source': 'wordfreq top_n_list, word-types unweighted',
                          'training_words': len(words),
                          'ngram_counts': {str(n): len(models[lang].grams[n])
                                           for n in NGRAM_N}}
    return models, metadata


def model_variants(base, languages, mapper, frequency_oov, wikipedia_names,
                   *, confident_min_zipf=3):
    def fresh():
        return create_policy({'words': base.words, 'confident': base.confident,
                              'exclusions': base.exclusions}, languages, mapper)
    policies = {'baseline': base}
    policies.update({name: fresh() for name in (
        'protect_only', 'all_confident', 'frequency_confident')})
    for lang in LANGUAGES:
        new_words = {row['word'] for row in frequency_oov[lang]}
        names = wikipedia_names[lang]
        for name in ('protect_only', 'all_confident', 'frequency_confident'):
            policies[name].words[lang].update(new_words | names)
        policies['all_confident'].confident[lang].update(new_words | names)
        policies['frequency_confident'].confident[lang].update(
            row['word'] for row in frequency_oov[lang]
            if row['zipf'] >= confident_min_zipf)
    return policies


def get_test_cases(snapshot, policies, tables, wikipedia_holdouts,
                   frequency_oov, frequency_holdouts,
                   *, sample_added=120):
    base = policies['baseline']
    # Pure, previous independent A/B cases; remove Wikipedia items used
    # to build the experimental name dictionary. Non-article noise kept.
    base.packs_as_catalog = {lang: base.packs[lang].description
                             for lang in LANGUAGES}
    primary, meta = make_samples(snapshot, base, tables,
                                 noise_per_language=240,
                                 typo_per_language=85, seed=SEED)
    items = []
    for item in primary:
        if item['kind'].startswith('attested_OOV'):
            target = item['expected'] if item['expected'] else item['source']
            key = (target, item['origin']['pageid'])
            if key not in wikipedia_holdouts:
                continue
        items.append({'source': item['source'], 'intended': item['expected'],
                      'physical': item['physical'],
                      'kind': item['kind'], 'word': item['word']})
    # Historical engine-strong positives and negatives from deterministic
    # other-seed old known words; no COM and no physical typing.
    controls = cases({'words': base.words, 'confident': base.confident,
                      'exclusions': base.exclusions}, tables,
                     per_direction=50, per_known=75, per_typo=75,
                     seed=20261101)
    for sample in controls:
        items.append({'source': sample.source, 'intended': sample.intended,
                      'physical': sample.physical,
                      'kind': 'known_'+sample.kind, 'word': sample.correct_word})
    # These are IN-SAMPLE positives derived from the source added to the
    # experimental dictionary: a coverage demonstration, NOT generalization.
    for lang in LANGUAGES:
        for entry in frequency_oov[lang][:sample_added]:
            word = entry['word']
            physical = keys_for(word, lang, tables)
            for source in LANGUAGES:
                if source == lang:
                    continue
                items.append({'source': source, 'intended': lang,
                              'physical': physical, 'kind': 'in_sample_added_word',
                              'word': word})
    for lang in LANGUAGES:
        for entry in frequency_holdouts[lang]:
            physical = keys_for(entry['word'], lang, tables)
            # A real frequency-attested but *unadded* source word should not
            # be changed just because a candidate in another language exists.
            items.append({'source': lang, 'intended': None,
                          'physical': physical, 'kind': 'frequency_heldout_source',
                          'word': entry['word']})
            for source in LANGUAGES:
                if source != lang:
                    items.append({'source': source, 'intended': lang,
                                  'physical': physical,
                                  'kind': 'frequency_heldout_wrong_layout',
                                  'word': entry['word']})
    return items, {'previous_holdout_metadata': meta,
                   'known_controls': len(controls),
                   'frequency_in_sample': sample_added * 2 * len(LANGUAGES),
                   'frequency_heldout_source': sum(len(x) for x in frequency_holdouts.values()),
                   'frequency_heldout_wrong_layout': 2 * sum(len(x) for x in frequency_holdouts.values())}


def measure(policies, items, tables, ngram, *, ratio_threshold=0.0,
            target_score_floor=None):
    score = defaultdict(lambda: defaultdict(Counter))
    mapper = next(iter(policies.values())).mapper
    for sample in items:
        keys = [(ord(ch.upper()), False) for ch in sample['physical']]
        source = sample['source']
        expected = sample['intended']
        forms = {lang: mapper.word(keys, lang) for lang in LANGUAGES}
        for name, policy in policies.items():
            chosen = policy.decide_token(keys, source)
            target = chosen.language if chosen is not None else None
            results = [('plain', target)]
            if chosen is not None:
                source_score = ngram[source].score(forms[source], n=4)
                target_score = ngram[target].score(forms[target], n=4)
                allowed = ((target_score - source_score >= ratio_threshold)
                           and (target_score_floor is None
                                or target_score >= target_score_floor))
            else:
                allowed = False
            results.append(('ngram_veto', target if allowed else None))
            for variant, answer in results:
                row = score[(name, variant)][sample['kind']]
                row['n'] += 1
                row['correct'] += int(answer == expected)
                row['switch'] += int(answer is not None)
                row['true_switch'] += int(expected is not None and answer == expected)
                row['false_switch'] += int(answer is not None and answer != expected)
                row['missed'] += int(expected is not None and answer != expected)
    return {name+'|'+variant: {kind: dict(stats) for kind, stats in kinds.items()}
            for (name, variant), kinds in score.items()}


def calibrate_threshold(policy, ngrams, tables, *, seed=20300101):
    """Select one ngram veto margin using a distinct calibration-only corpus.

    Constraint: preserve >=97% positive baseline decisions; then minimize
    negative random/noise switches. May select permissive -infinity margin.
    Never tune using primary evaluation cases.
    """
    mapper = policy.mapper
    from windows_spellcheck_benchmark import cases
    data = {'words': policy.words, 'confident': policy.confident,
            'exclusions': policy.exclusions}
    # Different seed, distinct sample from final evaluation.
    calibration = cases(data, tables, per_direction=40,
                        per_known=40, per_typo=40, seed=seed)
    rng = random.Random(seed)
    for lang in LANGUAGES:
        for _ in range(600):
            physical = ''.join(rng.choice('qwertyuiopasdfghjklzxcvbnm')
                               for __ in range(rng.randint(3, 10)))
            calibration.append(type('Cal', (), {'source':lang,
                                                 'physical':physical,
                                                 'kind':'random'})())
    observed = []
    for sample in calibration:
        keys = [(ord(ch.upper()), False) for ch in sample.physical]
        decision = policy.decide_token(keys, sample.source)
        if decision is None:
            continue
        target = decision.language
        delta = (ngrams[target].score(mapper.word(keys, target), n=4) -
                 ngrams[sample.source].score(mapper.word(keys, sample.source), n=4))
        observed.append((sample.kind, delta))
    baseline_true = sum(kind == 'wrong_layout' for kind, _ in observed)
    baseline_noise = sum(kind == 'random' for kind, _ in observed)
    feasible = []
    for threshold in (-1000.0, -3.0, -2.0, -1.0, -.5, 0.0, .5, 1.0, 1.5, 2.0):
        good = sum(kind == 'wrong_layout' and d >= threshold for kind,d in observed)
        noise = sum(kind == 'random' and d >= threshold for kind,d in observed)
        if baseline_true and good / baseline_true >= .97:
            feasible.append((noise, -threshold, threshold, good))
    if not feasible:
        return -1000.0, {'calibration_positive':baseline_true,
                         'calibration_noise_false_switches':baseline_noise,
                         'reason':'no_safe_threshold'}
    noise, _, threshold, good = min(feasible)
    return threshold, {'calibration_positive':baseline_true,
                       'calibration_noise_false_switches':baseline_noise,
                       'calibration_positive_retained':good,
                       'calibration_noise_remaining':noise,
                       'threshold':threshold}


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--output', type=Path, default=ROOT / 'build' /
                      'wordfreq-ngram-lexicon-ab-20261030.json')
    opts = args.parse_args()
    if opts.output.resolve().parent != ROOT / 'build' or opts.output.exists():
        args.error('Need nonexisting output inside project build/')
    snapshot = json.loads(TITLE_CORPUS.read_text(encoding='utf-8'))
    source_sha = hashlib.sha256(TITLE_CORPUS.read_bytes()).hexdigest()
    data, cache, catalog, languages = prepare_selected(
        ROOT / 'data', None, LANGUAGES, cache_dir=None)
    tables = keyboard_maps()
    mapper = SyntheticMapper(tables)
    baseline = create_policy(data, languages, mapper)
    frequency_oov, frequency_holdouts, freq_metadata = collect_frequency_oov(baseline, tables)
    names, holdouts, wiki_metadata = split_wikipedia_oov(snapshot, baseline, tables)
    policies = model_variants(baseline, languages, mapper, frequency_oov, names)
    models, ngram_metadata = create_ngram_models()
    threshold, calibration = calibrate_threshold(baseline, models, tables)
    items, fixture_metadata = get_test_cases(snapshot, policies, tables,
                                            holdouts, frequency_oov,
                                            frequency_holdouts)
    results = measure(policies, items, tables, models,
                      ratio_threshold=threshold)
    report = {
        'kind': 'TYPEBRIDGE_WORDFREQ_LEXICON_NGRAM_AB_V1',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'wordfreq_version':wordfreq_version,
        'lexical_source': 'wordfreq top_n_list(), word frequency estimates, no Wiktionary import',
        'wordfreq_license': 'Apache-2.0 code; CC-BY-SA-4.0 data; research-only isolated dependency',
        'wiki_source_sha256':source_sha,
        'wiki_split':wiki_metadata,
        'frequency_candidates':freq_metadata,
        'ngram_model':ngram_metadata,
        'calibrated_margin':calibration,
        'eval_sample_count':len(items),
        'eval_fixture':fixture_metadata,
        'safety_limitations': ('Synthetic standard physical keyboard, word-type ngrams '
           'trained on wordfreq (not calibrated), model-only no physical No01; '
           'in-sample added word cases are NOT generalization evidence; '
           'Wikimedia article-title OOV is not proof of spelling. '
           'Random-key non-actions are a test convention, not user intent.'),
        'results': results,
        'feature_policy':'No production source/EXE/config changes; no external lexicon redistribution',
    }
    opts.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n',
                           encoding='utf-8')
    print('SUMMARY', json.dumps({key:report[key] for key in
          ('frequency_candidates','wiki_split','calibrated_margin','eval_sample_count')},
          ensure_ascii=False),flush=True)
    for key in sorted(results):
        print(key, ' '.join(f'{kind}: {row["true_switch"]} T / {row["false_switch"]} F '
                            f'(n={row["n"]})' for kind,row in
                            results[key].items() if kind in
                            ('attested_OOV_wrong_layout','random_key_noise',
                             'curated_acronym','known_wrong_layout','in_sample_added_word')),
              flush=True)
    print('REPORT',opts.output.resolve(),flush=True)


if __name__=='__main__':
    main()
