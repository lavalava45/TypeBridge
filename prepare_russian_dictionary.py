"""Prepare an isolated RU candidate from pinned FrequencyWords and Lebedev data.

Does not install dictionaries or change the running application. The suffix
expander deliberately supports only the grammar of the pinned Russian snapshot;
unrecognized Hunspell features fail instead of silently generating wrong words.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request

from prepare_dictionaries import LIMITS, SOURCE_ROOT, select_words

HUNSPELL_COMMIT = '8cfea406b505e4d7df52d5a19bce525df98c54ab'
HUNSPELL_ROOT = f'https://raw.githubusercontent.com/wooorm/dictionaries/{HUNSPELL_COMMIT}/dictionaries/ru'
INPUTS = {
    'index.dic': (f'{HUNSPELL_ROOT}/index.dic', 'f6047416a0204adbecf3a451b874ec8a97ee37e2cbc714466ef04d8dbcc0d6fc'),
    'index.aff': (f'{HUNSPELL_ROOT}/index.aff', '38ce7d4af78e211e9bafe4bf7e3d6a2c420591136cb738ec6648f8fdf6524cd7'),
    'Lebedev-LICENSE.txt': (f'{HUNSPELL_ROOT}/license', '262af2f6ad70a61e5ee1332ff44fa8ee50edca819cf33207d8ad6ba6a0c9be52'),
    'ru_full.txt': (f'{SOURCE_ROOT}/ru/ru_full.txt', '32dfd94138aea2667ec455dcc8512029e39bd4b8ea70b09410d569b69d511a30'),
}
RUSSIAN = re.compile(r'[а-яё]{1,40}\Z')


def expand_suffixes(dictionary, affixes):
    groups = {}
    expected = {}
    for line in affixes.decode('utf-8-sig').splitlines():
        parts = line.split()
        if not parts or parts[0].startswith('#'):
            continue
        if parts[0] == 'SET' and parts == ['SET', 'UTF-8']:
            continue
        if parts[0] == 'TRY':
            continue  # Suggestion order does not change accepted word forms.
        if parts[0] != 'SFX':
            raise ValueError('Unsupported Hunspell directive: ' + parts[0])
        if len(parts) == 4 and parts[2] in ('Y', 'N'):
            flag = parts[1]
            if len(flag) != 1 or flag in groups:
                raise ValueError('Unsupported or duplicate suffix flag')
            groups[flag] = []
            expected[flag] = int(parts[3])
        elif len(parts) == 5 and parts[1] in groups:
            _, flag, strip, add, condition = parts
            if '/' in add:
                raise ValueError('Continuation classes are unsupported')
            groups[flag].append(('' if strip == '0' else strip,
                                 '' if add == '0' else add,
                                 re.compile('(?:' + condition + ')$')))
        else:
            raise ValueError('Unsupported suffix rule: ' + line)
    if any(len(groups[f]) != count for f, count in expected.items()):
        raise ValueError('Suffix rule count mismatch')
    lines = dictionary.decode('utf-8-sig').splitlines()
    if not lines or int(lines[0]) != len(lines) - 1:
        raise ValueError('Dictionary entry count mismatch')
    result = set()

    def retain(word):
        word = word.casefold()
        if RUSSIAN.fullmatch(word):
            result.add(word)

    for entry in lines[1:]:
        if len(entry.split()) != 1:
            raise ValueError('Morphological fields are unsupported')
        word, separator, flags = entry.partition('/')
        retain(word)
        for flag in flags:
            if flag not in groups:
                raise ValueError('Unknown dictionary flag: ' + flag)
            for strip, add, condition in groups[flag]:
                if condition.search(word) and (not strip or word.endswith(strip)):
                    retain((word[:-len(strip)] if strip else word) + add)
    return result


def prepare(output_dir, source_dir=None):
    if output_dir.exists():
        raise ValueError('Choose a new output directory; existing files are never overwritten')
    raw = {}
    for name, (url, digest) in INPUTS.items():
        cached = source_dir / name if source_dir is not None else None
        if cached is not None and cached.is_file():
            raw[name] = cached.read_bytes()
        else:
            print('Downloading ' + name, flush=True)
            with urllib.request.urlopen(url, timeout=60) as stream:
                raw[name] = stream.read()
        if hashlib.sha256(raw[name]).hexdigest() != digest:
            raise ValueError('Source hash mismatch: ' + name)
    frequency = select_words(raw['ru_full.txt'], 'ru')
    supplement = set('аивкосуя')
    frequent_words = set(frequency) | supplement
    confident = set(sorted(frequency, key=lambda w: (-frequency[w], w))[:LIMITS['ru']]) | supplement
    orthographic = expand_suffixes(raw['index.dic'], raw['index.aff'])
    combined = frequent_words | orthographic
    report = {
        'status': 'generated; runtime installation is a separate operation',
        'sources': {name: {'url': url, 'sha256': digest} for name, (url, digest) in INPUTS.items()},
        'modifications': ['expand pinned Hunspell suffix rules', 'casefold', 'retain Russian letters, length 1..40',
                          'union with filtered FrequencyWords', 'confidence remains frequency-derived'],
        'counts': {'frequency': len(frequent_words), 'hunspell': len(orthographic),
                   'shared': len(frequent_words & orthographic), 'combined': len(combined),
                   'confident': len(confident), 'added_to_frequency': len(orthographic - frequent_words)},
        'examples': {w: {'frequency': w in frequent_words, 'hunspell': w in orthographic,
                         'confident': w in confident}
                     for w in ('поясни', 'пояснить', 'белый', 'ещё', 'еще', 'ну', 'да', 'не', 'абажурами')},
    }
    output_dir.mkdir(parents=True)
    (output_dir / 'sources').mkdir()
    for name, content in raw.items():
        (output_dir / 'sources' / name).write_bytes(content)
    for name, words in [('ru.txt', combined), ('confident_ru.txt', confident),
                        ('frequency-only.txt', frequent_words), ('hunspell-only.txt', orthographic)]:
        (output_dir / name).write_text('\n'.join(sorted(words)) + '\n', encoding='utf-8', newline='\n')
    (output_dir / 'RU-SOURCES.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'RU-DATA-LICENSE.txt').write_text(
        'Combined/modified data: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/\n'
        'Frequency component: Hermit Dave / FrequencyWords; OpenSubtitles 2018 contributors.\n'
        'Orthographic component: Copyright (c) 1997-2008, Alexander I. Lebedev; '
        '2012 modifications by Laszlo Nemeth. Original notice and conditions are retained below.\n'
        'Changes: suffix expansion, filtering, casefolding, deduplication and union; '
        'confidence selected only from FrequencyWords. No author endorsement implied.\n'
        'Exact inputs and transformations: RU-SOURCES.json and sources/.\n\n'
        + raw['Lebedev-LICENSE.txt'].decode('utf-8-sig'), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = prepare(args.output_dir.resolve())
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(report['counts'], indent=2))


if __name__ == '__main__':
    main()
