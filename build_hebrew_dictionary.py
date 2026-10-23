"""Generate an offline Hebrew word list from FrequencyWords 2018 corpus.

Inputs are *data*, not executable code. Requires a local he_full.txt downloaded
separately from the source recorded in data/HEBREW-SOURCE.md. The generated
dictionary can be loaded by engine.py with no third-party runtime packages.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import unicodedata


HEBREW_ONLY = re.compile(r"[\u05d0-\u05ea]{2,40}\Z")
FINAL_LETTERS = frozenset("ךםןףץ")


def normalized_hebrew(token: str) -> str:
    """Remove niqqud/cantillation; reject punctuation or mixed scripts."""
    token = unicodedata.normalize("NFKC", token.strip())
    token = "".join(ch for ch in token if not unicodedata.combining(ch))
    return token if (HEBREW_ONLY.fullmatch(token)
                     and not any(ch in FINAL_LETTERS for ch in token[:-1])) else ""


def min_frequency(length: int) -> int:
    """Strict on short words to avoid noise from subtitle OCR and typos."""
    if length == 2:
        return 300
    if length == 3:
        return 80
    return 12


def generate(source: Path, destination: Path) -> dict[str, int | str]:
    """Read ranked word frequencies; keep only plausible plain Hebrew tokens."""
    selected: dict[str, int] = {}
    invalid = 0
    examined = 0
    source_digest = hashlib.sha256()
    with source.open("rb") as fh:
        for raw in fh:
            source_digest.update(raw)
            examined += 1
            try:
                line = raw.decode("utf-8-sig").strip()
                token, count_string = line.rsplit(maxsplit=1)
                count = int(count_string)
            except (UnicodeError, ValueError):
                invalid += 1
                continue
            word = normalized_hebrew(token)
            if not word or count < min_frequency(len(word)):
                continue
            selected[word] = max(selected.get(word, 0), count)
    if len(selected) < 10000:
        raise ValueError(f"Suspiciously small Hebrew dictionary: {len(selected)} entries")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stable Unicode sorting; UTF-8 without BOM, one word per line.
    destination.write_text("\n".join(sorted(selected)) + "\n", encoding="utf-8")
    return {"source_sha256": source_digest.hexdigest(), "source_lines": examined,
            "skipped_malformed_lines": invalid, "written_words": len(selected),
            "dictionary_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Previously downloaded FrequencyWords he_full.txt")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "data" / "he.txt")
    args = parser.parse_args()
    for name, value in generate(args.source, args.output).items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    main()
