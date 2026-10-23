"""Ephemeral last-word state; no document reads, clipboard or persistence."""
from dataclasses import dataclass


CYCLE_KEYS = {"Pause": 0x13, "F6": 0x75, "F7": 0x76, "F9": 0x78,
              "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
              "Scroll Lock": 0x91, "Отключено": 0}
ORDER = ("ru", "en", "he")


@dataclass
class RecentWord:
    keys: tuple[tuple[int, bool], ...]
    text: str
    language: str
    suffix: str
    scope: tuple

    def next_variant(self, mapper) -> tuple[str, str]:
        language = ORDER[(ORDER.index(self.language) + 1) % len(ORDER)]
        text = mapper.word(list(self.keys), language)
        if (len(text) != len(self.keys) or not text
                or not all(c.isprintable() and not c.isspace() for c in text)):
            raise ValueError("Для следующей раскладки нет точного соответствия клавиш")
        return language, text


def remember_word(keys, text: str, language: str, suffix: str, scope: tuple,
                  *, whole: bool = False) -> RecentWord | None:
    if not keys or len(keys) != len(text) or language not in ORDER:
        return None
    left, right = 0, len(text)
    if not whole:
        while left < right and not text[left].isalpha():
            left += 1
        while right > left and not text[right - 1].isalpha():
            right -= 1
    if left == right:
        return None
    return RecentWord(tuple(keys[left:right]), text[left:right], language,
                      text[right:] + suffix, scope)
