"""Independent user lexicon, confined to an explicitly supplied directory.

Atomic whole-file snapshots. Thread-safe within one process; concurrent writers
from independent processes need an external transaction lock to avoid lost updates.
No SwitchER files or application settings are read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unicodedata

_LANGS = ("ru", "en", "he")
_KINDS = ("words", "exclusions")
_MODES = ("insensitive", "sensitive")
_FILENAME = "user_lexicon.json"
_MAX_BYTES = 2_000_000
_MAX_WORDS = 50_000
_LOCK = threading.RLock()


def normalize(word: str, language: str, *, sensitive: bool = False) -> str:
    if language not in _LANGS or type(sensitive) is not bool or not isinstance(word, str):
        raise ValueError("invalid word/language/mode")
    value = unicodedata.normalize("NFC", word)
    if not 1 <= len(value) <= 80 or not value.isalpha():
        raise ValueError("word must contain 1..80 letters only")
    if language == "en":
        allowed = all("A" <= ch <= "Z" or "a" <= ch <= "z" for ch in value)
    elif language == "ru":
        allowed = all("А" <= ch <= "я" or ch in "Ёё" for ch in value)
    else:
        allowed = all("\u05d0" <= ch <= "\u05ea" for ch in value)
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

    def _bucket(self, kind: str, language: str, sensitive: bool):
        if kind not in _KINDS or language not in _LANGS or type(sensitive) is not bool:
            raise ValueError("invalid bucket")
        return self.data[kind][language]["sensitive" if sensitive else "insensitive"]

    def add(self, kind: str, language: str, word: str, *, sensitive: bool = False) -> bool:
        value = normalize(word, language, sensitive=sensitive)
        bucket = self._bucket(kind, language, sensitive)
        if len(bucket) >= _MAX_WORDS and value not in bucket:
            raise ValueError("lexicon limit")
        previous = len(bucket)
        bucket.add(value)
        return len(bucket) != previous

    def remove(self, kind: str, language: str, word: str, *, sensitive: bool = False) -> bool:
        value = normalize(word, language, sensitive=sensitive)
        bucket = self._bucket(kind, language, sensitive)
        if value not in bucket:
            return False
        bucket.remove(value)
        return True

    def contains(self, kind: str, language: str, word: str) -> bool:
        folded = normalize(word, language)
        exact = normalize(word, language, sensitive=True)
        return (folded in self._bucket(kind, language, False)
                or exact in self._bucket(kind, language, True))

    def engine_view(self) -> tuple[dict[str, set[str]], set[str]]:
        """Compatibility data for engine.words and engine.exclusions (casefold only).

        Case-sensitive entries are intentionally NOT collapsed: LanguageEngine
        uses contains() separately for exact-case rules. This lossy compatibility
        view is not a substitute for passing UserLexicon to LanguageEngine.
        """
        words = {lang: set(self.data["words"][lang]["insensitive"]) for lang in _LANGS}
        exclusions = set().union(*(self.data["exclusions"][lang]["insensitive"] for lang in _LANGS))
        return words, exclusions

    def _serialize(self) -> bytes:
        doc = {"version": 1, "entries": {
            kind: {lang: {mode: sorted(self.data[kind][lang][mode]) for mode in _MODES}
                   for lang in _LANGS} for kind in _KINDS}}
        raw = (json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
        if len(raw) > _MAX_BYTES:
            raise ValueError("file size limit")
        return raw

    def save(self, folder: Path) -> None:
        with _LOCK:
            folder, target = _check_dir(folder)
            raw = self._serialize()
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", prefix=".lexicon-", suffix=".tmp",
                                                 dir=folder, delete=False) as handle:
                    tmp = Path(handle.name)
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                _check_dir(folder)
                os.replace(tmp, target)
                tmp = None
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)

    @classmethod
    def load(cls, folder: Path) -> "UserLexicon":
        with _LOCK:
            _, target = _check_dir(folder)
            if not target.exists():
                return cls()
            if target.stat().st_size > _MAX_BYTES:
                raise ValueError("lexicon too large")
            try:
                raw = target.read_bytes()
                if len(raw) > _MAX_BYTES:
                    raise ValueError("lexicon too large")
                doc = json.loads(raw.decode("utf-8"))
                if set(doc) != {"version", "entries"} or doc["version"] != 1:
                    raise ValueError("unsupported lexicon schema")
                entries = doc["entries"]
                if set(entries) != set(_KINDS):
                    raise ValueError("invalid kinds")
                result = cls()
                for kind in _KINDS:
                    if set(entries[kind]) != set(_LANGS):
                        raise ValueError("invalid languages")
                    for lang in _LANGS:
                        if set(entries[kind][lang]) != set(_MODES):
                            raise ValueError("invalid modes")
                        for mode in _MODES:
                            items = entries[kind][lang][mode]
                            if not isinstance(items, list) or len(items) > _MAX_WORDS:
                                raise ValueError("invalid word list")
                            seen = set()
                            for word in items:
                                normalized = normalize(word, lang, sensitive=mode == "sensitive")
                                if word != normalized or normalized in seen:
                                    raise ValueError("noncanonical or duplicate word")
                                seen.add(normalized)
                            result.data[kind][lang][mode] = seen
                return result
            except (UnicodeError, json.JSONDecodeError, TypeError, KeyError, AttributeError) as exc:
                raise ValueError("malformed lexicon") from exc
