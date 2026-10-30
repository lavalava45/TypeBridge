"""Pure, conservative suggestions while a word is still being typed.

This is evidence for a suggestion, never permission to write to an editor.
The caller must independently prove token boundaries and safe replacement.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from types import SimpleNamespace
from engine import Decision, _one_script_letters, _valid_typed
from language_registry import DEFAULT_ACTIVE_LANGUAGES
from frequency_rank import normalize_word


MIN_EARLY_LENGTH = 5
MAX_UNFINISHED_LETTERS = 2


def _normalized(engine, word, language):
    custom = getattr(engine, '_normalize', None)
    return custom(word, language) if custom is not None else normalize_word(word, language)


def build_early_index(engine, lexicon=None):
    lexicon = lexicon if lexicon is not None else getattr(engine, "user_lexicon", None)
    result = {}
    for language in getattr(engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES):
        words = set(engine.words[language])
        if lexicon is not None:
            for mode in ("sensitive", "insensitive"):
                words.update(word.casefold() for word in lexicon.data["words"].get(
                    language, {}).get(mode, ()))
        if _has_yo_alias(engine, language):
            words.update(word.replace("ё", "е") for word in tuple(words))
        result[language] = tuple(sorted(words))
    return result


def _has_yo_alias(engine, code):
    policies = getattr(engine, 'language_policies', None)
    if policies is not None and code in policies:
        return 'yo_to_e' in policies[code].behavior.early_prefix_aliases
    return code == 'ru'  # Legacy model/old engine compatibility.


def prepare_early(engine) -> None:
    """Build immutable sorted snapshots BEFORE installing a keyboard hook.

    Rebuild explicitly if lexical data changes; the hot path never refreshes.
    """
    # Base dictionaries are immutable after engine construction. Sort them
    # once, before installing the hook; personal words stay in a small overlay.
    prepared = engine.__dict__.pop('_startup_base_index', None)
    engine._early_base_index = (prepared if prepared is not None else
                               build_early_index(SimpleNamespace(
                                   words=engine.words,
                                   active_languages=getattr(engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES))))
    engine._early_cached_key = None
    engine._early_prefix_index = cached_early_index(engine, getattr(engine, 'user_lexicon', None))


@dataclass(frozen=True)
class PrefixOverlay:
    base: tuple[str, ...]
    additions: tuple[str, ...]


def cached_early_index(engine, lexicon):
    """Reuse the base snapshot; settings/exclusions never trigger a full sort.

    Only personal *words* affect prefixes. Snapshot their contents so replacing,
    removing or editing a word cannot accidentally reuse a stale index.
    Publishing the returned mapping with its lexicon remains the caller's job.
    """
    if not hasattr(engine, '_early_base_index'):
        # Compatibility for engines created outside LiveHook. Production calls
        # prepare_early before installing the hook, even when early mode is off.
        prepare_early(engine)
    languages = getattr(engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES)
    key = tuple(frozenset(word.casefold()
                         for mode in ('sensitive', 'insensitive')
                         for word in lexicon.data['words'].get(lang, {}).get(mode, ()))
                if lexicon is not None else frozenset() for lang in languages)
    if key == getattr(engine, '_early_cached_key', None):
        return engine._early_cached_index
    result = {}
    for lang, words in zip(languages, key):
        additions = set(words)
        if _has_yo_alias(engine, lang):
            additions.update(word.replace('ё', 'е') for word in words)
        base = engine._early_base_index[lang]
        result[lang] = PrefixOverlay(base, tuple(sorted(additions))) if additions else base
    engine._early_cached_key = key
    engine._early_cached_index = result
    return result


def _prefix_matches(index: tuple[str, ...], prefix: str, limit: int = 2) -> tuple[str, ...]:
    if isinstance(index, PrefixOverlay):
        # The first N words of a union are among the first N of each shard.
        # Deduplication preserves the original full-index ambiguity policy.
        return tuple(sorted(set(_prefix_matches(index.base, prefix, limit)) |
                            set(_prefix_matches(index.additions, prefix, limit))))[:limit]
    position = bisect_left(index, prefix)
    result = []
    for word in index[position:position + limit]:
        if not word.startswith(prefix):
            break
        result.append(word)
    return tuple(result)


def _suggest(engine, keys: list[tuple[int, bool]], current: str,
             *, unique_language: bool = False) -> Decision | None:
    """Suggest a unique high-confidence target prefix before a separator.

    Refuses uncertain prefixes, upper case, unrelated symbols, and competing scripts.
    It cannot predict subsequent keys: a consuming caller must re-evaluate the
    evolving token and never assume a suggestion authorizes immediate writing.
    """
    active = getattr(engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES)
    if (current not in active or len(keys) < (4 if unique_language else MIN_EARLY_LENGTH)
            or len(keys) > 40
            or any(shift or not isinstance(vk, int) or vk < 0 or vk > 255
                   for vk, shift in keys)):
        return None
    index = getattr(engine, "_early_prefix_index", None)
    if index is None or any(language not in index for language in active):
        return None  # Never initialize, sort or scan dictionaries on hook thread.
    typed = engine.mapper.word(keys, current)
    if (len(typed) != len(keys) or typed != typed.lower()
            or not _valid_typed(keys, typed, current,
                                getattr(engine, 'language_catalog', None))):
        return None
    source = _normalized(engine, typed, current)
    excluded = getattr(engine, "_excluded", lambda lang, word: word.casefold() in engine.exclusions)
    if excluded(current, typed) or _prefix_matches(index[current], source, 1):
        return None

    possible = []
    for language in active:
        if language == current:
            continue
        candidate = engine.mapper.word(keys, language)
        if (len(candidate) != len(keys) or not candidate.isalpha()
                or candidate != candidate.lower() or not _one_script_letters(
                    candidate, getattr(engine, 'language_catalog', None))):
            continue
        normalized = _normalized(engine, candidate, language)
        if excluded(language, candidate):
            continue
        # Check the complete lexicon before quality filtering: even a rare
        # competing prefix must veto the supposedly confident destination.
        matches = _prefix_matches(index[language], normalized)
        if matches:
            possible.append((language, candidate, matches))
    if len(possible) != 1:
        # Never rank *unfinished* alternatives. In particular, a prefix of a
        # rare but longer word must not disappear merely for lacking counts.
        if (len(possible) < 2
                or any(matches != (_normalized(engine, candidate, language),)
                       for language, candidate, matches in possible)):
            return None
        resolver = getattr(engine, "resolve_ambiguity", None)
        selected = resolver([(lang, word) for lang, word, _ in possible], current) if resolver else None
        if selected is None:
            return None
        language, candidate = selected
        matches = (_normalized(engine, candidate, language),)
    else:
        language, candidate, matches = possible[0]
    if unique_language:
        # Evaluation only: a four-character token must be a complete,
        # confidently known word. Longer prefixes need an eligible confident
        # completion; this does not establish confidence in future keystrokes.
        if len(keys) == 4:
            if _normalized(engine, candidate, language) not in engine.confident[language]:
                return None
        elif not any(word in engine.confident[language]
                     and len(word) - len(candidate) <= MAX_UNFINISHED_LETTERS
                     for word in matches):
            return None
    else:
        if len(matches) != 1:
            return None
        completion = matches[0]
        if (completion not in engine.confident[language]
                or len(completion) - len(candidate) > MAX_UNFINISHED_LETTERS):
            return None
    return Decision(typed, candidate, language, "confident early prefix")


def suggest_early(engine, keys: list[tuple[int, bool]], current: str) -> Decision | None:
    """Conservative production policy. No relaxed rules are active."""
    return _suggest(engine, keys, current)


def evaluate_unique_language(engine, keys: list[tuple[int, bool]], current: str) -> Decision | None:
    """Offline research policy; do not call from the keyboard hook."""
    return _suggest(engine, keys, current, unique_language=True)


def _suggest_rare_exact(engine, keys: list[tuple[int, bool]], current: str) -> Decision | None:
    """Opt-in rare words: only a unique COMPLETE destination word, never a guess.

    The conservative prefix policy is intentionally not relaxed. The same
    sorted full-lexicon snapshots veto both source prefixes and any competing
    target prefix, even if the competing word is rare or unfinished. This
    check has logarithmic lookup cost and performs no indexing on the hook.
    """
    active = getattr(engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES)
    if (getattr(engine, 'conservative', True) or current not in active
            or len(keys) < MIN_EARLY_LENGTH or len(keys) > 40
            or any(shift or not isinstance(vk, int) or vk < 0 or vk > 255
                   for vk, shift in keys)):
        return None
    index = getattr(engine, '_early_prefix_index', None)
    if index is None or any(language not in index for language in active):
        return None
    typed = engine.mapper.word(keys, current)
    if (len(typed) != len(keys) or typed != typed.lower()
            or not _valid_typed(keys, typed, current,
                                getattr(engine, 'language_catalog', None))):
        return None
    excluded = getattr(engine, '_excluded', lambda lang, word: word.casefold() in engine.exclusions)
    if (excluded(current, typed)
            or _prefix_matches(index[current], _normalized(engine, typed, current), 1)):
        return None
    exact_targets = []
    for language in active:
        if language == current:
            continue
        candidate = engine.mapper.word(keys, language)
        if (len(candidate) != len(keys) or not candidate.isalpha()
                or candidate != candidate.lower() or not _one_script_letters(
                    candidate, getattr(engine, 'language_catalog', None))):
            continue
        normalized = _normalized(engine, candidate, language)
        matches = _prefix_matches(index[language], normalized, 1)
        if not matches:
            continue
        # A competing target prefix, even of a different rare/longer word,
        # makes correction before a delimiter unsafe.
        if excluded(language, candidate) or matches[0] != normalized:
            return None
        exact_targets.append((language, candidate))
    if len(exact_targets) != 1:
        return None
    language, replacement = exact_targets[0]
    return Decision(typed, replacement, language, 'unique rare exact word')


def suggest_live_early(engine, keys, current):
    """Confident early prefixes; optional rare words ONLY as unique exact tokens.

    The checkbox controls dictionary eligibility, not the safety of an editor
    write. For rare words, no incomplete-prefix or short-token guess is made.
    """
    policy = getattr(engine, '_policy_engine', None)
    if policy is not None:
        engine._sync_policy()
        policy.prefix_index = getattr(engine, '_early_prefix_index', None)
        result = policy.suggest_live_early(keys, current)
        return (Decision(result.typed, result.replacement, result.language,
                         result.reason) if result is not None else None)
    exact = _suggest(engine, keys, current, unique_language=True)
    if exact is not None and engine._confident(exact.language, exact.replacement):
        return exact
    confident = suggest_early(engine, keys, current)
    return confident if confident is not None else _suggest_rare_exact(engine, keys, current)
