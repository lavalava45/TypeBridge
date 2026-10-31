"""R13 research prototype: isolated, opt-in Russian transposition suggestions.

This module has no keyboard hooks, editor access, settings, or write operations.
The caller must establish safe, non-secret plain-text field context independently.
It must never be used as authorization to alter text in a live application.
"""

from collections.abc import Collection


RUSSIAN_LOWER = frozenset("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")


def suggest_adjacent_transposition(
    token: str,
    vocabulary: Collection[str],
    *,
    mode: str = "off",
    russian_confirmed: bool = False,
    field_kind: str = "unknown",
    exclusions: Collection[str] = (),
    protected_words: Collection[str] = (),
) -> tuple[str, ...]:
    """Return one suggestion or nothing, without changing the supplied token.

    Fail closed unless explicitly enabled in suggestion mode, Russian language
    is confirmed, and the caller verifies a safe ordinary text field. Input is
    limited to all-lowercase Russian words: other text is ineligible. All
    distinct adjacent swaps are examined, so ambiguities suppress the result.

    The future automatic mode needs a separately reviewed editor/Undo gate and
    is deliberately unsupported here even when ``mode='auto'`` is requested.
    """
    if mode not in {"off", "suggest", "auto"}:
        raise ValueError("Unsupported transposition mode")
    if mode != "suggest" or not russian_confirmed or field_kind != "plain_text":
        return ()
    if not isinstance(token, str) or not 3 <= len(token) <= 64:
        return ()
    if not all(letter in RUSSIAN_LOWER for letter in token):
        return ()

    forbidden = set(exclusions) | set(protected_words)
    # Existing words and explicit protected terms must always be preserved.
    if token in vocabulary or token in forbidden:
        return ()

    candidates = set()
    for i in range(len(token) - 1):
        if token[i] == token[i + 1]:
            continue
        candidate = token[:i] + token[i + 1] + token[i] + token[i + 2:]
        if candidate in vocabulary and candidate not in forbidden:
            candidates.add(candidate)
            if len(candidates) > 1:
                return ()
    return tuple(sorted(candidates))
