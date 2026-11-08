"""Small, fixed, non-statistical RU/EN/HE dictionary-mode comparison.

Run from repository root:
    py -3.12 -B experiments/dictionary_mode_evaluation_20261106.py

This reads bundled dictionaries; it never installs hooks or changes settings.
Physical keys are US keyboard letter names, independent of active HKL.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import LanguageEngine, source_data_dir  # noqa: E402


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    physical: str
    current: str
    expected: str
    note: str = ""


# Expected = intended final visible word, not a dictionary prediction.
# Two conflicting intentions for the identical "tot" are deliberate: text
# and HKL alone cannot distinguish these without additional context.
CASES = (
    Case("en-ok", "known-correct", "hello", "en", "hello"),
    Case("ru-ok", "known-correct", "ghbdtn", "ru", "привет"),
    Case("he-ok", "known-correct", "akuo", "he", "שלום"),
    Case("en-to-ru", "wrong-HKL", "ghbdtn", "en", "привет"),
    Case("he-to-ru", "wrong-HKL", "ghjdthrf", "he", "проверка"),
    Case("ru-to-en", "wrong-HKL", "hello", "ru", "hello"),
    Case("he-to-en", "wrong-HKL", "hello", "he", "hello"),
    Case("en-to-he", "wrong-HKL", "akuo", "en", "שלום"),
    Case("ru-to-he", "wrong-HKL", "akuo", "ru", "שלום"),
    Case("tot-en", "ambiguous", "tot", "en", "tot", "Actual English word"),
    Case("tot-ru", "ambiguous", "tot", "en", "еще", "Same keys; intended Russian 'еще'"),
    Case("short-en", "ambiguous", "a", "en", "a"),
    Case("short-ru", "ambiguous", "z", "en", "я"),
    Case("name-slava", "names", "ckfdf", "en", "слава", "Name in Russian"),
    Case("name-alice", "names", "alice", "en", "alice"),
    Case("name-david", "names", "david", "en", "david"),
    Case("tech-github", "tech", "github", "en", "github"),
    Case("tech-python", "tech", "python", "en", "python"),
    Case("tech-docker", "tech", "docker", "en", "docker"),
    Case("tech-spotify", "tech", "spotify", "en", "spotify"),
    Case("tech-openai", "tech", "openai", "en", "openai"),
    Case("tech-json", "tech", "json", "en", "json"),
    Case("rare-neiroset", "rare-probe", "ytqhjctnm", "en", "нейросеть"),
    Case("rare-kvant", "rare-probe", "rdfynjdfybt", "en", "квантование"),
    Case("rare-privetstvie", "rare-probe", "ghbdtncndbt", "en", "приветствие"),
)


def keyboard_events(physical: str) -> list[tuple[int, bool]]:
    if not physical.isascii() or not physical.isalpha():
        raise ValueError(f"Only A-Z physical-key labels are supported: {physical!r}")
    return [(ord(ch.upper()), ch.isupper()) for ch in physical]


def evaluate():
    data_dir = source_data_dir()
    for name in ("ru.txt", "en.txt", "he.txt", "confident_ru.txt",
                 "confident_en.txt", "confident_he.txt"):
        if not (data_dir / name).is_file():
            raise FileNotFoundError(f"Missing actual bundled dictionary: {data_dir / name}")
    engine = LanguageEngine(data_dir, conservative=True)
    output = []
    for case in CASES:
        row = {"case": case}
        for flag in (True, False):
            engine.conservative = flag
            keys = keyboard_events(case.physical)
            typed = engine.mapper.word(keys, case.current)
            decision = engine.decide_token(keys, case.current)
            actual = decision.replacement if decision else typed
            if actual == case.expected:
                verdict = "OK"
            elif actual != typed and actual != case.expected:
                verdict = "FP"
            elif actual == typed and typed != case.expected:
                verdict = "MISS"
            else:
                raise AssertionError((case.id, typed, actual, case.expected))
            row[flag] = (typed, actual, verdict, decision.reason if decision else "none")
        output.append(row)
    return data_dir, output


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    data_dir, rows = evaluate()
    print(f"Dictionary folder: {data_dir}")
    print("Fixed, manually curated examples; no sampling distribution or population estimate.")
    print("FP = unwanted/wrong replacement; MISS = retained text when correction was intended.")
    print("| ID | Category | Keys | HKL | Expected | Typed | Conservative=True | Verdict | Conservative=False | Verdict |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        case = row["case"]
        typed = row[True][0]
        print(f"| {case.id} | {case.category} | `{case.physical}` | {case.current.upper()} | "
              f"`{case.expected}` | `{typed}` | `{row[True][1]}` | {row[True][2]} | "
              f"`{row[False][1]}` | {row[False][2]} |")
    for flag in (True, False):
        totals = Counter(row[flag][2] for row in rows)
        print(f"conservative={flag}: OK={totals['OK']} FP={totals['FP']} MISS={totals['MISS']}")
    print("Uncertain ground truth: 'tot' has intentionally contradictory intentions; "
          "do not interpret counts as measured production rates.")


if __name__ == "__main__":
    main()
