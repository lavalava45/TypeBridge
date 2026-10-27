"""Rebuild all three verified frequency indices from pinned full corpora.

Run with --source-dir for previously downloaded, hash-verified inputs, or omit
it for fresh downloads. Output directory must not already exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request

from frequency_rank import LANGUAGES, normalize_word
from prepare_dictionaries import SOURCE_ROOT
from prepare_russian_dictionary import INPUTS

SOURCES = {
    "ru": (INPUTS["ru_full.txt"][0], INPUTS["ru_full.txt"][1]),
    "en": (f"{SOURCE_ROOT}/en/en_full.txt",
           "7fea67ab954e2c01df6c608c9826e594cf36f8823b3243554f88245fb75dc506"),
    "he": (f"{SOURCE_ROOT}/he/he_full.txt",
           "a69a3f390eb53183bf191c4eac18282592992aef9ff184c5dcf8919f5ea0f773"),
}
PATTERNS = {"ru": re.compile(r"[а-яе]{1,40}\Z"),
            "en": re.compile(r"[a-z]{1,40}\Z"),
            "he": re.compile(r"[א-ת]{1,40}\Z")}


def verified_source(language: str, source_dir: Path | None = None) -> bytes:
    url, digest = SOURCES[language]
    if source_dir is None:
        with urllib.request.urlopen(url, timeout=90) as stream:
            raw = stream.read()
    else:
        raw = (source_dir / f"{language}_full.txt").read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"Pinned source SHA-256 mismatch: {language}")
    return raw


def frequency_table(raw: bytes, language: str) -> tuple[dict[str, int], int]:
    """Sum the *entire* corpus before any word/script/length filtering.

    Combine differently written occurrences (including Russian е/ё) by adding
    each source row exactly once to one canonical key, never duplicating aliases.
    """
    counts = {}
    total = 0
    for number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            token, raw_count = line.rsplit(maxsplit=1)
            count = int(raw_count)
        except ValueError as exc:
            raise ValueError(f"Malformed {language} source row {number}") from exc
        if count <= 0:
            raise ValueError(f"Nonpositive {language} source row {number}")
        total += count
        normalized = normalize_word(token, language)
        if not PATTERNS[language].fullmatch(normalized):
            continue
        if language == "he" and any(char in "ךםןףץ" for char in normalized[:-1]):
            continue
        counts[normalized] = counts.get(normalized, 0) + count
    if total <= 0:
        raise ValueError(f"Empty corpus for {language}")
    return counts, total


def prepare(output_dir: Path, source_dir: Path | None = None) -> dict:
    if output_dir.exists():
        raise ValueError("Output directory exists; refusing to overwrite")
    # Fetch and validate ALL inputs before writing any output.
    sources = {lang: verified_source(lang, source_dir) for lang in LANGUAGES}
    output_dir.mkdir(parents=True)
    (output_dir / "sources").mkdir()
    manifest = {"format": 1, "unit": "occurrences_per_million_full_corpus",
                "normalization": "NFKC, casefold, remove combining marks; RU ё→е; sum each source row once",
                "license": "CC BY-SA 4.0", "attribution": "Hermit Dave / FrequencyWords; OpenSubtitles 2018",
                "languages": {}}
    for lang, raw in sources.items():
        counts, full_total = frequency_table(raw, lang)
        if len(counts) < 10000:
            raise ValueError(f"Unexpected source vocabulary size for {lang}: {len(counts)}")
        # Fixed decimal formatting makes the index deterministic and compact.
        lines = (f"{word}\t{counts[word] * 1_000_000 / full_total:.12f}\n"
                 for word in sorted(counts))
        data = "".join(lines).encode("utf-8")
        (output_dir / f"frequency_{lang}.tsv").write_bytes(data)
        (output_dir / "sources" / f"{lang}_full.txt").write_bytes(raw)
        manifest["languages"][lang] = {
            "source_url": SOURCES[lang][0], "source_sha256": SOURCES[lang][1],
            "full_corpus_total": full_total, "indexed_words": len(counts),
            "index_sha256": hashlib.sha256(data).hexdigest(),
        }
    (output_dir / "FREQUENCY-SOURCES.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "FREQUENCY-DATA-LICENSE.txt").write_text(
        "FrequencyWords content: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/\n"
        "Attribution: Hermit Dave / FrequencyWords; OpenSubtitles 2018 contributors.\n"
        "Modified: NFKC, casefold, combining marks, RU е/ё aggregation, script "
        "and length filtering, frequencies per million of complete original corpus.\n"
        "Source commits, URLs, SHA-256 and output hashes: FREQUENCY-SOURCES.json.\n",
        encoding="utf-8")
    return manifest


def restrict_to_vocabulary(output_dir: Path, vocabulary: dict[str, set[str]]) -> dict:
    """Reduce portable indices to words actually eligible for matching.

    Do not renormalize: absolute rates still use every token of each complete
    corpus. This drops only entries impossible to choose with these dictionaries.
    """
    manifest_file = output_dir / "FREQUENCY-SOURCES.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    for lang in LANGUAGES:
        path = output_dir / f"frequency_{lang}.tsv"
        raw = path.read_bytes()
        spec = manifest["languages"][lang]
        if hashlib.sha256(raw).hexdigest() != spec["index_sha256"]:
            raise ValueError(f"Frequency index checksum mismatch before pruning: {lang}")
        allowed = {normalize_word(word, lang) for word in vocabulary[lang]}
        rows = [line for line in raw.decode("utf-8").splitlines(keepends=True)
                if line.split("\t", 1)[0] in allowed]
        new_bytes = "".join(rows).encode("utf-8")
        path.write_bytes(new_bytes)
        spec["index_sha256"] = hashlib.sha256(new_bytes).hexdigest()
        spec["indexed_words"] = len(rows)
        spec["index_scope"] = "recognized dictionary entries; full-corpus denominator unchanged"
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output_dir.resolve(), args.source_dir),
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
