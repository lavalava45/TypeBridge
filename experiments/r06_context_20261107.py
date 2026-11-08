"""Conditional left-context experiment. NOT imported by the application.

Only fixed public/test strings, bundled dictionaries and a frozen keyboard map.
No hooks, field reads, live HKLs, language models, training or user settings.
"""
from collections import Counter, deque
from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unicodedata
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT), str(HERE)]
from layout_quality_20261106 import FrozenMapper, KEYMAP, classify, hashes
import engine as runtime

FIXTURE = HERE / 'fixtures/r06_context_cases_20261107.json'
EVENTS = ('click', 'navigation', 'window', 'field', 'layout', 'selection',
          'backspace', 'undo', 'paste', 'composition', 'stop', 'idle')
VARIANTS = ('baseline', 'keep_known_source', 'allow_known_source_override')


class ContextWindow:
    """Two language votes in memory, never the previous words themselves."""
    def __init__(self, policy):
        self.policy = policy
        self.votes = deque(maxlen=2)
        self.scope = None
        self.layout = None

    def reset(self, event):
        if event not in EVENTS:
            raise ValueError('Unrecognized context reset')
        self.votes.clear()

    def bind(self, scope, layout, now):
        if (scope != self.scope or layout != self.layout or
                self.votes and not 0 <= now - self.votes[0][1] <= 10):
            self.votes.clear()
        self.scope, self.layout = scope, layout

    def observe(self, word, scope, layout, now):
        self.bind(scope, layout, now)
        languages = [lang for lang in self.policy.active
                     if word.isalpha() and 2 <= len(word) <= 40
                     and self.policy.known(lang, word)
                     and not self.policy.excluded(lang, word)]
        if len(languages) != 1:
            self.votes.clear()
        else:
            self.votes.append((languages[0], now))

    def hint(self, scope, layout, now):
        self.bind(scope, layout, now)
        if len(self.votes) == 2 and self.votes[0][0] == self.votes[1][0]:
            return self.votes[0][0]
        return None


@dataclass(frozen=True)
class ContextAnswer:
    language: str
    replacement: str
    reason: str = 'experimental-two-word-context'


def propose(policy, keys, source, baseline, history, *, scope, now, override_known=False):
    hint = history.hint(scope, source, now)
    if hint is None:
        return baseline, 'no-context-hint'
    typed = policy.mapper.word(keys, source)
    if (not 1 <= len(keys) <= 40 or len(typed) != len(keys)
            or not policy.valid_typed(keys, typed, source)
            or policy.excluded(source, typed)):
        return baseline, 'source-not-eligible'
    if policy.known(source, typed) and not override_known:
        return baseline, 'known-source-kept'
    candidates = {}
    for lang in policy.active:
        word = policy.mapper.word(keys, lang)
        if (len(word) == len(keys) and word.isalpha() and policy.one_script(word)
                and policy.known(lang, word)):
            if policy.excluded(lang, word):
                return baseline, 'excluded-candidate'
            if lang == source or not policy.conservative or policy.certain(lang, word):
                candidates[lang] = word
    if hint not in candidates:
        return baseline, 'hint-has-no-eligible-candidate'
    if hint == source:
        return None, 'context-keeps-source'
    return ContextAnswer(hint, candidates[hint]), 'context-candidate'


def outcome(decision, typed, source):
    return (decision.replacement, decision.language) if decision else (typed, source)


def feed(history, words, source, *, scope=('A', 'field-A')):
    for index, word in enumerate(words):
        history.observe(word, scope, source, 100 + index)
    return 100 + len(words)


def run():
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    if fixture['kind'] != 'CURATED_CONDITIONAL_CONTEXT_DIAGNOSTIC':
        raise ValueError('Unexpected corpus provenance')
    mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
    with patch.object(runtime, 'LayoutMapper', return_value=mapper):
        engine = runtime.LanguageEngine(ROOT/'data', active_languages=('ru','en','he'))
    policy = engine._policy_engine
    paths = [Path(__file__), FIXTURE, KEYMAP, ROOT/'engine.py',
             HERE/'layout_quality_20261106.py', ROOT/'language_registry.py',
             ROOT/'language_pack_v2.py', ROOT/'language_pack_loader.py',
             ROOT/'language_decision_v2.py', ROOT/'language_unified_runtime.py',
             ROOT/'data/exclude.txt']
    for pack in engine.language_policies.values():
        paths.extend(p for p in pack.directory.iterdir() if p.is_file())
        paths.extend(ROOT/'data'/add.filename for add in pack.additions
                     if add.editable_source and (ROOT/'data'/add.filename).is_file())
    before = hashes(paths)
    records, resets = [], []
    for conservative in (True, False):
        engine.conservative = conservative
        mode = 'conservative' if conservative else 'extended'
        for case in fixture['cases']:
            keys = mapper.keys_for(case['physical'], 'en')
            if not keys:
                raise ValueError('Unmapped test keys: '+case['id'])
            baseline = engine.decide_token(keys, case['source'])
            typed = mapper.word(keys, case['source'])
            for variant in VARIANTS:
                history = ContextWindow(policy)
                now = feed(history, case['context'], case['source'])
                hint = history.hint(('A','field-A'), case['source'], now)
                decision, reason = (baseline, 'baseline') if variant == 'baseline' else propose(
                    policy, keys, case['source'], baseline, history,
                    scope=('A','field-A'), now=now,
                    override_known=variant == 'allow_known_source_override')
                actual, language = outcome(decision, typed, case['source'])
                records.append(dict(**case, mode=mode, variant=variant, hint=hint,
                    typed=typed, actual=actual, destination=language, reason=reason,
                    verdict=classify(typed, actual, case['source'], language,
                                     case['expected'], case['target'])))
        keys = mapper.keys_for('njr', 'en')
        baseline = engine.decide_token(keys, 'en')
        for event in fixture['reset_events']:
            for variant in VARIANTS[1:]:
                history = ContextWindow(policy)
                now = feed(history, ['אני', 'קונה'], 'en')
                if history.hint(('A','field-A'), 'en', now) != 'he':
                    raise ValueError('Reset probe lacks initial context')
                if event == 'idle':
                    now += 11
                else:
                    history.reset(event)
                decision, reason = propose(policy, keys, 'en', baseline, history,
                    scope=('A','field-A'), now=now,
                    override_known=variant == 'allow_known_source_override')
                resets.append(dict(mode=mode, variant=variant, event=event,
                    passed=outcome(decision, mapper.word(keys,'en'), 'en') ==
                           outcome(baseline, mapper.word(keys,'en'), 'en')
                           and history.hint(('A','field-A'),'en',now) is None))
    grouped = {}
    for row in records:
        group = '|'.join((row['mode'],row['split'],row['variant']))
        grouped.setdefault(group, Counter())[row['verdict']] += 1
    baseline = {(row['mode'],row['id']):row for row in records if row['variant']=='baseline'}
    comparisons = []
    for mode in ('conservative','extended'):
        for variant in VARIANTS[1:]:
            rows = [row for row in records if row['mode']==mode and row['variant']==variant]
            regressions = [row['id'] for row in rows if baseline[mode,row['id']]['verdict']=='correct'
                           and row['verdict']!='correct']
            gains = [row['id'] for row in rows if baseline[mode,row['id']]['verdict']!='correct'
                     and row['verdict']=='correct']
            comparisons.append(dict(mode=mode,variant=variant,regressions=regressions,gains=gains,
                fixture_gate_passed=not regressions and bool(gains)
                    and all(row['passed'] for row in resets)))
    if hashes(paths) != before:
        raise ValueError('Sources changed during experiment')
    return dict(scope=fixture['provenance'],rules=fixture['rules_predeclared'],
        python_version=sys.version.split()[0],unicode_version=unicodedata.unidata_version,
        case_count=len(fixture['cases']),sha256=before,groups=grouped,
        comparisons=comparisons,reset_probes=resets,records=records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    report = run()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(cases=report['case_count'],groups=report['groups'],
        comparisons=report['comparisons'],reset_passed=sum(r['passed'] for r in report['reset_probes']),
        reset_total=len(report['reset_probes'])),ensure_ascii=True))


if __name__ == '__main__':
    main()
