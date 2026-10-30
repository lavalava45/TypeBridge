"""Create tiny, license-declared R16 test packs. NOT a production corpus.

This is a developer fixture generator. Runtime TypeBridge never calls it.
The output is data-only; any package can be installed without importing code.
"""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent / 'r16-demo-packs'
PACKS = {
    'ar': {
        'name': 'Arabic (demo)', 'langid': 0x0401, 'script': 'ARABIC',
        'alphabet': 'ابتثجحخدذرزسشصضطظعغفقكلمنهوي',
        'words': 'مرحبا\nمدينة\nمدرسة\nنافذة\nصباح\nمساء\nسلام\nشكرا\nكتاب\n',
        'confident': 'مرحبا\nمدينة\nمدرسة\n',
    },
    'es': {
        'name': 'Spanish (demo)', 'langid': 0x0C0A, 'script': 'LATIN',
        'alphabet': 'abcdefghijklmnñopqrstuvwxyzáéíóúü',
        'words': 'niños\nmañana\nhola\ncasa\nárbol\namigo\nperro\ngato\ngracias\nfamilia\n',
        'confident': 'niños\nmañana\ngracias\n',
    },
}
LICENSE = (
    'TypeBridge R16 demonstration vocabulary (ordinary standalone words).\n'
    'Selected for controlled software testing, not derived from a dictionary corpus.\n'
    'Dedicated to the public domain under CC0-1.0 to the extent copyright applies.\n'
    'https://creativecommons.org/publicdomain/zero/1.0/\n'
)


def generate():
    for code, spec in PACKS.items():
        path = ROOT / code
        path.mkdir(parents=True, exist_ok=True)
        texts = {'words.txt': spec['words'], 'confident.txt': spec['confident'],
                 'LICENSE.txt': LICENSE}
        files = {}
        for name, content in texts.items():
            raw = content.encode('utf-8')
            filename = path / name
            if filename.exists() and filename.read_bytes() != raw:
                raise ValueError(f'Refusing to overwrite a modified fixture: {filename}')
            filename.write_bytes(raw)
            files[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        manifest = {
            'schema_version': 1, 'package_version': '1.0.0',
            'code': code, 'name': spec['name'], 'langid': spec['langid'],
            'unicode_script': spec['script'], 'alphabet': spec['alphabet'],
            'normalization': 'casefold', 'dictionary_file': 'words.txt',
            'confident_file': 'confident.txt', 'additions': [],
            'alternatives': ['en', 'ru'],
            'source': {'name': 'TypeBridge original demonstration words',
                       'url': '', 'license_id': 'CC0-1.0',
                       'license_file': 'LICENSE.txt'},
            'files': files,
        }
        raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        target = path / 'manifest.json'
        if target.exists() and target.read_bytes() != raw:
            raise ValueError(f'Refusing to overwrite a modified fixture: {target}')
        target.write_bytes(raw)


if __name__ == '__main__':
    generate()
