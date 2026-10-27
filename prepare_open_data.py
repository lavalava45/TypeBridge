"""Prepare reproducible PUBLIC TypeBridge dictionary data without private lists.

Creates Russian FrequencyWords + Lebedev/Hunspell word forms and a separate
verified FrequencyWords index for RU/EN/HE. No executable is built here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from prepare_dictionaries import LIMITS, select_words
from prepare_frequency_indices import prepare as prepare_indices, restrict_to_vocabulary, SOURCES
from prepare_russian_dictionary import prepare as prepare_russian


def prepare(output_dir: Path, source_dir: Path | None = None):
    # The committed public source may already contain a standalone Hebrew
    # research notice, but never a distributable word list.
    if output_dir.exists() and any(path.is_file() and path.name != "HEBREW-SOURCE.md"
                                   for path in output_dir.iterdir()):
        raise ValueError("Refusing to replace existing data files")
    with tempfile.TemporaryDirectory(prefix="typebridge-open-data-") as tmp:
        temp = Path(tmp)
        frequency = temp / "frequencies"
        prepare_indices(frequency, source_dir)
        russian = temp / "russian"
        prepare_russian(russian, frequency / "sources")
        output = {}
        vocabulary = {"ru": (russian / "ru.txt").read_text(encoding="utf-8").splitlines()}
        vocabulary = {"ru": set(vocabulary["ru"])}
        for name in ("ru.txt", "confident_ru.txt", "RU-SOURCES.json", "RU-DATA-LICENSE.txt"):
            output[name] = (russian / name).read_bytes()
        sources = {
            "license": "CC-BY-SA-4.0",
            "attribution": "Hermit Dave / FrequencyWords; OpenSubtitles 2018",
            "languages": {},
        }
        for lang in ("en", "he"):
            raw = (frequency / "sources" / f"{lang}_full.txt").read_bytes()
            words = select_words(raw, lang)
            confident = set(sorted(words, key=lambda w: (-words[w], w))[:LIMITS[lang]])
            output[f"{lang}.txt"] = ("\n".join(sorted(words)) + "\n").encode("utf-8")
            output[f"confident_{lang}.txt"] = ("\n".join(sorted(confident)) + "\n").encode("utf-8")
            vocabulary[lang] = set(words)
            sources["languages"][lang] = {
                "url": SOURCES[lang][0], "sha256": SOURCES[lang][1],
                "words": len(words), "confident": len(confident),
            }
        restrict_to_vocabulary(frequency, vocabulary)
        for name in ("FREQUENCY-SOURCES.json", "FREQUENCY-DATA-LICENSE.txt",
                     "frequency_ru.tsv", "frequency_en.tsv", "frequency_he.tsv"):
            output[name] = (frequency / name).read_bytes()
        sources["languages"]["ru"] = {
            "url": SOURCES["ru"][0], "sha256": SOURCES["ru"][1],
            "words": json.loads(output["RU-SOURCES.json"])["counts"]["combined"],
            "generator": "prepare_russian_dictionary.py (FrequencyWords + Lebedev Hunspell)",
        }
        output["SOURCES.json"] = (json.dumps(sources, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        output["DATA-LICENSE.txt"] = (
            "FrequencyWords-derived data: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/\n"
            "Hermit Dave / FrequencyWords; OpenSubtitles 2018 contributors.\n"
            "RU orthographic data: Alexander I. Lebedev and Laszlo Nemeth; see RU-DATA-LICENSE.txt.\n"
            "All full-corpus source URLs, revisions and hashes are recorded in RU-SOURCES.json "
            "and FREQUENCY-SOURCES.json.\n").encode("utf-8")
        output["he_custom.txt"] = "מחשב\n".encode("utf-8")
        output["exclude.txt"] = b"# Optional local exclusions, one per line.\n"
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, content in output.items():
            with (output_dir / name).open("xb") as stream:
                stream.write(content)
        return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path,
                        help="Directory with pre-downloaded, SHA-256 verified full corpora")
    args = parser.parse_args()
    print(json.dumps(prepare(args.output_dir.resolve(), args.source_dir),
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
