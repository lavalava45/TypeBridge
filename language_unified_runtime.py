"""R16/4c: one selected-language interface for builtin, v1 and v2 packages.

Builtin descriptions are compatibility adapters until all shipped dictionaries
can be repackaged as independently licensed v2 assets. They are passed through
the SAME lexical and decision interface as installed third-party packs.

No global hook, UI, HKL changes, imports from a package or user-data writes.
"""

from dataclasses import dataclass
from pathlib import Path

from dictionary_cache import load_or_build_sources, policy_digest
from language_lexicon_v2 import _read_words
from language_pack_v2 import V2Addition, V2Behavior, normalize_v2
from language_registry import LANGUAGE_BY_CODE, validate_active_languages


@dataclass(frozen=True)
class UnifiedLanguage:
    description: object
    directory: Path
    normalization: str
    additions: tuple[V2Addition, ...]
    behavior: V2Behavior
    manifest: str | None
    inline_words: tuple[str, ...] = ()
    inline_confident: tuple[str, ...] = ()


_BUILTIN_NORMALIZERS = {
    'ru': 'nfkc_casefold_strip_combining_yo_to_e',
    'en': 'nfkc_casefold_strip_combining',
    'he': 'nfkc_casefold_strip_combining',
}
_BUILTIN_PERSONAL = {'ru': 'russian_letters', 'en': 'ascii_latin_letters',
                     'he': 'hebrew_letters'}
_HE_NONLETTER = ((81, '/'), (87, "'"))
_LEGACY_INLINE = {
    'ru': (('а', 'и', 'поясни'), ('а', 'и', 'поясни')),
    'en': (('a', 'i'), ()),
}


def _legacy_inline(code):
    if code == 'he':
        # Import only inside the backwards-compatibility file adapter.
        # A normal v2 bundled HE language never imports this Python wordset.
        from engine import SAMPLE_HEBREW
        ordered = tuple(sorted(SAMPLE_HEBREW))
        return ordered, ordered
    return _LEGACY_INLINE[code]


def builtin_language(data_dir, code):
    """Temporary legacy storage adapter, not a decision-engine special case."""
    desc = LANGUAGE_BY_CODE[code]
    normalizer = _BUILTIN_NORMALIZERS[code]
    adds = tuple(V2Addition(add.filename, add.for_words, add.for_confident,
                            normalizer, 'casefold') for add in desc.additions)
    behavior = V2Behavior('ordered_priority', ('yo_to_e',) if code == 'ru' else (),
                          _HE_NONLETTER if code == 'he' else (),
                          _BUILTIN_PERSONAL[code])
    inline_words, inline_confident = _legacy_inline(code)
    return UnifiedLanguage(desc, Path(data_dir), normalizer, adds, behavior,
                           None, inline_words, inline_confident)


def installed_language(pack):
    """Turn a verified v1 or v2 installed pack into the SAME runtime shape."""
    if hasattr(pack, 'behavior'):
        return UnifiedLanguage(pack.description, pack.directory,
                               pack.normalization, pack.additions,
                               pack.behavior, 'manifest.json')
    # Validated v1 packs have casefold-only normalization. No code changes are
    # needed for new v1 language codes; ambiguity stays conservative.
    adds = tuple(V2Addition(a.filename, a.for_words, a.for_confident,
                            'casefold', 'casefold') for a in pack.description.additions)
    behavior = V2Behavior('reject_multiple', (), (), 'unicode_script_name')
    return UnifiedLanguage(pack.description, pack.directory, 'casefold',
                           adds, behavior, 'manifest.json')


def _compat_builtin_source_plan(selected, bundled_root):
    """Isolate fixed legacy identities from the package-driven runtime.

    A present bundled root must contain valid v2 packages for *all* selected
    shipped identities. Missing or corrupt selected bundles never fall back
    to old flat dictionaries. The fallback applies only to old distributions
    with no bundled root at all.
    """
    from language_pack_v2 import load_v2_pack
    reserved = dict(LANGUAGE_BY_CODE)
    builtin_selected = tuple(code for code in selected if code in reserved)
    bundled = ({code: load_v2_pack(bundled_root / code)
                for code in builtin_selected} if bundled_root.exists() else {})
    identity = ('code', 'langid', 'unicode_script', 'alphabet',
                'alternatives', 'dictionary_file', 'confident_file')
    for code, pack in bundled.items():
        canonical = reserved[code]
        if any(getattr(pack.description, field) != getattr(canonical, field)
               for field in identity):
            raise ValueError(f'Bundled language identity drift: {code}')
    fallback = tuple(code for code in builtin_selected if code not in bundled)
    return reserved, bundled, fallback


def _selected_package_sources(data_dir, pack_root, selected, verified_packages):
    """Resolve selected data packages, keeping the old flat-file gate isolated.

    The verified package records, rather than the historic builtin registry,
    supply the active runtime's descriptions, priorities and lexical sources.
    """
    from language_pack_loader import discover_language_packs
    reserved, bundled, fallback = _compat_builtin_source_plan(
        selected, data_dir.parent / 'bundled_language_packs')
    external = tuple(code for code in selected if code not in reserved)
    if external and pack_root is None:
        raise ValueError('External language activation requires pack_root')
    installed = (discover_language_packs(pack_root, selected_codes=external)
                 if external else {}) if verified_packages is None else verified_packages
    if set(installed) != set(external):
        raise ValueError('Missing/incorrect selected installed language package')
    # Preserve the reserved LANGID namespace even when a builtin is disabled.
    reserved_ids = {item.langid for item in reserved.values()}
    if any(pack.description.code != code or
           pack.description.langid in reserved_ids
           for code, pack in installed.items()):
        raise ValueError('Duplicate selected package code/LANGID')
    verified = {**installed, **bundled}
    if len({pack.description.langid for pack in verified.values()}) != len(verified):
        raise ValueError('Duplicate selected package LANGID')
    # Only activated languages enter the runtime catalog. Legacy descriptions
    # are admitted solely when a selected language has no v2 bundle at all.
    # Reserved identities are still consulted above for collision protection.
    catalog = {**{code: reserved[code] for code in fallback},
               **{code: pack.description for code, pack in verified.items()}}
    return catalog, verified, fallback, reserved, bundled


def prepare_selected(data_dir, pack_root, selected, *, cache_dir=None,
                     cache_progress=None, verified_packages=None):
    """Selected-only discovery, one lexical builder and shared source cache.

    This function intentionally accepts both existing v1 packages and v2
    packs, and builtin file-backed compatibility descriptions. The selection
    is fail-closed; broken unselected language payloads aren't opened.
    """
    data_dir = Path(data_dir)
    selected = tuple(selected)
    if pack_root is not None and selected == ():
        raise ValueError('External language activation requires explicit active_languages')
    catalog, verified, fallback, reserved, bundled = _selected_package_sources(
        data_dir, pack_root, selected, verified_packages)
    active = validate_active_languages(selected, catalog)
    languages = {code: (builtin_language(data_dir, code) if code in fallback
                        else installed_language(verified[code])) for code in active}
    from engine import require_dictionary_files
    if fallback:
        require_dictionary_files(data_dir, tuple(reserved[code] for code in fallback))
    def addon_source(code, language, addition):
        # Previously shipped distributions stored editable he_custom.txt in
        # data/. The v2 bundle declares its roles/normalization; the original
        # adjacent data file is a generic compatibility overlay when present.
        # This is outside the v2 manifest (NOT a special legacy pack schema).
        override = data_dir / addition.filename
        if code in bundled and addition.editable_source and override.is_file():
            return override
        return language.directory / addition.filename
    sources = {}
    for code, language in languages.items():
        root, desc = language.directory, language.description
        for name in (desc.dictionary_file, desc.confident_file):
            sources[f'lang/{code}/{name}'] = root / name
        for addition in language.additions:
            sources[f'lang/{code}/{addition.filename}'] = addon_source(
                code, language, addition)
        if language.manifest:
            sources[f'lang/{code}/manifest.json'] = root / language.manifest
            # Include license and other verified payloads in the source digest:
            # changes to a selected package invalidate even a warm cache.
            for filename in verified[code].verified_files:
                sources[f'verified/{code}/{filename}'] = root / filename
    sources['shared/exclude.txt'] = data_dir / 'exclude.txt'

    def build():
        words, confident = {}, {}
        for code, language in languages.items():
            root, desc = language.directory, language.description
            # External v1 always required valid 1..40-letter words from the
            # declared script. Preserve this gate; a signed manifest cannot
            # authorize arbitrary foreign-script or punctuation dictionary
            # entries merely because its SHA256 is valid.
            strict_script = desc.unicode_script if language.manifest else None
            words[code] = _read_words(root / desc.dictionary_file,
                                      language.normalization,
                                      strict_script=strict_script)
            confident[code] = _read_words(root / desc.confident_file,
                                          language.normalization,
                                          strict_script=strict_script)
            if not confident[code]:
                raise ValueError('Empty required confidence dictionary: '
                                 f'{desc.confident_file} ({code})')
            for addition in language.additions:
                if addition.for_words:
                    words[code].update(_read_words(addon_source(code, language, addition),
                                                   addition.words_normalizer,
                                                   strict_script=strict_script))
                if addition.for_confident:
                    confident[code].update(_read_words(addon_source(code, language, addition),
                                                       addition.confident_normalizer,
                                                       strict_script=strict_script))
            words[code].update(language.inline_words)
            confident[code].update(language.inline_confident)
            # Alias only when the declared policy permits it, not for RU by
            # a hidden decision-engine condition.
            if 'yo_to_e' in language.behavior.early_prefix_aliases:
                for bucket in (words[code], confident[code]):
                    bucket.update(value.replace('ё', 'е') for value in tuple(bucket))
        return {'words': words, 'confident': confident,
                'exclusions': _read_words(data_dir / 'exclude.txt', 'casefold')}

    def verify_selected_again():
        # In uncached mode verify selected packages a second time after reading
        # rather than trusting a stale initial manifest hash.
        from language_pack_loader import load_installed_language_pack
        for code, pack in verified.items():
            fresh = load_installed_language_pack(pack.directory)
            if fresh.description != pack.description or (
                    hasattr(pack, 'manifest_sha256') and
                    fresh.manifest_sha256 != pack.manifest_sha256):
                raise ValueError(f'Language pack changed during load: {code}')

    if cache_dir is None:
        from dictionary_cache import fingerprint_sources
        sources_tuple = tuple(sorted(sources.items()))
        policy = _policy_digest(languages, active)
        initial = fingerprint_sources(sources_tuple, policy)
        snapshot = build()
        if fingerprint_sources(sources_tuple, policy) != initial:
            raise ValueError('Language sources changed during preparation')
        verify_selected_again()
        return snapshot, 'disabled', catalog, languages
    policy = _policy_digest(languages, active)
    # Existing v2 cache implementation verifies source contents before reuse
    # and again before publication. Legacy words-v1.cache stays untouched.
    snapshot, status = load_or_build_sources(tuple(sorted(sources.items())),
                                              policy, build, cache_dir, active,
                                              progress=cache_progress)
    return snapshot, status, catalog, languages


def _policy_digest(languages, active):
    return policy_digest((normalize_v2, _read_words, prepare_selected),
                         ['r16/4c/unified-v1', tuple(active), [
                             (code, lang.normalization, lang.description.langid,
                              tuple(lang.description.alternatives),
                              lang.behavior.ambiguous_destinations,
                              tuple(lang.behavior.early_prefix_aliases),
                              lang.behavior.source_nonletter_keys,
                              [(a.filename, a.for_words, a.for_confident,
                                a.words_normalizer, a.confident_normalizer,
                                a.editable_source)
                               for a in lang.additions],
                              tuple(lang.inline_words), tuple(lang.inline_confident))
                             for code, lang in languages.items()]])
