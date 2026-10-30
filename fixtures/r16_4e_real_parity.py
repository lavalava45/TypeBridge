"""Isolated full-size dictionary parity check; never starts a keyboard hook.

Run from the repository root: python build/r16-4e-real-parity.py
Copies dictionaries/packages into a temporary build folder; outputs counts and
hash-independent comparison flags, not private words or user settings.
"""

import gc
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import DICTIONARY_FILES, LanguageEngine  # noqa: E402
from test_r16_dynamic_engine import SyntheticMapper  # noqa: E402


def run():
    with TemporaryDirectory(prefix='r16-4e-real-parity-', dir=ROOT / 'build') as tmp:
        base = Path(tmp)
        data = base / 'data'
        data.mkdir()
        for filename in DICTIONARY_FILES:
            shutil.copy2(ROOT / 'dist' / 'data' / filename, data / filename)
        shutil.copytree(ROOT / 'bundled_language_packs',
                        base / 'bundled_language_packs')
        pack_root = base / 'language_packs'
        pack_root.mkdir()
        forms = {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}
        selected = ('ru', 'en', 'he')
        with patch('engine.LayoutMapper', return_value=SyntheticMapper(forms)):
            old = LanguageEngine(data, pack_root=pack_root,
                                 active_languages=selected,
                                 engine_backend='legacy')
            unified = LanguageEngine(data, pack_root=pack_root,
                                     active_languages=selected,
                                     engine_backend='unified',
                                     cache_dir=base / 'cache')
            report = {
                'semantic_equal': (old.words == unified.words and
                                   old.confident == unified.confident and
                                   old.exclusions == unified.exclusions),
                'words': {code: len(unified.words[code]) for code in selected},
                'confident': {code: len(unified.confident[code]) for code in selected},
                'cold_cache_status': unified.cache_status,
            }
            del old, unified
            gc.collect()
            warm = LanguageEngine(data, pack_root=pack_root,
                                  active_languages=selected,
                                  engine_backend='unified',
                                  cache_dir=base / 'cache')
            report['warm_cache_status'] = warm.cache_status
            report['warm_counts_identical'] = (
                report['words'] == {code: len(warm.words[code]) for code in selected}
                and report['confident'] == {
                    code: len(warm.confident[code]) for code in selected})
    if (not report['semantic_equal'] or not report['warm_counts_identical'] or
            report['cold_cache_status'] != 'built' or
            report['warm_cache_status'] != 'hit'):
        raise RuntimeError(f'Full-size R16/4e parity failed: {report}')
    path = ROOT / 'build' / 'r16-4e-real-parity-20261029.json'
    path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n',
                    encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    run()
