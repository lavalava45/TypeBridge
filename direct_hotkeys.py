"""Parse local layout shortcuts; no hooks, registration or Windows side effects."""
from dataclasses import dataclass

DISABLED = "Отключено"
DEFAULTS = {"ru": "Ctrl+Shift+F1", "en": "Ctrl+Shift+F2", "he": "Ctrl+Shift+F3"}
MODIFIERS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10}
KEYS = {**{f"f{n}": 0x6F + n for n in range(1, 13)},
        **{c.lower(): ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"},
        "pause": 0x13, "scroll lock": 0x91}
SUGGESTIONS = (DISABLED, *(f"Ctrl+Shift+F{n}" for n in range(1, 13)),
               *(f"Ctrl+Alt+F{n}" for n in range(1, 13)))


@dataclass(frozen=True)
class Chord:
    vk: int
    modifiers: frozenset[int]


def parse_chord(value: str) -> Chord | None:
    if not isinstance(value, str):
        raise ValueError("Сочетание клавиш должно быть строкой")
    if not value.strip() or value.strip().casefold() == DISABLED.casefold():
        return None
    parts = [part.strip().casefold() for part in value.split("+")]
    if not parts or parts[-1] not in KEYS or any(part not in MODIFIERS for part in parts[:-1]):
        raise ValueError("Пример сочетания: Ctrl+Shift+F1 или Ctrl+Alt+1")
    mods = [MODIFIERS[part] for part in parts[:-1]]
    if len(mods) != len(set(mods)):
        raise ValueError("Модификатор указан дважды")
    vk = KEYS[parts[-1]]
    if (0x30 <= vk <= 0x5A) and not ({0x11, 0x12} & set(mods)):
        raise ValueError("Для буквы или цифры добавь Ctrl или Alt")
    return Chord(vk, frozenset(mods))


def compile_bindings(settings) -> dict[Chord, str]:
    from word_cycle import CYCLE_KEYS
    bindings = {}
    for lang in DEFAULTS:
        chord = parse_chord(getattr(settings, f"direct_{lang}"))
        if chord is None:
            continue
        if chord in bindings:
            raise ValueError("Одно сочетание назначено двум языкам")
        if chord.vk == 0x13 and {0x11, 0x12} <= chord.modifiers:
            raise ValueError("Ctrl+Alt+Pause занят включением/выключением паузы")
        if not chord.modifiers:
            if chord.vk == CYCLE_KEYS[settings.cycle_key]:
                raise ValueError("Клавиша занята ручным переключением слова")
            if chord.vk == 0x77 and settings.manual_f8:
                raise ValueError("F8 занят словарным исправлением")
        bindings[chord] = lang
    return bindings
