"""R16/3a: read-only discovery of versioned, data-only language packs.

This module never imports executable code from a pack, changes the user's HKL,
loads a keyboard hook, installs files, or enables a language in the engine.
The latter requires separate runtime/cache integration (R16/3b).
"""

from dataclasses import dataclass
import argparse
import codecs
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import urlsplit

from language_registry import (DictionaryAddition, LANGUAGE_REGISTRY,
                               LanguageDescription)


PACK_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 64 * 1024
MAX_DICTIONARY_BYTES = 64 * 1024 * 1024
MAX_LICENSE_BYTES = 1024 * 1024
MAX_PACK_FILES = 24

_CODE = re.compile(r'[a-z]{2,3}(?:-[a-z]{2,4})?', re.ASCII)
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}\.(?:txt|md)', re.ASCII)
_VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+', re.ASCII)
_SHA256 = re.compile(r'[0-9a-f]{64}', re.ASCII)
_SCRIPT = re.compile(r'[A-Z][A-Z_ ]{1,39}', re.ASCII)
_LICENSE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+-]{0,63}', re.ASCII)


@dataclass(frozen=True)
class LoadedLanguagePack:
    description: LanguageDescription
    directory: Path
    package_version: str
    normalization: str
    source_name: str
    source_url: str
    license_id: str
    license_file: str
    verified_files: tuple[str, ...]


def _require_object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f'{label}: missing or unsupported manifest fields')
    return value


def _strict_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f'Duplicate JSON field: {key}')
        obj[key] = value
    return obj


def _regular_path(path: Path) -> bool:
    # Reject link/reparse-point redirects (including Windows junctions).
    return (not path.is_symlink()
            and not (getattr(path, 'is_junction', lambda: False)()))


def _safe_file_name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError('Language pack filenames must be plain .txt/.md basenames')
    return value


def _required_string(value, field, maximum=256):
    if (not isinstance(value, str) or not value.strip()
            or len(value) > maximum or any(ord(c) < 32 for c in value)):
        raise ValueError(f'Invalid language pack field: {field}')
    return value


def _validate_alphabet(alphabet, script):
    _required_string(alphabet, 'alphabet', 512)
    if (len(set(alphabet)) != len(alphabet)
            or any(not char.isalpha() or script not in unicodedata.name(char, '')
                   for char in alphabet)):
        raise ValueError('Alphabet must contain unique Unicode letters of the declared script')
    return alphabet


def _text_hash(path, max_size, *, needs_words=False):
    size = path.stat().st_size
    if size < 1 or size > max_size:
        raise ValueError(f'Language pack file size out of bounds: {path.name}')
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    total = 0
    tail = ''
    has_word = False
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > max_size:
                raise ValueError(f'Language pack file too large: {path.name}')
            digest.update(chunk)
            try:
                decoded = decoder.decode(chunk)
            except UnicodeDecodeError as error:
                raise ValueError(f'Language pack file is not UTF-8: {path.name}') from error
            if '\x00' in decoded:
                raise ValueError(f'NUL character in language pack file: {path.name}')
            if needs_words:
                lines = (tail + decoded).split('\n')
                tail = lines.pop()
                if len(tail) > 4096:
                    raise ValueError(f'Language pack word line too long: {path.name}')
                if any(line.strip() and not line.lstrip().startswith('#') for line in lines):
                    has_word = True
    try:
        decoded = decoder.decode(b'', final=True)
    except UnicodeDecodeError as error:
        raise ValueError(f'Language pack file is not UTF-8: {path.name}') from error
    if needs_words and ((tail + decoded).strip()
                        and not (tail + decoded).lstrip().startswith('#')):
        has_word = True
    if total != size or path.stat().st_size != total:
        raise ValueError(f'Language pack file changed during verification: {path.name}')
    if needs_words and not has_word:
        raise ValueError(f'Language pack dictionary is empty: {path.name}')
    return total, digest.hexdigest()


def load_language_pack(directory: Path) -> LoadedLanguagePack:
    """Parse and authenticate one directory without executing any pack content."""
    directory = Path(directory)
    if not _regular_path(directory) or not directory.is_dir():
        raise ValueError('Language pack must be a real directory')
    manifest_path = directory / 'manifest.json'
    if not _regular_path(manifest_path) or not manifest_path.is_file():
        raise ValueError('Missing regular language pack manifest.json')
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError('Language pack manifest too large')
    with manifest_path.open('rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('Language pack manifest too large')
    try:
        manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=_strict_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid UTF-8/JSON language pack manifest') from error
    _require_object(manifest, (
        'schema_version', 'package_version', 'code', 'name', 'langid',
        'unicode_script', 'alphabet', 'normalization', 'dictionary_file',
        'confident_file', 'additions', 'alternatives', 'source', 'files'), 'manifest')
    if type(manifest['schema_version']) is not int or manifest['schema_version'] != PACK_SCHEMA_VERSION:
        raise ValueError('Unsupported language pack schema version')
    version = manifest['package_version']
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError('Invalid language pack version')
    code = manifest['code']
    if not isinstance(code, str) or not _CODE.fullmatch(code) or directory.name != code:
        raise ValueError('Language pack code must match its directory name')
    name = _required_string(manifest['name'], 'name', 128)
    langid = manifest['langid']
    if type(langid) is not int or not 0 < langid <= 0xFFFF:
        raise ValueError('Invalid Windows LANGID')
    script = manifest['unicode_script']
    if not isinstance(script, str) or not _SCRIPT.fullmatch(script):
        raise ValueError('Invalid Unicode script')
    alphabet = _validate_alphabet(manifest['alphabet'], script)
    normalization = manifest['normalization']
    if normalization != 'casefold':
        raise ValueError('Unsupported language pack normalization rule')

    dictionary_file = _safe_file_name(manifest['dictionary_file'])
    confident_file = _safe_file_name(manifest['confident_file'])
    additions = manifest['additions']
    if not isinstance(additions, list) or len(additions) > MAX_PACK_FILES - 3:
        raise ValueError('Invalid language pack additions')
    parsed_additions = []
    for addition in additions:
        _require_object(addition, ('filename', 'for_words', 'for_confident'), 'addition')
        filename = _safe_file_name(addition['filename'])
        if (type(addition['for_words']) is not bool
                or type(addition['for_confident']) is not bool
                or not (addition['for_words'] or addition['for_confident'])):
            raise ValueError('Invalid language pack addition roles')
        parsed_additions.append(DictionaryAddition(filename, addition['for_words'],
                                                    addition['for_confident']))
    alternatives = manifest['alternatives']
    if (not isinstance(alternatives, list) or len(alternatives) > 1000
            or any(not isinstance(other, str) or not _CODE.fullmatch(other)
                   or other == code for other in alternatives)
            or len(set(alternatives)) != len(alternatives)):
        raise ValueError('Invalid language pack alternative priorities')
    source = _require_object(manifest['source'],
                             ('name', 'url', 'license_id', 'license_file'), 'source')
    source_name = _required_string(source['name'], 'source.name')
    source_url = source['url']
    if not isinstance(source_url, str) or len(source_url) > 2048:
        raise ValueError('Invalid language pack source URL')
    if source_url:
        parsed_url = urlsplit(source_url)
        if (parsed_url.scheme != 'https' or not parsed_url.hostname
                or parsed_url.username or parsed_url.password):
            raise ValueError('Language pack source URL must be HTTPS')
    license_id = source['license_id']
    if not isinstance(license_id, str) or not _LICENSE_ID.fullmatch(license_id):
        raise ValueError('Invalid language pack license ID')
    license_file = _safe_file_name(source['license_file'])

    required = (dictionary_file, confident_file,
                *(addition.filename for addition in parsed_additions), license_file)
    if len(set(required)) != len(required):
        raise ValueError('Language pack file names must not overlap')
    files = manifest['files']
    if (not isinstance(files, dict) or len(files) > MAX_PACK_FILES
            or set(files) != set(required)):
        raise ValueError('Language pack files must match the declared payload')
    # Reject all hidden, executable, extra or nested files. No unpacking here.
    if {item.name for item in directory.iterdir()} != {'manifest.json', *required}:
        raise ValueError('Language pack contains undeclared files or directories')
    for filename in sorted(files):
        file_path = directory / filename
        if not _regular_path(file_path) or not file_path.is_file():
            raise ValueError(f'Language pack file is not a regular file: {filename}')
        record = _require_object(files[filename], ('bytes', 'sha256'), 'file metadata')
        expected_size, expected_hash = record['bytes'], record['sha256']
        if (type(expected_size) is not int or expected_size < 1
                or not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash)):
            raise ValueError('Invalid language pack file signature')
        cap = MAX_LICENSE_BYTES if filename == license_file else MAX_DICTIONARY_BYTES
        if expected_size > cap:
            raise ValueError('Language pack file exceeds size cap')
        measured = _text_hash(file_path, cap, needs_words=(
            filename in (dictionary_file, confident_file)))
        if measured != (expected_size, expected_hash):
            raise ValueError(f'Language pack file checksum mismatch: {filename}')
    description = LanguageDescription(
        code=code, name=name, langid=langid, unicode_script=script,
        alphabet=alphabet, dictionary_file=dictionary_file,
        confident_file=confident_file, additions=tuple(parsed_additions),
        alternatives=tuple(alternatives))
    return LoadedLanguagePack(description, directory, version, normalization,
                              source_name, source_url, license_id, license_file,
                              tuple(sorted(required)))


def load_installed_language_pack(directory: Path):
    """Dispatch strictly by manifest schema, accepting v1 and data-only v2.

    Existing v1 validation remains byte-for-byte intact. No package Python
    modules are imported or executed; v2 uses an independent strict parser.
    """
    directory = Path(directory)
    manifest = directory / 'manifest.json'
    if not _regular_path(directory) or not _regular_path(manifest):
        raise ValueError('Installed language package cannot be a symlink')
    with manifest.open('rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('Language package manifest too large')
    try:
        version = json.loads(raw.decode('utf-8'), object_pairs_hook=_strict_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid installed language package manifest') from error
    if not isinstance(version, dict) or type(version.get('schema_version')) is not int:
        raise ValueError('Missing valid language package schema version')
    if version['schema_version'] == 1:
        return load_language_pack(directory)
    if version['schema_version'] == 2:
        from language_pack_v2 import load_v2_pack
        return load_v2_pack(directory)
    raise ValueError('Unsupported installed language pack schema version')


def discover_language_packs(root: Path, *, tolerant=False, errors=None,
                            selected_codes=None) -> dict[str, LoadedLanguagePack]:
    """Verify packages. Optional selection never reads inactive payloads.

    Inventory mode may isolate an invalid *unselected* package; callers must
    still fail closed if an explicitly selected package is missing or invalid.
    Strict mode is the default for validation tools and existing callers.
    """
    root = Path(root)
    if not root.exists():
        return {}
    if not _regular_path(root) or not root.is_dir():
        raise ValueError('Language pack root must be a real directory')
    result = {}
    if selected_codes is None:
        entries = (entry for entry in sorted(root.iterdir(), key=lambda item: item.name)
                   if not (entry.is_file() and entry.name == 'README.md'))
    else:
        if any(not isinstance(code, str) or not _CODE.fullmatch(code)
               for code in selected_codes):
            raise ValueError('Invalid selected language pack code')
        entries = (root / code for code in dict.fromkeys(selected_codes))
    for entry in entries:
        try:
            pack = load_installed_language_pack(entry)
        except (ValueError, OSError) as error:
            if not tolerant:
                raise
            if errors is not None:
                errors[entry.name] = f'{type(error).__name__}: {error}'
            continue
        code = pack.description.code
        if code in result:
            raise ValueError(f'Duplicate language pack: {code}')
        result[code] = pack
    return result


def available_language_catalog(root: Path, *, tolerant=False, errors=None,
                               bundled_root=None, selected_codes=None) -> dict[str, LanguageDescription]:
    """Inventory authenticated bundled v2 and installed languages.

    Default bundled location is adjacent to the installed root for production. A caller
    with isolated fixture directories can pass its own bundled_root, including
    a nonexistent path to exercise historical flat-file compatibility.
    Selected bundled corruption always fails closed, even in tolerant mode.
    """
    from language_registry import LANGUAGE_BY_CODE
    from language_pack_v2 import load_v2_pack
    bundled_root = (Path(root).parent / 'bundled_language_packs'
                    if bundled_root is None else Path(bundled_root))
    reserved = LANGUAGE_BY_CODE
    catalog = {}
    if bundled_root.exists():
        if not _regular_path(bundled_root) or not bundled_root.is_dir():
            raise ValueError('Bundled language root must be a real directory')
        for code, canonical in reserved.items():
            try:
                pack = load_v2_pack(bundled_root / code)
                fields = ('code', 'langid', 'unicode_script', 'alphabet',
                          'alternatives', 'dictionary_file', 'confident_file')
                if any(getattr(pack.description, field) != getattr(canonical, field)
                       for field in fields):
                    raise ValueError(f'Bundled language identity drift: {code}')
            except (ValueError, OSError) as error:
                if not tolerant or (selected_codes is not None and code in selected_codes):
                    raise
                if errors is not None:
                    errors[code] = f'{type(error).__name__}: {error}'
                continue
            catalog[code] = pack.description
    else:
        catalog.update(reserved)
    # Installed packages cannot occupy a reserved code or LANGID, even when
    # an invalid bundled entry was isolated from inventory.
    langids = {language.langid: code for code, language in reserved.items()}
    # Even strict callers can supply selected_codes without an error sink.
    # A missing selected directory is not discovered during a general inventory
    # scan, so check the requested identities explicitly after merging.
    installed_errors = {} if tolerant or errors is None else errors
    installed = discover_language_packs(root, tolerant=tolerant,
                                        errors=installed_errors)
    if selected_codes is not None and any(code in installed_errors
                                          for code in selected_codes):
        raise ValueError('Selected installed language package is invalid')
    if errors is not None and installed_errors is not errors:
        errors.update(installed_errors)
    for code, pack in installed.items():
        if code in reserved or pack.description.langid in langids:
            reason = (f'External language pack may not override built-in {code}'
                      if code in reserved else
                      f'Language pack LANGID conflicts with {langids[pack.description.langid]}')
            if not tolerant or (selected_codes is not None and code in selected_codes):
                raise ValueError(reason)
            if errors is not None:
                errors[code] = reason
            continue
        catalog[code] = pack.description
        langids[pack.description.langid] = code
    if selected_codes is not None:
        if (not isinstance(selected_codes, (tuple, list))
                or any(not isinstance(code, str) or not _CODE.fullmatch(code)
                       for code in selected_codes)
                or len(set(selected_codes)) != len(selected_codes)
                or any(code not in catalog for code in selected_codes)):
            raise ValueError('Selected language package is missing or invalid')
    return catalog


def main(argv=None) -> int:
    """Read-only inventory for package authors; never enable a language."""
    parser = argparse.ArgumentParser(description='Validate TypeBridge language packages (metadata only)')
    parser.add_argument('--root', type=Path,
                        default=Path(__file__).resolve().parent / 'language_packs',
                        help='directory containing one subdirectory per language code')
    args = parser.parse_args(argv)
    try:
        catalog = available_language_catalog(args.root)
    except (OSError, ValueError) as error:
        print(f'INVALID LANGUAGE PACKAGE: {error}', file=sys.stderr)
        return 2
    builtin = {language.code for language in LANGUAGE_REGISTRY}
    for code, description in catalog.items():
        origin = 'builtin' if code in builtin else 'verified package, not enabled'
        # An ASCII fallback avoids mojibake in the legacy Windows console.
        display = description.name if description.name.isascii() else code.upper()
        print(f'{code}: {display} | LANGID {description.langid:04X} | {origin}')
    print(f'Discovered {len(catalog)} language descriptions; maximum three active.')
    print('NOTE: validation does not activate a package. Select 1-3 languages and restart a compatible TypeBridge build.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
