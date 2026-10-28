"""Bounded TXT/JSON exclusion imports. No network, execution or disk writes."""
from dataclasses import dataclass
import copy
import json
from pathlib import Path
import re
import sys

from user_lexicon import normalize

MAX_BYTES = 2_000_000
MAX_ENTRIES = 50_000
PACK_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'dictionary_packs'
BUILTINS = (('programming.json', 'Python / JavaScript / C++'), ('gaming.json', 'Игровые термины'))


@dataclass(frozen=True)
class Entry:
    language: str
    word: str
    sensitive: bool = False


@dataclass(frozen=True)
class ImportPreview:
    name: str
    entries: tuple[Entry, ...]
    duplicates: int
    skipped: tuple[tuple[int, str], ...]


def detect_language(word):
    for lang in ('ru', 'en', 'he'):
        try:
            normalize(word, lang)
            return lang
        except ValueError:
            pass
    raise ValueError('Поддерживаются отдельные слова RU/EN/HE, без цифр и символов')


def parse_import(raw, *, filename='dictionary.txt', language='auto', sensitive=False):
    if len(raw) > MAX_BYTES:
        raise ValueError('Словарь больше 2 МБ')
    if language not in ('auto', 'ru', 'en', 'he') or type(sensitive) is not bool:
        raise ValueError('Некорректный язык или режим регистра')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError as error:
        raise ValueError('Нужен текстовый файл UTF-8') from error
    name = Path(filename).name
    records = []
    if filename.casefold().endswith('.json'):
        try:
            doc = json.loads(text)
        except (ValueError, RecursionError) as error:
            raise ValueError('Некорректный JSON') from error
        if (not isinstance(doc, dict) or doc.get('format') != 'typebridge-exclusions'
                or type(doc.get('version')) is not int or doc['version'] != 1
                or not isinstance(doc.get('entries'), list)):
            raise ValueError('Нужен JSON формата typebridge-exclusions, version 1')
        name = doc.get('name', name)
        if not isinstance(name, str) or len(name) > 160:
            raise ValueError('Некорректное название словаря')
        if len(doc['entries']) > MAX_ENTRIES:
            raise ValueError('В словаре больше 50 000 записей')
        for index, item in enumerate(doc['entries'], 1):
            if (not isinstance(item, dict) or not isinstance(item.get('word'), str)
                    or item.get('language') not in ('ru', 'en', 'he')
                    or type(item.get('sensitive', False)) is not bool):
                raise ValueError(f'Некорректная структура записи {index}')
            records.append((index, item['word'], item['language'], item.get('sensitive', False)))
    else:
        lines = text.splitlines()
        if len(lines) > MAX_ENTRIES:
            raise ValueError('В словаре больше 50 000 строк')
        for index, line in enumerate(lines, 1):
            line = line.strip()
            # Fixed-width lookbehind: the old \s+# retried an entire run of
            # whitespace from each position when no comment followed (O(n²)).
            word = '' if line.startswith('#') else re.split(r'(?<=\s)#', line, maxsplit=1)[0].rstrip()
            if word:
                records.append((index, word, language, sensitive))
    entries, seen, skipped, duplicates = [], set(), [], 0
    for index, word, lang, exact in records:
        try:
            lang = detect_language(word) if lang == 'auto' else lang
            value = normalize(word, lang, sensitive=exact)
        except ValueError:
            skipped.append((index, 'Не отдельное слово выбранного языка (1–80 букв)'))
            continue
        entry = Entry(lang, value, exact)
        if entry in seen:
            duplicates += 1
            continue
        seen.add(entry)
        entries.append(entry)
    return ImportPreview(name, tuple(entries), duplicates, tuple(skipped))


def read_import(path, **options):
    path = Path(path)
    if not path.is_file():
        raise ValueError('Выбери обычный файл словаря')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    return parse_import(raw, filename=path.name, **options)


def merge_exclusions(lexicon, preview):
    """Return a new snapshot; caller saves atomically before publishing it."""
    result = copy.deepcopy(lexicon)
    added = existing = 0
    for entry in preview.entries:
        # An insensitive existing exception also covers exact-case imports.
        folded = result._bucket('exclusions', entry.language, False)
        exact = result._bucket('exclusions', entry.language, entry.sensitive)
        if entry.word.casefold() in folded or entry.word in exact:
            existing += 1
            continue
        result.add('exclusions', entry.language, entry.word, sensitive=entry.sensitive)
        added += 1
    result._serialize()  # Check limits before any destination write.
    return result, added, existing
