"""Independent user lexicon, confined to an explicitly supplied directory.

Atomic whole-file snapshots. Thread-safe within one process; concurrent writers
from independent processes need an external transaction lock to avoid lost updates.
No SwitchER files or application settings are read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import unicodedata

_LANGS = ("ru", "en", "he")
_KINDS = ("words", "exclusions")
_MODES = ("insensitive", "sensitive")
_FILENAME = "user_lexicon.json"
_PACKAGES_FILENAME = "user_lexicon_packages.json"
_MAX_BYTES = 2_000_000
_MAX_WORDS = 50_000
_LOCK = threading.RLock()
_PACK_CODE = re.compile(r'[a-z]{2,3}(?:-[a-z]{2,4})?', re.ASCII)


class LexiconLoadError(ValueError):
    """Location and controlled reason only; never include a personal word."""
    def __init__(self, path, location, reason, digest=None):
        self.path, self.location, self.reason, self.digest = Path(path), location, reason, digest
        super().__init__(f'Личный словарь: {self.path}\nМесто ошибки: {location}\nПричина: {reason}')


def recover_lexicon(error):
    """Explicit recovery only: verify a complete backup BEFORE replacing data."""
    if not isinstance(error, LexiconLoadError) or error.digest is None:
        raise ValueError('Автовосстановление недоступно: сохрани файл словаря вручную')
    with _LOCK:
        folder, target = _check_dir(error.path.parent)
        with target.open('rb') as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES or hashlib.sha256(raw).hexdigest() != error.digest:
            raise ValueError('Файл словаря изменился; перезапусти программу для повторной проверки')
        with tempfile.NamedTemporaryFile(dir=folder, prefix='user_lexicon-', suffix='.bad', delete=False) as stream:
            backup = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if hashlib.sha256(backup.read_bytes()).hexdigest() != error.digest:
            raise OSError('Не удалось проверить резервную копию; исходный словарь сохранён')
        _check_dir(folder)
        with target.open('rb') as stream:
            current = stream.read(_MAX_BYTES + 1)
        if current != raw:
            raise ValueError('Файл словаря изменился; перезапусти программу для повторной проверки')
        result = UserLexicon()
        result.save(folder)
        return result, backup


def normalize(word: str, language: str, *, sensitive: bool = False, catalog=None) -> str:
    known = _LANGS if catalog is None else catalog
    if language not in known or type(sensitive) is not bool or not isinstance(word, str):
        raise ValueError("invalid word/language/mode")
    value = unicodedata.normalize("NFC", word)
    if not 1 <= len(value) <= 80 or not value.isalpha():
        raise ValueError("word must contain 1..80 letters only")
    # New verified v2 packs declare their personal alphabet policy in the
    # manifest. A catalog entry carrying that policy is authoritative even
    # when its code happens to match a legacy reserved identity. Older v1
    # descriptions do not carry it and retain the exact original rules.
    policy = (getattr(catalog[language], 'personal_alphabet_policy', None)
              if catalog is not None else None)
    if policy == 'ascii_latin_letters' or (policy is None and language == 'en'):
        allowed = all("A" <= ch <= "Z" or "a" <= ch <= "z" for ch in value)
    elif policy == 'russian_letters' or (policy is None and language == 'ru'):
        allowed = all("А" <= ch <= "я" or ch in "Ёё" for ch in value)
    elif policy == 'hebrew_letters' or (policy is None and language == 'he'):
        allowed = all("\u05d0" <= ch <= "\u05ea" for ch in value)
    elif policy in (None, 'unicode_script_name'):
        # External v1 metadata has no personal policy and remains script
        # restricted by its original substring rule for compatibility with
        # previously saved v1 words. Verified v2 uses a stricter Unicode-name
        # script prefix; never reinterpret historical user data on upgrade.
        script = catalog[language].unicode_script
        if policy is None:
            allowed = all(script in unicodedata.name(ch, '') for ch in value)
        else:
            allowed = all(unicodedata.name(ch, '').startswith(script + ' ') for ch in value)
    else:
        # A policy unknown to this binary must fail closed, not become an
        # unrestricted Unicode personal-word entry.
        raise ValueError('unsupported personal alphabet policy')
    if not allowed:
        raise ValueError("word contains characters outside target language alphabet")
    return value if sensitive else value.casefold()


def _empty():
    return {kind: {lang: {mode: set() for mode in _MODES} for lang in _LANGS}
            for kind in _KINDS}


def _check_dir(folder: Path):
    folder = Path(folder)
    if not folder.is_absolute() or ".." in folder.parts:
        raise ValueError("absolute directory without parent traversal required")
    for part in (folder, *folder.parents):
        if part.is_symlink():
            raise ValueError("symlink directory refused")
    if not folder.exists():
        raise ValueError("directory must already exist")
    if not folder.is_dir():
        raise ValueError("directory must already exist")
    target = folder / _FILENAME
    if target.is_symlink():
        raise ValueError("symlink target refused")
    if target.exists() and not stat.S_ISREG(target.stat(follow_symlinks=False).st_mode):
        raise ValueError("target must be regular file")
    return folder, target


@dataclass
class UserLexicon:
    data: dict = field(default_factory=_empty)
    catalog: dict | None = field(default=None, repr=False, compare=False)
    # Records for unavailable/invalid *inactive* packages are retained as
    # opaque personal data. They are never exposed to the engine or allowed
    # through add()/contains() until that package is valid again.
    unavailable: dict = field(default_factory=lambda: {kind: {} for kind in _KINDS},
                              repr=False, compare=False)

    def _languages(self):
        return _LANGS if self.catalog is None else self.catalog

    def _bucket(self, kind: str, language: str, sensitive: bool, *, create=True):
        if kind not in _KINDS or language not in self._languages() or type(sensitive) is not bool:
            raise ValueError("invalid bucket")
        if language not in self.data[kind]:
            if not create:
                return frozenset()
            self.data[kind][language] = {mode: set() for mode in _MODES}
        return self.data[kind][language]["sensitive" if sensitive else "insensitive"]

    def add(self, kind: str, language: str, word: str, *, sensitive: bool = False) -> bool:
        value = normalize(word, language, sensitive=sensitive, catalog=self.catalog)
        bucket = self._bucket(kind, language, sensitive)
        if len(bucket) >= _MAX_WORDS and value not in bucket:
            raise ValueError("lexicon limit")
        previous = len(bucket)
        bucket.add(value)
        return len(bucket) != previous

    def remove(self, kind: str, language: str, word: str, *, sensitive: bool = False) -> bool:
        value = normalize(word, language, sensitive=sensitive, catalog=self.catalog)
        bucket = self._bucket(kind, language, sensitive)
        if value not in bucket:
            return False
        bucket.remove(value)
        return True

    def contains(self, kind: str, language: str, word: str) -> bool:
        folded = normalize(word, language, catalog=self.catalog)
        exact = normalize(word, language, sensitive=True, catalog=self.catalog)
        return (folded in self._bucket(kind, language, False, create=False)
                or exact in self._bucket(kind, language, True, create=False))

    def engine_view(self) -> tuple[dict[str, set[str]], set[str]]:
        """Compatibility data for engine.words and engine.exclusions (casefold only).

        Case-sensitive entries are intentionally NOT collapsed: LanguageEngine
        uses contains() separately for exact-case rules. This lossy compatibility
        view is not a substitute for passing UserLexicon to LanguageEngine.
        """
        words = {lang: set(items["insensitive"])
                 for lang, items in self.data["words"].items()}
        exclusions = set().union(*(items["insensitive"]
                                   for items in self.data["exclusions"].values()))
        return words, exclusions

    def _serialize(self) -> bytes:
        # Preserve EXACT legacy schema. External words live in a separate
        # sidecar so older installed TypeBridge binaries can still open v1.
        doc = {"version": 1, "entries": {
            kind: {lang: {mode: sorted(self.data[kind].get(lang, {}).get(mode, ()))
                          for mode in _MODES}
                   for lang in _LANGS} for kind in _KINDS}}
        raw = (json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
        if len(raw) > _MAX_BYTES:
            raise ValueError("file size limit")
        return raw

    def _serialize_packages(self) -> bytes | None:
        external = ({lang for kind in _KINDS for lang in self.data[kind]
                     if lang not in _LANGS}
                    | {lang for kind in _KINDS for lang in self.unavailable[kind]})
        if not external:
            return None
        live = {lang for kind in _KINDS for lang in self.data[kind]
                if lang not in _LANGS}
        if self.catalog is None or not live <= set(self.catalog):
            raise ValueError('Unknown language in personal dictionary packages')
        document = {"version": 1, "entries": {
            kind: {lang: {mode: (list(self.unavailable[kind][lang][mode])
                                  if lang in self.unavailable[kind] else
                                  sorted(self.data[kind].get(lang, {}).get(mode, ())))
                          for mode in _MODES} for lang in sorted(external)}
            for kind in _KINDS}}
        raw = (json.dumps(document, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')) + '\n').encode('utf-8')
        if len(raw) > _MAX_BYTES:
            raise ValueError('file size limit')
        return raw

    @staticmethod
    def _write_snapshot(folder, target, raw):
        if target.is_symlink() or (target.exists() and not stat.S_ISREG(
                target.stat(follow_symlinks=False).st_mode)):
            raise ValueError('personal dictionary target must be a regular file')
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', prefix='.lexicon-', suffix='.tmp',
                                             dir=folder, delete=False) as handle:
                tmp = Path(handle.name)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            _check_dir(folder)
            if target.is_symlink():
                raise ValueError('personal dictionary target replaced with link')
            os.replace(tmp, target)
            tmp = None
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def save(self, folder: Path) -> None:
        with _LOCK:
            folder, target = _check_dir(folder)
            raw = self._serialize()
            packages = self._serialize_packages()
            sidecar = folder / _PACKAGES_FILENAME
            if packages is not None:
                self._write_snapshot(folder, sidecar, packages)
            self._write_snapshot(folder, target, raw)

    @classmethod
    def load(cls, folder: Path, *, catalog=None) -> "UserLexicon":
        with _LOCK:
            _, target = _check_dir(folder)
            if not target.exists():
                if catalog is not None and (folder / _PACKAGES_FILENAME).exists():
                    raise LexiconLoadError(target, _PACKAGES_FILENAME,
                        'Основной личный словарь отсутствует; внешний файл сохранён', None)
                return cls(catalog=catalog)
            location, raw = '$', None
            try:
                with target.open('rb') as stream:
                    raw = stream.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES:
                    raise ValueError("lexicon too large")
                doc = json.loads(raw.decode("utf-8"))
                if (set(doc) != {"version", "entries"} or type(doc["version"]) is not int
                        or doc["version"] != 1):
                    raise ValueError("unsupported lexicon schema")
                entries = doc["entries"]
                location = 'entries'
                if set(entries) != set(_KINDS):
                    raise ValueError("invalid kinds")
                result = cls(catalog=catalog)
                languages = set(_LANGS)
                for kind in _KINDS:
                    location = f'entries.{kind}'
                    if set(entries[kind]) != languages:
                        raise ValueError("invalid languages")
                    for lang in languages:
                        location = f'entries.{kind}.{lang}'
                        if set(entries[kind][lang]) != set(_MODES):
                            raise ValueError("invalid modes")
                        for mode in _MODES:
                            location = f'entries.{kind}.{lang}.{mode}'
                            items = entries[kind][lang][mode]
                            if not isinstance(items, list) or len(items) > _MAX_WORDS:
                                raise ValueError("invalid word list")
                            seen = set()
                            for index, word in enumerate(items, 1):
                                location = f'entries.{kind}.{lang}.{mode}[{index}]'
                                normalized = normalize(word, lang, sensitive=mode == "sensitive",
                                                       catalog=catalog)
                                if word != normalized or normalized in seen:
                                    raise ValueError("noncanonical or duplicate word")
                                seen.add(normalized)
                            result._bucket(kind, lang, mode == 'sensitive').update(seen)
                if catalog is not None:
                    sidecar = folder / _PACKAGES_FILENAME
                    if sidecar.exists() or sidecar.is_symlink():
                        location = _PACKAGES_FILENAME
                        if (sidecar.is_symlink() or not stat.S_ISREG(
                                sidecar.stat(follow_symlinks=False).st_mode)):
                            raise ValueError('invalid packages file')
                        # Bound the read itself, not just the parsed content:
                        # a corrupt/hostile sidecar must not allocate its full
                        # size before the 2 MiB guard runs.
                        with sidecar.open('rb') as package_stream:
                            content = package_stream.read(_MAX_BYTES + 1)
                        if len(content) > _MAX_BYTES:
                            raise ValueError('lexicon too large')
                        package_doc = json.loads(content.decode('utf-8'))
                        if (set(package_doc) != {'version', 'entries'}
                                or type(package_doc['version']) is not int
                                or package_doc['version'] != 1):
                            raise ValueError('unsupported lexicon schema')
                        package_entries = package_doc['entries']
                        if set(package_entries) != set(_KINDS):
                            raise ValueError('invalid kinds')
                        extra = set(package_entries['words'])
                        if (not extra.isdisjoint(_LANGS)
                                or any(not isinstance(lang, str)
                                       or not _PACK_CODE.fullmatch(lang) for lang in extra)
                                or set(package_entries['exclusions']) != extra):
                            raise ValueError('invalid languages')
                        # Validate structural integrity for EVERY language
                        # before checking evolving package alphabet policies.
                        # A rejected policy must never hide corrupt JSON or
                        # malformed personal entries in another bucket.
                        for kind in _KINDS:
                            for lang in extra:
                                bucket = package_entries[kind][lang]
                                if set(bucket) != set(_MODES):
                                    raise ValueError('invalid modes')
                                for mode in _MODES:
                                    location = f'{_PACKAGES_FILENAME}.{kind}.{lang}.{mode}'
                                    items = bucket[mode]
                                    if not isinstance(items, list) or len(items) > _MAX_WORDS:
                                        raise ValueError('invalid word list')
                                    if (any(not isinstance(item, str)
                                            or not 1 <= len(item) <= 80
                                            or not item.isalpha() for item in items)
                                            or len(set(items)) != len(items)):
                                        raise ValueError('invalid word list')
                        for lang in extra:
                            incompatible = lang not in catalog
                            for kind in _KINDS:
                                for mode in _MODES:
                                    items = package_entries[kind][lang][mode]
                                    if incompatible:
                                        continue
                                    for item in items:
                                        try:
                                            normalized = normalize(item, lang,
                                                sensitive=mode == 'sensitive', catalog=catalog)
                                        except ValueError:
                                            # A previously accepted word may
                                            # now fail an updated alphabet
                                            # policy. Quarantine the COMPLETE
                                            # language, including exclusions.
                                            incompatible = True
                                            break
                                        if item != normalized:
                                            raise ValueError('noncanonical or duplicate word')
                            for kind in _KINDS:
                                for mode in _MODES:
                                    items = package_entries[kind][lang][mode]
                                    if incompatible:
                                        result.unavailable[kind].setdefault(lang, {})[mode] = tuple(items)
                                    else:
                                        result._bucket(kind, lang, mode == 'sensitive').update(items)
                return result
            except (ValueError, UnicodeError, TypeError, KeyError, AttributeError, RecursionError) as exc:
                reasons = {
                    'lexicon too large': 'Размер файла превышает 2 МБ',
                    'unsupported lexicon schema': 'Неподдерживаемый формат словаря',
                    'invalid kinds': 'Неверные разделы словаря',
                    'invalid languages': 'Неверные языки словаря',
                    'invalid modes': 'Неверные режимы регистра',
                    'invalid word list': 'Неверный список слов',
                    'noncanonical or duplicate word': 'Неканоническая или повторяющаяся запись',
                }
                reason = reasons.get(str(exc), 'Недопустимая структура или запись словаря')
                if isinstance(exc, json.JSONDecodeError):
                    location = f'JSON: {exc.lineno}:{exc.colno}'
                    reason = 'Некорректный JSON'
                elif isinstance(exc, UnicodeError):
                    reason = 'Файл должен иметь кодировку UTF-8'
                # A broken external sidecar is NOT a reason to offer an
                # automatic reset of the user's valid legacy dictionary.
                digest = (hashlib.sha256(raw).hexdigest()
                          if (raw is not None and len(raw) <= _MAX_BYTES
                              and not location.startswith(_PACKAGES_FILENAME)) else None)
                raise LexiconLoadError(target, location, reason, digest) from exc
