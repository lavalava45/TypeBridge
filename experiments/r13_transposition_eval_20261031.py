"""Offline deterministic R13 probe against shipped Russian lexicon (read only).

Synthetic swapped tokens are NOT a representative user typing corpus. The
reported false-suggestion proxy concerns known intended originals only.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from russian_transposition_20261031 import suggest_adjacent_transposition

LETTERS = frozenset("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")


def read_vocabulary(path):
    return frozenset(w for w in path.read_text(encoding="utf-8-sig").splitlines()
                     if 3 <= len(w) <= 64 and set(w) <= LETTERS)


def swapped_tokens(word):
    return {word[:i] + word[i + 1] + word[i] + word[i + 2:]
            for i in range(len(word) - 1) if word[i] != word[i + 1]}


def evaluate(vocabulary, originals, *, limit=2000):
    """Synthetic intent is the selected original; evaluate its adjacent swaps."""
    sample = sorted((w for w in originals if 4 <= len(w) <= 12 and w in vocabulary),
                    key=lambda w: (hashlib.sha256(w.encode("utf-8")).digest(), w))[:limit]
    counts = {"sampled_originals": len(sample), "generated_swaps": 0,
              "swaps_already_words": 0, "unknown_swaps": 0,
              "single_correct": 0, "single_wrong": 0,
              "ambiguous_suppressed": 0, "no_candidate": 0}
    examples = {"wrong": [], "ambiguous": [], "correct": [], "known": []}
    for original in sample:
        for typo in sorted(swapped_tokens(original)):
            counts["generated_swaps"] += 1
            if typo in vocabulary:
                counts["swaps_already_words"] += 1
                if len(examples["known"]) < 5:
                    examples["known"].append([original, typo])
                continue
            counts["unknown_swaps"] += 1
            valid = {w for w in swapped_tokens(typo) if w in vocabulary}
            result = suggest_adjacent_transposition(
                typo, vocabulary, mode="suggest", russian_confirmed=True,
                field_kind="plain_text")
            if len(valid) > 1:
                group = "ambiguous_suppressed"
                name = "ambiguous"
            elif result == (original,):
                group = "single_correct"
                name = "correct"
            elif result:
                group = "single_wrong"
                name = "wrong"
            else:
                group = "no_candidate"
                name = None
            counts[group] += 1
            if name and len(examples[name]) < 5:
                examples[name].append([original, typo, *sorted(valid), *result])
    return {"counts": counts, "examples": examples}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, default=ROOT / "dist" / "data")
    p.add_argument("--limit", type=int, default=2000)
    args = p.parse_args()
    if not 1 <= args.limit <= 50000:
        p.error("limit must be between 1 and 50000")
    ru = args.data / "ru.txt"
    confident = args.data / "confident_ru.txt"
    vocabulary = read_vocabulary(ru)
    originals = read_vocabulary(confident)
    result = evaluate(vocabulary, originals, limit=args.limit)
    result["metadata"] = {
        "lexicon": str(ru), "lexicon_sha256": hashlib.sha256(ru.read_bytes()).hexdigest(),
        "confident_sha256": hashlib.sha256(confident.read_bytes()).hexdigest(),
        "vocabulary_words_filtered": len(vocabulary),
        "confident_words_filtered": len(originals),
        "method": "sha256-ordered confident word sample, every distinct adjacent swap",
        "limitations": "synthetic only; original was selected from source lexicon; not user typo accuracy",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
