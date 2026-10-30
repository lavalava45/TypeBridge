"""R16/4d read-only decision rejection codes; never writes to an editor.

This optional diagnostic is computed *after* the normal decision, never in
the global keyboard callback. It reports causes using the same observed
dictionary predicates for the old and unified backends.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DecisionExplanation:
    accepted: bool
    reason: str
    destination: str | None = None
    replacement: str | None = None


def explain_token(engine, keys, current, *, allow_rare=False, exact_policy=False):
    """Explain complete-token accept/rejection without changing any decision.

    Rejection codes are diagnostics, not persistent user settings. They are
    deliberately not emitted from the normal hook while a word is being typed.
    """
    # The policy evaluator can report the actual rejecting branch. The
    # independent legacy evaluator retains the historical diagnostic path.
    policy = getattr(engine, '_policy_engine', None)
    if exact_policy and policy is not None:
        return policy.diagnose_token(keys, current, allow_rare=allow_rare)
    decision = engine.decide_token(keys, current, allow_rare=allow_rare)
    if decision is not None:
        return DecisionExplanation(True, decision.reason, decision.language,
                                   decision.replacement)
    if not keys or len(keys) > 40:
        return DecisionExplanation(False, 'invalid-key-length')
    active = getattr(engine, 'active_languages', ('ru', 'en', 'he'))
    if current not in active:
        return DecisionExplanation(False, 'inactive-source-language')
    typed = engine.mapper.word(keys, current)
    if len(typed) != len(keys):
        return DecisionExplanation(False, 'unmapped-physical-keys')
    if engine._excluded(current, typed):
        return DecisionExplanation(False, 'excluded-source-word')
    if engine._known(current, typed) and not allow_rare:
        return DecisionExplanation(False, 'known-source-word')
    from engine import _one_script_letters
    candidates = []
    for language in active:
        if language == current:
            continue
        alternate = engine.mapper.word(keys, language)
        if (len(alternate) == len(keys)
                and _one_script_letters(alternate,
                    getattr(engine, 'language_catalog', None))
                and engine._known(language, alternate)):
            candidates.append((language, alternate))
    if len(candidates) > 1:
        policy = getattr(engine, '_policy_engine', None)
        if policy is not None:
            strict = any(p.behavior.ambiguous_destinations == 'reject_multiple'
                         for p in policy.packs.values())
        else:
            from language_registry import LANGUAGE_BY_CODE
            strict = bool(getattr(engine, 'language_catalog', None)) and any(
                language not in LANGUAGE_BY_CODE for language in active)
        if strict:
            return DecisionExplanation(False, 'ambiguous-destinations')
    if any(engine._excluded(lang, word) for lang, word in candidates):
        return DecisionExplanation(False, 'excluded-target-word')
    if candidates and getattr(engine, 'conservative', True) and not allow_rare:
        if any(len(value) > 1 and not engine._confident(lang, value)
               for lang, value in candidates):
            return DecisionExplanation(False, 'insufficient-target-confidence')
    if not candidates:
        return DecisionExplanation(False, 'no-matching-destination')
    return DecisionExplanation(False, 'candidate-rejected-by-word-boundary-or-policy')


def explain_early(engine, keys, current, *, exact_policy=False):
    """Early rejection codes, with no global hook install or side effects."""
    policy = getattr(engine, '_policy_engine', None)
    if exact_policy and policy is not None:
        return policy.diagnose_early(keys, current)
    from incremental_recognition import suggest_live_early, MIN_EARLY_LENGTH
    result = suggest_live_early(engine, keys, current)
    if result is not None:
        return DecisionExplanation(True, result.reason, result.language,
                                   result.replacement)
    if current not in getattr(engine, 'active_languages', ()):
        return DecisionExplanation(False, 'inactive-source-language')
    if not keys or len(keys) > 40:
        return DecisionExplanation(False, 'invalid-key-length')
    if len(keys) < MIN_EARLY_LENGTH:
        return DecisionExplanation(False, 'prefix-below-confidence-threshold')
    typed = engine.mapper.word(keys, current)
    if engine._excluded(current, typed):
        return DecisionExplanation(False, 'excluded-source-word')
    if engine._known(current, typed):
        return DecisionExplanation(False, 'known-source-prefix')
    return DecisionExplanation(False, 'prefix-not-uniquely-confident')
