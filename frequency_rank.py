"""Archived offline FrequencyWords index tooling, NOT used by live selection.

Numeric tables remain on disk for reproducibility and optional offline tests;
LanguageEngine does not load them or consult the legacy 20x tie-breaker.
"""
from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
import unicodedata

LANGUAGES = ("ru", "en", "he")
MANIFEST = "FREQUENCY-SOURCES.json"
# Minimum absolute evidence and a large separation to avoid guessing on noise.
MIN_PER_MILLION = 2.0
MIN_RATIO = 20.0


def normalize_word(word: str, language: str) -> str:
    word = unicodedata.normalize("NFKC", word).casefold()
    word = "".join(char for char in word if not unicodedata.combining(char))
    return word.replace("ё", "е") if language == "ru" else word


def load_frequencies(data_dir: Path) -> dict[str, dict[str, float]]:
    """Return all three verified tables or no evidence at all.

    None/unknown must never be treated as a zero, including Hunspell-only words.
    Reuse one verified snapshot across engine instances unless any file changes.
    """
    data_dir = Path(data_dir).resolve()
    try:
        stamp = tuple((p.stat().st_size, p.stat().st_mtime_ns) for p in
                      (data_dir / MANIFEST, *(data_dir / f"frequency_{lang}.tsv"
                                              for lang in LANGUAGES)))
    except OSError:
        return {}
    return _load_verified_snapshot(data_dir, stamp)


@lru_cache(maxsize=4)
def _load_verified_snapshot(data_dir: Path, stamp: tuple) -> dict[str, dict[str, float]]:
    # The stamp is a cache key only; source and index SHA-256 are still checked.
    try:
        meta = json.loads((data_dir / MANIFEST).read_text(encoding="utf-8"))
        if meta["format"] != 1 or meta["unit"] != "occurrences_per_million_full_corpus":
            return {}
        result = {}
        for lang in LANGUAGES:
            spec = meta["languages"][lang]
            path = data_dir / f"frequency_{lang}.tsv"
            contents = path.read_bytes()
            if hashlib.sha256(contents).hexdigest() != spec["index_sha256"]:
                return {}
            if spec["full_corpus_total"] <= 0:
                return {}
            table = {}
            previous = ""
            for line in contents.decode("utf-8").splitlines():
                word, value = line.split("\t")
                if (word <= previous or word != normalize_word(word, lang)):
                    return {}
                rate = float(value)
                if not math.isfinite(rate) or not 0 < rate <= 1_000_000:
                    return {}
                table[word] = rate
                previous = word
            if len(table) != spec["indexed_words"]:
                return {}
            result[lang] = table
        return result
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, OverflowError):
        return {}


def resolve_tie(candidates: list[tuple[str, str]],
                frequencies: dict[str, dict[str, float]]) -> tuple[str, str] | None:
    """Choose only between known, substantially different measured frequencies.

    Unknown competitors veto the decision: missing is not zero. Neither a
    spelling-list hit nor a user-defined word is assigned an invented weight.
    """
    if len(candidates) < 2 or any(lang not in frequencies for lang, _ in candidates):
        return None
    ranked = []
    for language, word in candidates:
        value = frequencies[language].get(normalize_word(word, language))
        if value is None:
            return None
        ranked.append((value, language, word))
    ranked.sort(reverse=True)
    first, second = ranked[:2]
    if first[0] < MIN_PER_MILLION or second[0] <= 0 or first[0] / second[0] < MIN_RATIO:
        return None
    return first[1], first[2]
