"""Frozen RU/EN/HE decision baseline. No hooks, user text, COM or live HKL calls.

Replays a checked-in keyboard map through the real LanguageEngine wrapper.
Public titles are a REUSED diagnostic holdout, not fresh validation or prose.
"""
from collections import Counter, defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT), str(HERE)]
import engine as runtime

LANGUAGES = ('ru', 'en', 'he')
KEYMAP = HERE / 'fixtures/quality_keymap_20261106.json'
TITLES = HERE / 'fixtures/windows_spellcheck_wikipedia_titles_20261030.json'
SEED = 20261106


class FrozenMapper:
    def __init__(self, payload):
        if payload.get('kind') != 'READ_ONLY_LOADED_LAYOUT_KEYMAP':
            raise ValueError('Unexpected keymap provenance')
        self.tables = payload['tables']
        self.layouts = payload['layouts']

    def char(self, vk, language, shift=False):
        return self.tables[language].get(f'{vk}:{int(shift)}', '')

    def word(self, keys, language):
        return ''.join(self.char(vk, language, shift) for vk, shift in keys)

    def keys_for(self, text, language):
        inverse = {}
        # Prefer an unshifted representation, then stable virtual-key order.
        for encoded, char in sorted(self.tables[language].items(),
                key=lambda pair: tuple(reversed(tuple(map(int, pair[0].split(':')))))):
            vk, shift = map(int, encoded.split(':'))
            inverse.setdefault(char, (vk, bool(shift)))
        if not all(char in inverse for char in text):
            return None
        keys = tuple(inverse[char] for char in text)
        assert self.word(keys, language) == text
        return keys


def build_rows(snapshot, mapper):
    """Freeze labels from intended strings, never from the engine's answer."""
    if snapshot.get('kind') != 'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT':
        raise ValueError('Unexpected title provenance')
    rows, skipped = [], Counter()
    def add(identity, kind, intended, target, *, wrong=True, origin=None):
        keys = mapper.keys_for(intended, target)
        if not keys or not 1 <= len(keys) <= 40:
            skipped[kind] += 1
            return
        for source in LANGUAGES if wrong else (target,):
            rows.append(dict(id=f'{identity}:{source}', kind=kind, source=source,
                target=target, expected=intended, keys=keys, origin=origin))
    for lang in LANGUAGES:
        seen = set()
        for page in snapshot['languages'][lang]['titles']:
            word = page['title'].casefold()
            if page['pageid'] % 3 == 0:
                continue  # Previously designated training IDs stay excluded.
            if word in seen or not 4 <= len(word) <= 18:
                continue
            seen.add(word)
            add(f'title:{lang}:{page["pageid"]}', 'reused_title', word, lang,
                origin={'pageid': page['pageid'], 'url': page['source_url']})
    curated = {
        'ru': ('привет проверка время сегодня завтра работа музыка программа '
               'словарь компьютер сообщение настройки нейросеть квантование '
               'приветствие яблоко белый еще').split(),
        'en': ('hello world time today tomorrow keyboard layout message settings '
               'computer music dictionary language correct').split(),
        'he': ('שלום תודה בבקשה עכשיו היום מחר עבודה מוזיקה מחשב תוכנה '
               'מילה שפה זמן בדיקה ירושלים').split(),
    }
    protected = {
        'en': ('Alice David Slava Spotify OpenAI GitHub Python Docker NASA UNESCO '
               'NATO HTTP HTML SQL PDF GPS USB CPU GPU SSD RAM URL XML JSON API '
               'DNS TCP UDP JPEG FBI CIA FAQ tot ye xkcd xyzzy').split()
               + ['foo_bar', 'hello123', 'https://example.com', 'user@example.com',
                  'TypeBridge.exe', 'foo.bar', 'Ctrl+Shift+V'],
        'ru': ('Слава Москва Израиль Петах-Тиква ООН НАТО МГУ МФТИ РАН СССР '
               'ГИБДД МЧС ДНК РНК ГМО БРИКС СМИ НИИ').split(),
        'he': ['דוד', 'ישראל', 'ירושלים', 'צה"ל', 'בג"ץ', 'שב"כ', 'נתב"ג',
               'רש"י', 'קק"ל', 'תנ"ך', 'חו"ל', 'רה"מ'],
    }
    for lang, words in curated.items():
        for index, word in enumerate(words):
            add(f'curated:{lang}:{index}', 'curated_word', word, lang)
    for lang, words in protected.items():
        for index, word in enumerate(words):
            add(f'protected:{lang}:{index}', 'curated_keep', word, lang, wrong=False)
    for lang, words in {'en': ['hello,', '(hello)', 'Hello', 'HELLO'],
                        'ru': ['привет,', '(привет)', 'Привет', 'ПРИВЕТ'],
                        'he': ['שלום,', '(שלום)']}.items():
        for index, word in enumerate(words):
            add(f'punct:{lang}:{index}', 'curated_punctuation_case', word, lang)
    rng = random.Random(SEED)
    for lang in LANGUAGES:
        for index in range(100):
            keys = tuple((rng.randrange(65, 91), False)
                         for _ in range(rng.randint(3, 12)))
            typed = mapper.word(keys, lang)
            rows.append(dict(id=f'noise:{lang}:{index}', kind='synthetic_noise',
                source=lang, target=lang, expected=typed, keys=keys, origin={'seed': SEED}))
    return rows, dict(skipped)


def classify(typed, actual, source, destination, expected, target):
    if actual == expected and destination == target:
        return 'correct'
    if typed == expected and source == target:
        return 'false_switch'
    return 'miss' if destination == source and actual == typed else 'wrong_replacement'


def evaluate(rows, engine):
    records = []
    groups = defaultdict(Counter)
    delays = defaultdict(list)
    for conservative in (True, False):
        engine.conservative = conservative
        mode = 'conservative' if conservative else 'extended'
        engine.decide_token([(65, False)], 'en')  # Unscored warm-up.
        for row in rows:
            source, target, keys = row['source'], row['target'], row['keys']
            typed = engine.mapper.word(keys, source)
            started = time.perf_counter_ns()
            decision = engine.decide_token(keys, source)
            elapsed = (time.perf_counter_ns()-started)/1_000_000
            actual = decision.replacement if decision else typed
            destination = decision.language if decision else source
            verdict = classify(typed, actual, source, destination, row['expected'], target)
            reason = decision.reason if decision else engine.diagnose_token(
                keys, source, exact_policy=True).reason
            known_target = engine._policy_engine.known(target, row['expected'])
            confident_target = engine._policy_engine.certain(target, row['expected'])
            source_collision = source != target and engine._policy_engine.known(source, typed)
            group = (mode, row['kind'], source, target)
            groups[group]['n'] += 1
            groups[group][verdict] += 1
            delays[mode].append(elapsed)
            records.append(dict(**row, mode=mode, typed=typed, actual=actual,
                destination=destination, verdict=verdict, reason=reason,
                known_target=known_target, confident_target=confident_target,
                source_collision=source_collision))
    return records, [dict(zip(('mode','kind','source','target'), key), **value)
                     for key, value in sorted(groups.items())], {
                         mode: {'n': len(values),
                            'p95_ms': sorted(values)[int((len(values)-1)*.95)]}
                         for mode, values in delays.items()}


def hashes(paths):
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))}


def compare(baseline, report):
    if baseline['corpus_sha256'] != report['corpus_sha256']:
        raise ValueError('Cannot compare different corpora')
    previous = {(row['mode'], row['id']): row for row in baseline['outcomes']}
    current = {(row['mode'], row['id']): row for row in report['records']}
    if set(previous) != set(current):
        raise ValueError('Baseline cases or modes do not match')
    regressions, improvements = [], []
    for key, row in current.items():
        old = previous[key]
        entry = dict(mode=key[0], id=key[1], before=old['actual'], after=row['actual'])
        if old['verdict'] == 'correct' and row['verdict'] != 'correct':
            regressions.append(entry)
        elif old['verdict'] != 'correct' and row['verdict'] == 'correct':
            improvements.append(entry)
    return dict(regressions=regressions, improvements=improvements,
                no_new_regressions=not regressions)


def run():
    mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
    rows, skipped = build_rows(json.loads(TITLES.read_text(encoding='utf-8')), mapper)
    # The actual wrapper/policy and bundled dictionaries; never instantiate the
    # real Win32 mapper or consult settings/user_lexicon directories.
    with patch.object(runtime, 'LayoutMapper', return_value=mapper):
        engine = runtime.LanguageEngine(ROOT/'data', conservative=True,
                                       active_languages=LANGUAGES)
    paths = [KEYMAP, TITLES, Path(__file__), ROOT/'engine.py',
             ROOT/'language_decision_v2.py', ROOT/'language_unified_runtime.py',
             ROOT/'data/exclude.txt']
    for language in engine.language_policies.values():
        paths.extend(p for p in language.directory.iterdir() if p.is_file())
        for addition in language.additions:
            if addition.editable_source and (ROOT/'data'/addition.filename).is_file():
                paths.append(ROOT/'data'/addition.filename)
    before = hashes(paths)
    records, groups, timings = evaluate(rows, engine)
    if before != hashes(paths):
        raise ValueError('Inputs changed during evaluation')
    corpus_sha256 = hashlib.sha256(json.dumps(rows, sort_keys=True,
        ensure_ascii=False).encode('utf-8')).hexdigest()
    return dict(scope='offline decisions, fixed mapped keys; no real typing or early mode',
        caveat='reused Wikipedia title holdout + curated and synthetic diagnostics; not population accuracy',
        languages=LANGUAGES, seed=SEED, case_count=len(rows), skipped=skipped,
        corpus_sha256=corpus_sha256, sha256=before, timings=timings,
        groups=groups, records=records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    if args.baseline and args.output.resolve() == args.baseline.resolve():
        parser.error('Output must not overwrite the frozen baseline')
    report = run()
    if args.baseline:
        report['comparison'] = compare(json.loads(args.baseline.read_text(encoding='utf-8')), report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(cases=report['case_count'], skipped=report['skipped'],
        totals={mode: dict(Counter(r['verdict'] for r in report['records'] if r['mode']==mode))
                for mode in report['timings']}, timings=report['timings'],
        comparison=report.get('comparison')), ensure_ascii=True))


if __name__ == '__main__':
    main()
