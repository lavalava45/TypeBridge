"""R16/4b parallel read-only lexical preparation for verified v2 packages.

Never called from TypeBridge's production engine. Does not use the legacy
_read_lexical_data implementation, and deliberately does NOT write cache.
"""

from pathlib import Path
import hashlib
import stat
import unicodedata

from language_pack_loader import MAX_DICTIONARY_BYTES, _regular_path
from language_pack_v2 import load_v2_selection, normalize_v2


def _read_words(path, normalization, *, max_bytes=MAX_DICTIONARY_BYTES,
                strict_script=None):
    """Use the exact legacy read_words line/annotation rules, bounded on disk."""
    if not _regular_path(path) or not path.is_file() or not stat.S_ISREG(
            path.stat(follow_symlinks=False).st_mode):
        raise ValueError(f'Unsafe v2 lexical source: {path.name}')
    if path.stat().st_size > max_bytes:
        raise ValueError(f'Oversized v2 lexical source: {path.name}')
    result = set()
    with path.open('r', encoding='utf-8-sig') as stream:
        for physical_line in stream:
            # Python's file iteration splits LF/CRLF/CR, whereas historical
            # read_words uses str.splitlines() (also recognizing Unicode NEL,
            # U+2028 etc.). Keep streaming without losing that exact rule.
            for line in physical_line.splitlines():
                stripped = line.strip()
                if stripped and not line.lstrip().startswith('#'):
                    if (strict_script is not None
                            and (len(stripped) > 40 or not any(ch.isalpha()
                                                              for ch in stripped)
                                 or any(strict_script not in unicodedata.name(char, '')
                                        or (not char.isalpha() and
                                            not unicodedata.category(char).startswith('M'))
                                        for char in stripped))):
                        raise ValueError(f'Invalid script/word in {path.name}')
                    word = normalize_v2(stripped, normalization)
                    if (strict_script is not None
                            and (len(word) > 40 or not any(ch.isalpha()
                                                          for ch in word)
                                 or any(strict_script not in unicodedata.name(char, '')
                                        or (not char.isalpha() and
                                            not unicodedata.category(char).startswith('M'))
                                        for char in word))):
                        raise ValueError(f'Invalid normalized script in {path.name}')
                    if word:
                        result.add(word)
    return result


def _check_verified(pack):
    """Reaffirm the exact verified content to reject post-verification edits."""
    path = pack.directory / 'manifest.json'
    if not _regular_path(path) or not path.is_file():
        raise ValueError('v2 manifest no longer a regular file')
    with path.open('rb') as stream:
        if hashlib.sha256(stream.read(65537)).hexdigest() != pack.manifest_sha256:
            raise ValueError('v2 manifest modified since validation')
        if stream.read(1):
            raise ValueError('v2 manifest size changed')
    for name, expected in pack.file_hashes:
        source = pack.directory / name
        if not _regular_path(source) or not source.is_file():
            raise ValueError('v2 source no longer regular')
        digest = hashlib.sha256()
        with source.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f'v2 source changed since validation: {name}')


def build_v2_lexicon(packs, *, exclusions_file):
    """Prepare same dictionary shape as legacy without a legacy code branch.

    Files and supplemental lists are declared entirely by each v2 package.
    Package installation, profile state and the installed engine are untouched.
    """
    if not isinstance(packs, dict) or not 1 <= len(packs) <= 3:
        raise ValueError('Invalid verified v2 selection')
    langids = set()
    for code, pack in packs.items():
        if pack.description.code != code or pack.description.langid in langids:
            raise ValueError('Duplicate or inconsistent verified v2 language')
        langids.add(pack.description.langid)
        _check_verified(pack)

    words, confident = {}, {}
    for code, pack in packs.items():
        source = pack.directory
        description = pack.description
        words[code] = _read_words(source / description.dictionary_file, pack.normalization)
        primary_confident = _read_words(source / description.confident_file,
                                        pack.normalization)
        if not primary_confident:
            raise ValueError(f'Empty primary v2 confidence dictionary: {code}')
        confident[code] = primary_confident
        for addition in pack.additions:
            if addition.for_words:
                words[code].update(_read_words(source / addition.filename,
                                               addition.words_normalizer))
            if addition.for_confident:
                confident[code].update(_read_words(source / addition.filename,
                                                   addition.confident_normalizer))
    excluded = _read_words(Path(exclusions_file), 'casefold')
    # No-cache path must reject modification during preparation. This check
    # does not read any unselected (or corrupt inactive) language folder.
    for pack in packs.values():
        _check_verified(pack)
    return {'words': words, 'confident': confident, 'exclusions': excluded}


def load_selected_v2_lexicon(pack_root, selected_codes, *, exclusions_file):
    packages = load_v2_selection(pack_root, selected_codes)
    return build_v2_lexicon(packages, exclusions_file=exclusions_file)
