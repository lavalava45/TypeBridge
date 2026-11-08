"""Ephemeral last-word state; no document reads, clipboard or persistence."""
from dataclasses import dataclass
from language_registry import DEFAULT_ACTIVE_LANGUAGES, validate_active_languages


CYCLE_KEYS = {"Pause": 0x13, "Scroll Lock": 0x91,
              **{f"F{number}": 0x6F + number for number in range(1, 13)},
              "Отключено": 0}
ORDER = DEFAULT_ACTIVE_LANGUAGES


def effective_word_key(settings) -> str:
    """One manual command; old two-key profiles migrate deterministically.

    An explicitly configured legacy cycle key wins over a custom manual key;
    a custom legacy manual key wins over the default Pause. A disabled cycle
    falls back to the enabled manual key. New word_key always takes precedence.
    """
    selected = getattr(settings, 'word_key', None)
    if selected is not None:
        return selected
    cycle = settings.cycle_key
    manual = settings.manual_key if settings.manual_f8 else 'Отключено'
    if cycle not in ('Pause', 'Отключено'):
        return cycle
    if cycle == 'Pause':
        return manual if manual not in ('Scroll Lock', 'Отключено') else cycle
    return manual


@dataclass
class RecentWord:
    keys: tuple[tuple[int, bool], ...]
    text: str
    language: str
    suffix: str
    scope: tuple
    # Absolute character offset within text; None means caret follows suffix.
    caret_offset: int | None = None

    def next_variant(self, mapper, active_languages=None) -> tuple[str, str]:
        order = (ORDER if active_languages is None
                 else validate_active_languages(active_languages, active_languages))
        if self.language not in order or len(order) < 2:
            raise ValueError("Последний язык неактивен или нет следующего активного языка")
        language = order[(order.index(self.language) + 1) % len(order)]
        text = mapper.word(list(self.keys), language)
        if (len(text) != len(self.keys) or not text
                or not all(c.isprintable() and not c.isspace() for c in text)):
            raise ValueError("Для следующей раскладки нет точного соответствия клавиш")
        return language, text


def remember_word(keys, text: str, language: str, suffix: str, scope: tuple,
                  *, whole: bool = False, active_languages=None,
                  caret_offset: int | None = None) -> RecentWord | None:
    order = ORDER if active_languages is None else validate_active_languages(
        active_languages, active_languages)
    if not keys or len(keys) != len(text) or language not in order:
        return None
    left, right = 0, len(text)
    if not whole:
        while left < right and not text[left].isalpha():
            left += 1
        while right > left and not text[right - 1].isalpha():
            right -= 1
    if left == right:
        return None
    if caret_offset is not None and not left <= caret_offset <= right:
        return None
    return RecentWord(tuple(keys[left:right]), text[left:right], language,
                      text[right:] + suffix, scope,
                      caret_offset - left if caret_offset is not None else None)
