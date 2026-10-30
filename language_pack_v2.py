"""R16/4b: independent, read-only, strict data-only language-pack v2 parser.

Not imported by the shipping engine, GUI, v1 loader or current build.
No dynamic imports, OS keyboard APIs, hooks, unpacking or profile writes.
JSON Schema is a design artifact; runtime checks below also verify actual
paths, bytes, role combinations, filenames, Unicode and SHA-256.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat
import unicodedata

from language_pack_loader import (
    MAX_MANIFEST_BYTES, MAX_DICTIONARY_BYTES, MAX_LICENSE_BYTES,
    MAX_PACK_FILES, _CODE, _LICENSE_ID, _SCRIPT, _SHA256, _VERSION,
    _regular_path, _required_string, _require_object, _safe_file_name,
    _strict_pairs, _text_hash, _validate_alphabet,
)
from language_registry import LanguageDescription, DictionaryAddition

NORMALIZERS = frozenset((
    'casefold', 'nfkc_casefold_strip_combining',
    'nfkc_casefold_strip_combining_yo_to_e',
))
ALPHABET_POLICIES = frozenset((
    'ascii_latin_letters', 'russian_letters', 'hebrew_letters',
    'unicode_script_name',
))
AMBIGUITY_POLICIES = frozenset(('ordered_priority', 'reject_multiple'))
PREFIX_ALIASES = frozenset(('yo_to_e',))
# For v2/1 these are the ONLY historically proven non-OEM exceptions.
# Broader layout-specific policies require independent native verification.
NONLETTER_VK_ALLOWLIST = {81: '/', 87: "'"}


@dataclass(frozen=True)
class V2Addition:
    filename: str
    for_words: bool
    for_confident: bool
    words_normalizer: str
    confident_normalizer: str
    editable_source: bool = False


@dataclass(frozen=True)
class V2Behavior:
    ambiguous_destinations: str
    early_prefix_aliases: tuple[str, ...]
    source_nonletter_keys: tuple[tuple[int, str], ...]
    personal_alphabet_policy: str


@dataclass(frozen=True)
class VerifiedV2Pack:
    directory: Path
    description: LanguageDescription
    normalization: str
    additions: tuple[V2Addition, ...]
    behavior: V2Behavior
    verified_files: tuple[str, ...]
    file_hashes: tuple[tuple[str, str], ...]
    manifest_sha256: str
    package_version: str


def _object(value, fields, label):
    # Reject lists disguised as objects and any unsupported executable keys.
    return _require_object(value, fields, label)


def _enum(value, options, label):
    if not isinstance(value, str) or value not in options:
        raise ValueError(f'Unsupported {label}: {value!r}')
    return value


def _checked_folder(directory):
    directory = Path(directory)
    if (not _regular_path(directory) or not directory.is_dir()
            or any(not _regular_path(parent) for parent in directory.parents)):
        raise ValueError('v2 language pack must be a real directory, not a reparse point')
    return directory


def load_v2_pack(directory: Path) -> VerifiedV2Pack:
    """Validate one isolated package, fail closed on every mismatch."""
    directory = _checked_folder(directory)
    manifest_path = directory / 'manifest.json'
    if not _regular_path(manifest_path) or not manifest_path.is_file():
        raise ValueError('Missing regular v2 manifest.json')
    with manifest_path.open('rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('v2 manifest too large')
    try:
        doc = json.loads(raw.decode('utf-8'), object_pairs_hook=_strict_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid v2 manifest UTF-8/JSON') from error
    _object(doc, ('schema_version', 'package_version', 'code', 'name',
                  'langid', 'unicode_script', 'alphabet', 'normalization',
                  'dictionary_file', 'confident_file', 'additions',
                  'alternatives', 'behavior', 'source', 'files'), 'v2 manifest')
    if type(doc['schema_version']) is not int or doc['schema_version'] != 2:
        raise ValueError('Unsupported language pack v2 schema version')
    version = doc['package_version']
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError('Invalid v2 package version')
    code = doc['code']
    if not isinstance(code, str) or not _CODE.fullmatch(code) or code != directory.name:
        raise ValueError('v2 package code must match directory')
    name = _required_string(doc['name'], 'name', 128)
    langid = doc['langid']
    if type(langid) is not int or not 1 <= langid <= 0xFFFF:
        raise ValueError('Invalid v2 LANGID')
    script = doc['unicode_script']
    if not isinstance(script, str) or not _SCRIPT.fullmatch(script):
        raise ValueError('Invalid v2 unicode script')
    alphabet = _validate_alphabet(doc['alphabet'], script)
    norm = _object(doc['normalization'], ('dictionary',), 'v2 normalization')
    normalization = _enum(norm['dictionary'], NORMALIZERS, 'dictionary normalizer')
    if normalization.endswith('_yo_to_e') and script != 'CYRILLIC':
        raise ValueError('Yo-to-e dictionary normalizer requires Cyrillic script')
    dictionary_file = _safe_file_name(doc['dictionary_file'])
    confident_file = _safe_file_name(doc['confident_file'])
    raw_additions = doc['additions']
    if not isinstance(raw_additions, list) or len(raw_additions) > MAX_PACK_FILES - 3:
        raise ValueError('Invalid v2 additions')
    additions = []
    for item in raw_additions:
        base_fields = {'filename', 'for_words', 'for_confident', 'normalization'}
        if (not isinstance(item, dict) or
                set(item) not in (base_fields, base_fields | {'editable_source'})):
            raise ValueError('v2 addition: missing or unsupported manifest fields')
        if (type(item['for_words']) is not bool or type(item['for_confident']) is not bool
                or not (item['for_words'] or item['for_confident'])):
            raise ValueError('Invalid v2 addition roles')
        if type(item.get('editable_source', False)) is not bool:
            raise ValueError('Invalid v2 editable source policy')
        rules = _object(item['normalization'], ('words', 'confident'),
                        'v2 addition normalizers')
        additions.append(V2Addition(
            _safe_file_name(item['filename']), item['for_words'],
            item['for_confident'], _enum(rules['words'], NORMALIZERS, 'words normalizer'),
            _enum(rules['confident'], NORMALIZERS, 'confident normalizer'),
            item.get('editable_source', False)))
    alternatives = doc['alternatives']
    if (not isinstance(alternatives, list) or len(alternatives) > 1000
            or any(not isinstance(other, str) or not _CODE.fullmatch(other)
                   or other == code for other in alternatives)
            or len(set(alternatives)) != len(alternatives)):
        raise ValueError('Invalid v2 alternatives')
    b = _object(doc['behavior'], ('ambiguous_destinations', 'early_prefix_aliases',
                                  'source_nonletter_keys', 'personal_alphabet_policy'),
                'v2 behavior')
    ambiguity = _enum(b['ambiguous_destinations'], AMBIGUITY_POLICIES,
                      'ambiguity policy')
    alphabet_policy = _enum(b['personal_alphabet_policy'], ALPHABET_POLICIES,
                            'personal alphabet policy')
    if (alphabet_policy == 'russian_letters' and script != 'CYRILLIC'
            or alphabet_policy == 'hebrew_letters' and script != 'HEBREW'
            or alphabet_policy == 'ascii_latin_letters' and script != 'LATIN'):
        raise ValueError('v2 personal alphabet policy contradicts Unicode script')
    aliases = b['early_prefix_aliases']
    if (not isinstance(aliases, list) or len(aliases) > len(PREFIX_ALIASES)
            or any(not isinstance(x, str) or x not in PREFIX_ALIASES for x in aliases)
            or len(set(aliases)) != len(aliases)):
        raise ValueError('Invalid v2 prefix aliases')
    if 'yo_to_e' in aliases and script != 'CYRILLIC':
        raise ValueError('Yo-to-e alias requires Cyrillic script')
    nonletter = b['source_nonletter_keys']
    if not isinstance(nonletter, list) or len(nonletter) > 32:
        raise ValueError('Invalid v2 nonletter list')
    keys = []
    for entry in nonletter:
        _object(entry, ('vk', 'character'), 'v2 nonletter key')
        key, character = entry['vk'], entry['character']
        if (type(key) is not int or key not in NONLETTER_VK_ALLOWLIST
                or character != NONLETTER_VK_ALLOWLIST[key]):
            raise ValueError('Unverified v2 nonletter key')
        keys.append((key, character))
    if len(set(key for key, _ in keys)) != len(keys):
        raise ValueError('Duplicate v2 nonletter key')
    if keys and script != 'HEBREW':
        raise ValueError('v2 non-OEM key exception requires verified Hebrew script')
    behavior = V2Behavior(ambiguity, tuple(aliases), tuple(keys), alphabet_policy)
    source = _object(doc['source'], ('name', 'url', 'license_id', 'license_file'),
                     'v2 source')
    _required_string(source['name'], 'source.name')
    url = source['url']
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('Invalid v2 source URL')
    if url:
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname
                or parsed.username or parsed.password):
            raise ValueError('v2 source URL must be HTTPS')
    license_id = source['license_id']
    if not isinstance(license_id, str) or not _LICENSE_ID.fullmatch(license_id):
        raise ValueError('Invalid v2 license identifier')
    license_file = _safe_file_name(source['license_file'])
    required = (dictionary_file, confident_file,
                *(addition.filename for addition in additions), license_file)
    if len(set(required)) != len(required):
        raise ValueError('v2 payload filename collision')
    files = doc['files']
    if (not isinstance(files, dict) or len(files) > MAX_PACK_FILES
            or set(files) != set(required)):
        raise ValueError('Invalid v2 payload inventory')
    if {item.name for item in directory.iterdir()} != {'manifest.json', *required}:
        raise ValueError('v2 package contains undeclared content')
    hashes = []
    for filename in sorted(files):
        path = directory / filename
        if (not _regular_path(path) or not path.is_file()
                or not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)):
            raise ValueError('v2 payload is not a regular file')
        record = _object(files[filename], ('bytes', 'sha256'), 'v2 file metadata')
        expected_size, expected_hash = record['bytes'], record['sha256']
        cap = MAX_LICENSE_BYTES if filename == license_file else MAX_DICTIONARY_BYTES
        if (type(expected_size) is not int or not 1 <= expected_size <= cap
                or not isinstance(expected_hash, str)
                or not _SHA256.fullmatch(expected_hash)):
            raise ValueError('Invalid v2 payload limits or hash')
        if _text_hash(path, cap, needs_words=(filename in (dictionary_file, confident_file))) != (
                expected_size, expected_hash):
            raise ValueError(f'v2 payload checksum mismatch: {filename}')
        hashes.append((filename, expected_hash))
    description = LanguageDescription(
        code, name, langid, script, alphabet, dictionary_file, confident_file,
        tuple(DictionaryAddition(a.filename, a.for_words, a.for_confident)
              for a in additions), tuple(alternatives),
        personal_alphabet_policy=behavior.personal_alphabet_policy)
    return VerifiedV2Pack(directory, description, normalization, tuple(additions),
                          behavior, tuple(sorted(required)), tuple(hashes),
                          hashlib.sha256(raw).hexdigest(), version)


def load_v2_selection(root, selected_codes):
    """Only requested packs are touched; corrupt inactive packs are isolated."""
    root = _checked_folder(root)
    if (not isinstance(selected_codes, (tuple, list)) or not 1 <= len(selected_codes) <= 3
            or any(not isinstance(code, str) or not _CODE.fullmatch(code)
                   for code in selected_codes)):
        raise ValueError('Select one to three distinct v2 package codes')
    if len(set(selected_codes)) != len(selected_codes):
        raise ValueError('Duplicate selected v2 codes')
    packs = {}
    langids = set()
    for code in selected_codes:
        pack = load_v2_pack(root / code)
        if pack.description.langid in langids:
            raise ValueError('Duplicate active language LANGID')
        langids.add(pack.description.langid)
        packs[code] = pack
    return packs


def normalize_v2(word, mode):
    """Independent, declarative equivalent of the currently used normalizers."""
    if mode not in NORMALIZERS:
        raise ValueError('Unsupported v2 normalization')
    if mode == 'casefold':
        return word.casefold()
    normalized = unicodedata.normalize('NFKC', word).casefold()
    normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
    if mode == 'nfkc_casefold_strip_combining_yo_to_e':
        normalized = normalized.replace('ё', 'е')
    return normalized
