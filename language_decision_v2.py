"""R16/4b/2: independent, experimental, policy-driven decision evaluator.

No imports from LanguageEngine, incremental_recognition or their decision
helpers. No Windows APIs, hooks, editor writes, settings or filesystem I/O.
The only language-specific behavior comes from verified v2 package metadata.
NOT attached to the production GUI/LiveHook; parity is a model-only gate.
"""

from bisect import bisect_left
from dataclasses import dataclass
import unicodedata

from language_pack_v2 import normalize_v2

WORD_AFFIXES = frozenset(".,;:'\"`()[]{}<>!?…")
AMBIGUOUS_LAYOUT_SYMBOLS = frozenset("[];,.'`/\\-=<>:{}\"~")
LETTER_PUNCTUATION_KEYS = frozenset((0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF,
                                      0xC0, 0xDB, 0xDC, 0xDD, 0xDE))
MIN_EARLY_LENGTH = 5
MAX_UNFINISHED_LETTERS = 2


@dataclass(frozen=True)
class V2Decision:
    typed: str
    replacement: str
    language: str
    reason: str


class PolicyDecisionEngine:
    """A separate decision pipeline, configured solely by verified v2 packs."""

    def __init__(self, *, packs, active, mapper, words, confident,
                 exclusions, conservative=True, personal=None):
        if (not isinstance(active, (tuple, list)) or not 1 <= len(active) <= 3
                or len(set(active)) != len(active) or set(active) != set(packs)):
            raise ValueError('Invalid active policy composition')
        self.active = tuple(active)
        self.packs = packs
        self.mapper = mapper
        self.words = words
        self.confident = confident
        self.exclusions = exclusions
        self.conservative = conservative
        self.personal = personal
        self.prefix_index = None

    def normalize(self, code, text):
        return normalize_v2(text, self.packs[code].normalization)

    def _personal_word(self, kind, lang, value):
        if self.personal is None:
            return False
        try:
            return self.personal.contains(kind, lang, value)
        except ValueError:
            return False

    def excluded(self, lang, value):
        return value.casefold() in self.exclusions or self._personal_word('exclusions', lang, value)

    def known(self, lang, value):
        return (self.normalize(lang, value) in self.words[lang]
                or self._personal_word('words', lang, value))

    def certain(self, lang, value):
        return (self.normalize(lang, value) in self.confident[lang]
                or self._personal_word('words', lang, value))

    def _script(self, char):
        name = unicodedata.name(char, '')
        for code in self.active:
            script = self.packs[code].description.unicode_script
            if name.startswith(script + ' '):
                return script
        return None

    def one_script(self, text):
        scripts = {self._script(char) for char in text}
        return len(scripts) == 1 and None not in scripts

    def _allowed_symbol(self, vk, char, current):
        if char not in AMBIGUOUS_LAYOUT_SYMBOLS:
            return False
        if vk in LETTER_PUNCTUATION_KEYS:
            return True
        return (vk, char) in self.packs[current].behavior.source_nonletter_keys

    def valid_typed(self, keys, text, current):
        letters = []
        for (vk, _), char in zip(keys, text):
            if char.isalpha():
                letters.append(char)
            elif not self._allowed_symbol(vk, char, current):
                return False
        return not letters or self.one_script(''.join(letters))

    def resolve_ambiguity(self, matches, current, *, allow_rare=False, _trace=None):
        def reject(reason):
            if _trace is not None:
                _trace.append(reason)
            return None
        if current not in self.active:
            return reject('inactive-source-language')
        matches = [(lang, word) for lang, word in matches if lang in self.active]
        if (len(matches) > 1 and any(
                self.packs[code].behavior.ambiguous_destinations == 'reject_multiple'
                for code in self.active)):
            return reject('ambiguous-destinations')
        if any(self.excluded(lang, word) for lang, word in matches):
            return reject('excluded-target-word')
        before_confidence = len(matches)
        matches = [(lang, word) for lang, word in matches
                   if (allow_rare or not self.conservative or len(word) == 1
                       or self.certain(lang, word))]
        if before_confidence and not matches:
            return reject('insufficient-target-confidence')
        for language in self.packs[current].description.alternatives:
            candidates = [match for match in matches if match[0] == language]
            if len(candidates) == 1:
                return candidates[0]
            if candidates:
                return reject('ambiguous-destinations')
        return reject('no-matching-destination')

    def decide(self, keys, current, *, allow_rare=False, _trace=None):
        def reject(reason):
            if _trace is not None:
                _trace.append(reason)
            return None
        if (not keys or len(keys) > 40 or current not in self.active
                or current not in self.words):
            return reject('inactive-source-language' if current not in self.active else 'invalid-key-length')
        typed = self.mapper.word(keys, current)
        if len(typed) != len(keys):
            return reject('unmapped-physical-keys')
        if not self.valid_typed(keys, typed, current):
            return reject('invalid-source-script-or-symbol')
        if self.excluded(current, typed):
            return reject('excluded-source-word')
        if self.known(current, typed) and not allow_rare:
            return reject('known-source-word')
        if (len(typed) > 1 and not typed[-1].isalpha()
                and self.known(current, typed[:-1])):
            return reject('known-source-prefix')
        matches = []
        for language in self.active:
            if language == current:
                continue
            candidate = self.mapper.word(keys, language)
            if (len(candidate) == len(keys) and self.one_script(candidate)
                    and self.known(language, candidate)):
                matches.append((language, candidate))
        if len(matches) == 1:
            chosen = matches[0]
        else:
            chosen = self.resolve_ambiguity(matches, current, allow_rare=allow_rare,
                                            _trace=_trace)
            if chosen is None:
                return None
        language, replacement = chosen
        if (self.conservative and len(keys) > 1 and not allow_rare
                and not self.certain(language, replacement)):
            return reject('insufficient-target-confidence')
        if self.excluded(language, replacement):
            return reject('excluded-target-word')
        return V2Decision(typed, replacement, language,
                          'unique dictionary match' if len(matches) == 1
                          else 'configured language priority')

    def decide_token(self, keys, current, *, allow_rare=False, _trace=None):
        direct_trace = [] if _trace is not None else None
        direct = self.decide(keys, current, allow_rare=allow_rare,
                             _trace=direct_trace)
        if (direct is not None or current not in self.active or not 2 <= len(keys) <= 40):
            if _trace is not None and direct is None:
                _trace.extend(direct_trace)
            return direct
        typed = self.mapper.word(keys, current)
        if len(typed) != len(keys):
            if _trace is not None:
                _trace.append('unmapped-physical-keys')
            return None
        if self.excluded(current, typed):
            if _trace is not None:
                _trace.append('excluded-untrimmed-token')
            return None
        if any(self.known(lang, self.mapper.word(keys, lang)) for lang in self.active):
            if _trace is not None:
                _trace.append('known-other-language-untrimmed-token' if not
                              self.known(current, typed) else 'known-source-untrimmed-token')
            return None
        left_bound = 0
        while left_bound < min(4, len(typed)) and typed[left_bound] in WORD_AFFIXES:
            left_bound += 1
        right_bound = 0
        while (right_bound < min(4, len(typed))
               and typed[-right_bound - 1] in WORD_AFFIXES):
            right_bound += 1
        matches = {}
        for left in range(left_bound + 1):
            for right in range(right_bound + 1):
                end = len(keys) - right
                if (left == right == 0) or end - left < 1:
                    continue
                center = keys[left:end]
                if self.known(current, typed[left:end]):
                    if _trace is not None:
                        _trace.append('known-source-punctuation-center')
                    return None
                if self.excluded(current, typed[left:end]):
                    if _trace is not None:
                        _trace.append('excluded-source-punctuation-center')
                    return None
                decision = self.decide(center, current, allow_rare=allow_rare)
                if decision is not None:
                    replaced = typed[:left] + decision.replacement + typed[end:]
                    matches[(decision.language, replaced)] = V2Decision(
                        typed, replaced, decision.language, 'word with literal punctuation')
        if len(matches) == 1:
            return next(iter(matches.values()))
        if _trace is not None:
            _trace.append('ambiguous-punctuation-candidates' if matches else
                          ('no-matching-punctuation-destination' if
                           left_bound or right_bound else
                           (direct_trace[-1] if direct_trace else 'no-matching-destination')))
        return None

    def diagnose_token(self, keys, current, *, allow_rare=False):
        """Optional exact-path diagnostics; does no I/O and is never called by the hook.

        Each rejection code is captured at the branch that actually rejected
        the candidate. Normal decide/decide_token callers allocate no trace.
        """
        from decision_diagnostics import DecisionExplanation
        reasons = []
        decision = self.decide_token(keys, current, allow_rare=allow_rare,
                                     _trace=reasons)
        if decision is not None:
            return DecisionExplanation(True, decision.reason, decision.language,
                                       decision.replacement)
        return DecisionExplanation(False, reasons[-1] if reasons else
                                   'no-matching-destination')

    def prepare_early(self):
        index = {}
        for language in self.active:
            words = set(self.words[language])
            if self.personal is not None:
                for mode in ('sensitive', 'insensitive'):
                    words.update(word.casefold() for word in
                                 self.personal.data['words'].get(language, {}).get(mode, ()))
            if 'yo_to_e' in self.packs[language].behavior.early_prefix_aliases:
                words.update(word.replace('ё', 'е') for word in tuple(words))
            index[language] = tuple(sorted(words))
        self.prefix_index = index

    def _matches(self, language, prefix, limit=2):
        index = self.prefix_index[language]
        if hasattr(index, 'base') and hasattr(index, 'additions'):
            # Preserve the existing immutable large base + small personal
            # overlay. This avoids re-sorting millions of words when users
            # edit their personal dictionaries while LiveHook is running.
            def segment(words):
                place = bisect_left(words, prefix)
                return (word for word in words[place:place + limit]
                        if word.startswith(prefix))
            return tuple(sorted(set(segment(index.base)) |
                                set(segment(index.additions))))[:limit]
        position = bisect_left(index, prefix)
        matches = []
        for word in index[position:position + limit]:
            if not word.startswith(prefix):
                break
            matches.append(word)
        return tuple(matches)

    def _early(self, keys, current, *, unique_language=False, _trace=None):
        def reject(reason):
            if _trace is not None:
                _trace.append(reason)
            return None
        if (current not in self.active or len(keys) < (4 if unique_language else MIN_EARLY_LENGTH)
                or len(keys) > 40
                or any(shift or not isinstance(vk, int) or not 0 <= vk <= 255
                       for vk, shift in keys) or self.prefix_index is None):
            if current not in self.active:
                return reject('inactive-source-language')
            if len(keys) > 40:
                return reject('invalid-key-length')
            if len(keys) < (4 if unique_language else MIN_EARLY_LENGTH):
                return reject('prefix-below-confidence-threshold')
            if self.prefix_index is None:
                return reject('prefix-index-not-prepared')
            return reject('invalid-physical-key-or-shift')
        typed = self.mapper.word(keys, current)
        if (len(typed) != len(keys) or typed != typed.lower()
                or not self.valid_typed(keys, typed, current)):
            return reject('invalid-source-script-or-symbol')
        source = self.normalize(current, typed)
        if self.excluded(current, typed) or self._matches(current, source, 1):
            return reject('excluded-source-word' if self.excluded(current, typed)
                          else 'known-source-prefix')
        possible = []
        for language in self.active:
            if language == current:
                continue
            candidate = self.mapper.word(keys, language)
            if (len(candidate) != len(keys) or not candidate.isalpha()
                    or candidate != candidate.lower() or not self.one_script(candidate)):
                continue
            matches = self._matches(language, self.normalize(language, candidate))
            if matches and not self.excluded(language, candidate):
                possible.append((language, candidate, matches))
        if len(possible) != 1:
            if (len(possible) < 2 or any(
                    matches != (self.normalize(language, candidate),)
                    for language, candidate, matches in possible)):
                return reject('no-matching-destination' if not possible else
                              'ambiguous-prefix-destinations')
            chosen = self.resolve_ambiguity(
                [(language, candidate) for language, candidate, _ in possible], current,
                _trace=_trace)
            if chosen is None:
                return None
            language, candidate = chosen
            matches = (self.normalize(language, candidate),)
        else:
            language, candidate, matches = possible[0]
        if unique_language:
            if len(keys) == 4:
                if self.normalize(language, candidate) not in self.confident[language]:
                    return reject('insufficient-target-confidence')
            elif not any(word in self.confident[language]
                             and len(word) - len(candidate) <= MAX_UNFINISHED_LETTERS
                             for word in matches):
                return reject('insufficient-target-confidence')
        else:
            if len(matches) != 1:
                return reject('ambiguous-prefix-completions')
            completion = matches[0]
            if (completion not in self.confident[language]
                    or len(completion) - len(candidate) > MAX_UNFINISHED_LETTERS):
                return reject('insufficient-target-confidence')
        return V2Decision(typed, candidate, language, 'confident early prefix')

    def _rare_exact(self, keys, current):
        if (self.conservative or current not in self.active
                or not MIN_EARLY_LENGTH <= len(keys) <= 40
                or any(shift or not isinstance(vk, int) or not 0 <= vk <= 255
                       for vk, shift in keys) or self.prefix_index is None):
            return None
        typed = self.mapper.word(keys, current)
        if (len(typed) != len(keys) or typed != typed.lower()
                or not self.valid_typed(keys, typed, current)
                or self.excluded(current, typed)
                or self._matches(current, self.normalize(current, typed), 1)):
            return None
        exact = []
        for language in self.active:
            if language == current:
                continue
            candidate = self.mapper.word(keys, language)
            if (len(candidate) != len(keys) or not candidate.isalpha()
                    or candidate != candidate.lower() or not self.one_script(candidate)):
                continue
            normalized = self.normalize(language, candidate)
            matches = self._matches(language, normalized, 1)
            if not matches:
                continue
            if self.excluded(language, candidate) or matches[0] != normalized:
                return None
            exact.append((language, candidate))
        if len(exact) != 1:
            return None
        language, replacement = exact[0]
        return V2Decision(typed, replacement, language, 'unique rare exact word')

    def suggest_live_early(self, keys, current):
        exact = self._early(keys, current, unique_language=True)
        if exact is not None and self.certain(exact.language, exact.replacement):
            return exact
        confident = self._early(keys, current, unique_language=False)
        return confident if confident is not None else self._rare_exact(keys, current)

    def diagnose_early(self, keys, current):
        """Optional branch-level prefix explanation, without hook-side tracing."""
        from decision_diagnostics import DecisionExplanation
        reasons = []
        exact = self._early(keys, current, unique_language=True, _trace=reasons)
        if exact is not None and self.certain(exact.language, exact.replacement):
            return DecisionExplanation(True, exact.reason, exact.language,
                                       exact.replacement)
        confident = self._early(keys, current, unique_language=False, _trace=reasons)
        if confident is not None:
            return DecisionExplanation(True, confident.reason, confident.language,
                                       confident.replacement)
        rare = self._rare_exact(keys, current)
        if rare is not None:
            return DecisionExplanation(True, rare.reason, rare.language, rare.replacement)
        return DecisionExplanation(False, reasons[-1] if reasons else
                                   'prefix-not-uniquely-confident')
