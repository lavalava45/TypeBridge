"""Create reproducible, fully declarative RU/EN/HE v2 packages from data/.

Offline build-time exporter only. Never invoked by keyboard hooks or startup.
Do not change original dictionaries, application profiles or installed packs.
"""

from pathlib import Path
import hashlib
import json
import shutil

from engine import SAMPLE_HEBREW
from language_pack_v2 import load_v2_pack
from language_registry import LANGUAGE_REGISTRY

NORMALIZATION = {'ru': 'nfkc_casefold_strip_combining_yo_to_e',
                 'en': 'nfkc_casefold_strip_combining',
                 'he': 'nfkc_casefold_strip_combining'}
PERSONAL = {'ru': 'russian_letters', 'en': 'ascii_latin_letters',
            'he': 'hebrew_letters'}
INLINE = {'ru': (('а', 'и', 'поясни'), True),
          'en': (('a', 'i'), False),
          'he': (tuple(sorted(SAMPLE_HEBREW)), True)}


def make_bundled_packages(data, output):
    """Build into an EMPTY destination; reject silent overwrite.

    Supply the already redistributed project `data/` with its original
    attribution, never obtain or download third-party files at runtime.
    """
    data, output = Path(data), Path(output)
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite bundled packages: {output}')
    output.mkdir(parents=True)
    try:
        for description in LANGUAGE_REGISTRY:
            code = description.code
            folder = output / code
            folder.mkdir()
            for name in (description.dictionary_file, description.confident_file,
                         *(addition.filename for addition in description.additions)):
                # `he_custom.txt` is an editable legacy local overlay and
                # may contain PRIVATE words in a developer's `data/`. Never
                # redistribute it from that directory. The bundled default
                # is the same public baseline used by prepare_open_data.py.
                if name == 'he_custom.txt':
                    (folder / name).write_text('מחשב\n', encoding='utf-8')
                    continue
                shutil.copyfile(data / name, folder / name)
                def digest(path):
                    check = hashlib.sha256()
                    with path.open('rb') as stream:
                        while part := stream.read(1024 * 1024):
                            check.update(part)
                    return check.hexdigest()
                if digest(data / name) != digest(folder / name):
                    raise ValueError(f'Bundled source changed during copy: {name}')
            supplement = 'builtin_supplement.txt'
            (folder / supplement).write_text('\n'.join(INLINE[code][0]) + '\n', encoding='utf-8')
            license_text = (data / 'DATA-LICENSE.txt').read_text(encoding='utf-8')
            if code == 'ru':
                license_text += '\n\n' + (data / 'RU-DATA-LICENSE.txt').read_text(encoding='utf-8')
            license_text += ('\n\nTypeBridge bundled adaptation: generated from existing '
                             'distributed data; full provenance in data/RU-SOURCES.json '
                             'and data/FREQUENCY-SOURCES.json.\n')
            (folder / 'LICENSE.txt').write_text(license_text, encoding='utf-8')
            normalizer = NORMALIZATION[code]
            additions = [
                {'filename': addition.filename,
                 'for_words': addition.for_words,
                 'for_confident': addition.for_confident,
                 'editable_source': addition.filename == 'he_custom.txt',
                 'normalization': {'words': normalizer, 'confident': 'casefold'}}
                for addition in description.additions]
            additions.append({'filename': supplement, 'for_words': True,
                              'for_confident': INLINE[code][1],
                              'normalization': {'words': normalizer,
                                                'confident': normalizer}})
            manifest = {
                'schema_version': 2, 'package_version': '1.0.0',
                'code': code, 'name': description.name, 'langid': description.langid,
                'unicode_script': description.unicode_script,
                'alphabet': description.alphabet,
                'normalization': {'dictionary': normalizer},
                'dictionary_file': description.dictionary_file,
                'confident_file': description.confident_file,
                'additions': additions, 'alternatives': list(description.alternatives),
                'behavior': {
                    'ambiguous_destinations': 'ordered_priority',
                    'early_prefix_aliases': ['yo_to_e'] if code == 'ru' else [],
                    'source_nonletter_keys': ([{'vk': 81, 'character': '/'},
                                               {'vk': 87, 'character': "'"}]
                                              if code == 'he' else []),
                    'personal_alphabet_policy': PERSONAL[code]},
                'source': {'name': 'TypeBridge redistributed vocabulary with original notices',
                           'url': 'https://creativecommons.org/licenses/by-sa/4.0/',
                           'license_id': 'CC-BY-SA-4.0',
                           'license_file': 'LICENSE.txt'},
                'files': {}}
            for path in sorted(folder.iterdir()):
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                manifest['files'][path.name] = {'bytes': path.stat().st_size,
                                                 'sha256': digest.hexdigest()}
            (folder / 'manifest.json').write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                encoding='utf-8')
            load_v2_pack(folder)
        return output
    except Exception:
        # No other process should consume a partial package build.
        shutil.rmtree(output)
        raise


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    target = make_bundled_packages(root / 'data', root / 'bundled_language_packs')
    print('BUILT', target)
