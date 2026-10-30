"""R16/3b/1: isolated lexical snapshots for a selected language set.

This is a data preparation API, NOT a new LiveHook or keyboard mapper.
The installed application's legacy LanguageEngine continues using its
existing dictionary and v1 cache paths until runtime integration is proven.
"""

from pathlib import Path
import unicodedata

from dictionary_cache import load_or_build_sources, policy_digest
from engine import (SAMPLE_HEBREW, _read_lexical_data, read_words,
                    require_dictionary_files)
from frequency_rank import normalize_word
from language_pack_loader import discover_language_packs, load_language_pack
from language_registry import (LANGUAGE_BY_CODE, SHARED_DICTIONARY_FILES,
                               validate_active_languages)


def _read_external_words(path: Path, script: str) -> set[str]:
    """A v1 pack is casefold-only; reject words that violate that contract."""
    words = set()
    with path.open('r', encoding='utf-8') as stream:
        for line in stream:
            word = line.strip()
            if not word or word.startswith('#'):
                continue
            if (len(word) > 40 or not word.isalpha()
                    or any(script not in unicodedata.name(c, '') for c in word)):
                raise ValueError(f'Unsupported language pack word in {path.name}')
            normalized = word.casefold()
            if (not normalized or len(normalized) > 40
                    or not normalized.isalpha()
                    or any(script not in unicodedata.name(c, '')
                           for c in normalized)):
                raise ValueError(f'Unsupported normalized word in {path.name}')
            words.add(normalized)
    return words


def load_selected_lexicon(data_dir: Path, pack_root: Path,
                          selected_codes, *, cache_dir: Path | None = None,
                          verified_packages=None):
    """Prepare words/confidence/exclusions for up to three verified languages.

    Returns (snapshot, cache_status). All paths and data are read-only except
    the dedicated *v2* cache when cache_dir is explicitly supplied. No Windows
    APIs, global keyboard hooks, user settings or personal dictionaries.
    """
    data_dir, pack_root = Path(data_dir), Path(pack_root)
    # First verify only packages explicitly selected for correction. A broken
    # unused package must not block RU/EN/HE or consume full dictionary reads.
    selected = tuple(selected_codes)
    external = tuple(code for code in selected if code not in LANGUAGE_BY_CODE)
    packages = (discover_language_packs(pack_root, selected_codes=external)
                if verified_packages is None else verified_packages)
    used_langids = {description.langid for description in LANGUAGE_BY_CODE.values()}
    for code, package in packages.items():
        if code not in external or package.description.code != code:
            raise ValueError('Verified language pack does not match the selected set')
        if package.description.langid in used_langids:
            raise ValueError(f'Language pack LANGID conflict: {code}')
        used_langids.add(package.description.langid)
    catalog = {**LANGUAGE_BY_CODE,
               **{code: pack.description for code, pack in packages.items()}}
    active = validate_active_languages(selected, catalog)
    builtins = tuple(LANGUAGE_BY_CODE[code] for code in active if code in LANGUAGE_BY_CODE)
    require_dictionary_files(data_dir, builtins)

    files = {}
    builtin_names = (
        *(description.dictionary_file for description in builtins),
        *(description.confident_file for description in builtins),
        *(addition.filename for description in builtins for addition in description.additions),
        *SHARED_DICTIONARY_FILES,
    )
    for name in builtin_names:
        files[f'builtin/{name}'] = data_dir / name
    for code in active:
        if code not in packages:
            continue
        package = packages[code]
        for filename in ('manifest.json', *package.verified_files):
            files[f'pack/{code}/{filename}'] = package.directory / filename
    sources = tuple(sorted(files.items()))

    def build():
        # Preserve built-in supplements, normalizations and exclusions exactly.
        snapshot = _read_lexical_data(data_dir, builtins)
        for code in active:
            if code not in packages:
                continue
            package = packages[code]
            description = package.description
            root = package.directory
            words = _read_external_words(root / description.dictionary_file,
                                         description.unicode_script)
            confident = _read_external_words(root / description.confident_file,
                                             description.unicode_script)
            for addition in description.additions:
                extra = _read_external_words(root / addition.filename,
                                             description.unicode_script)
                if addition.for_words:
                    words.update(extra)
                if addition.for_confident:
                    confident.update(extra)
            if not confident:
                raise ValueError(f'Empty verified confidence dictionary: {code}')
            snapshot['words'][code] = words
            snapshot['confident'][code] = confident
        # The no-cache path needs a post-read integrity check. The v2 cache
        # builder independently fingerprints source files before and after
        # construction, avoiding this additional full validation there.
        if cache_dir is None:
            for code in external:
                load_language_pack(packages[code].directory)
        return snapshot

    if cache_dir is None:
        return build(), 'disabled'
    policy = policy_digest(
        (_read_lexical_data, read_words, normalize_word, _read_external_words),
        ['selected-lexicon-v2', tuple(active), sorted(SAMPLE_HEBREW),
         [(lang.code, lang.dictionary_file, lang.confident_file,
           [(addition.filename, addition.for_words, addition.for_confident)
            for addition in lang.additions]) for lang in builtins],
         [(code, packages[code].description.unicode_script,
           packages[code].normalization) for code in active if code in packages]])
    result, status = load_or_build_sources(sources, policy, build,
                                           cache_dir, active)
    return result, status
