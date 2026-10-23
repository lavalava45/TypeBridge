"""Build high-confidence candidate vocabularies from FrequencyWords (OpenSubtitles).

This is a conservative ranking filter, NOT a linguistic validation guarantee.
Keeps the original broad dictionaries untouched for detecting already-correct
words. Source tables are only required for rebuilding, not for runtime use.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import hashlib
import re
import unicodedata

from engine import read_words, source_data_dir


LIMITS = {"ru": 40000, "en": 35000, "he": 45000}
PATTERNS = {"ru": re.compile(r"[а-яё]{2,40}\Z"),
            "en": re.compile(r"[a-z]{2,40}\Z"),
            "he": re.compile(r"[א-ת]{2,40}\Z")}
HEBREW_FINAL = "ךםןףץ"


def plausible(word: str, language: str) -> bool:
    if not PATTERNS[language].fullmatch(word):
        return False
    if language == "he" and any(x in word[:-1] for x in HEBREW_FINAL):
        return False
    return True


def build(source: Path, language: str, data_dir: Path) -> dict:
    broad = read_words(data_dir / f"{language}.txt")
    selected = {}
    source_hash = hashlib.sha256()
    with source.open("rb") as fh:
        for raw in fh:
            source_hash.update(raw)
            try:
                token, count = raw.decode("utf-8-sig").strip().rsplit(maxsplit=1)
                count = int(count)
            except (UnicodeError, ValueError):
                continue
            word = unicodedata.normalize("NFKC", token).casefold()
            word = "".join(c for c in word if not unicodedata.combining(c))
            if plausible(word, language) and word in broad:
                selected[word] = max(selected.get(word, 0), count)
    selection = sorted(selected.items(), key=lambda kv: (-kv[1], kv[0]))[:LIMITS[language]]
    words = sorted(w for w, _ in selection)
    destination = data_dir / f"confident_{language}.txt"
    destination.write_text("\n".join(words) + "\n", encoding="utf-8")
    return {"language": language, "source_sha256": source_hash.hexdigest(),
            "source_path": str(source), "selected": len(words),
            "min_selected_count": selection[-1][1] if selection else None,
            "output_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=source_data_dir())
    args = parser.parse_args()
    for lang in LIMITS:
        source = args.source_dir / f"frequency_{lang}.tmp"
        if not source.is_file():
            parser.error(f"Missing input: {source}")
        print(build(source, lang, args.output_dir), flush=True)


if __name__ == "__main__":
    main()
