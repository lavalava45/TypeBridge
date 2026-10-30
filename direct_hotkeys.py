"""Parse local layout shortcuts; no hooks, registration or Windows side effects."""
from dataclasses import dataclass

DISABLED = "Отключено"
DEFAULTS = {"ru": "Ctrl+Shift+F1", "en": "Ctrl+Shift+F2", "he": "Ctrl+Shift+F3"}
MANUAL_KEYS = {"Scroll Lock": 0x91, **{f"F{n}": 0x6F + n for n in range(1, 13)},
               "Pause": 0x13, "Отключено": 0}
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


def compile_bindings(settings, catalog=None) -> dict[Chord, str]:
    from word_cycle import CYCLE_KEYS
    from language_registry import DEFAULT_ACTIVE_LANGUAGES, validate_active_languages
    active = validate_active_languages(getattr(settings, 'active_languages',
                                             DEFAULT_ACTIVE_LANGUAGES), catalog)
    extra = getattr(settings, 'direct_extra', ())
    universal = getattr(settings, 'language_shortcuts', ())
    if universal:
        if (not isinstance(universal, (tuple, list))
                or any(not isinstance(pair, (tuple, list)) or len(pair) != 2
                       or not all(isinstance(value, str) for value in pair)
                       for pair in universal)
                or len({code for code, _ in universal}) != len(universal)):
            raise ValueError('Invalid universal language shortcuts')
        from language_pack_loader import _CODE
        if any(not _CODE.fullmatch(code) for code, _ in universal):
            raise ValueError('Unsafe language shortcut code')
    if (not isinstance(extra, (tuple, list))
            or any(not isinstance(row, (list, tuple)) or len(row) != 2
                   or not all(isinstance(part, str) for part in row) for row in extra)):
        raise ValueError('Invalid external language hotkeys')
    values = dict(extra)
    if (len(values) != len(extra)
            or any(lang in DEFAULTS or (not universal and catalog is not None
                                        and lang not in catalog)
                   for lang in values)):
        raise ValueError('Unsupported or repeated external language hotkey')
    manual_key = settings.manual_key
    if not isinstance(manual_key, str) or manual_key not in MANUAL_KEYS:
        raise ValueError("Выбери поддерживаемую клавишу исправления слова")
    manual_vk = MANUAL_KEYS[manual_key] if settings.manual_f8 else 0
    if manual_vk and manual_vk == CYCLE_KEYS[settings.cycle_key]:
        raise ValueError("Клавиша уже занята перебором раскладок последнего слова")
    bindings = {}
    # A legacy engine may load saved shortcuts for inactive package languages;
    # never intercept them until the matching catalog and active set are live.
    candidates = (dict(universal) if universal else
                  {**{lang: getattr(settings, f'direct_{lang}') for lang in DEFAULTS},
                   **(values if catalog is not None else {})})
    for lang, value in candidates.items():
        # Missing inactive packages retain a shortcut but never intercept it.
        if lang not in active:
            continue
        chord = parse_chord(value)
        # Keep the saved shortcut for a disabled language, but do not intercept
        # its chord: another application or an active language can use it.
        if chord is None or lang not in active:
            continue
        if chord in bindings:
            raise ValueError("Одно сочетание назначено двум языкам")
        if chord.vk == 0x13 and {0x11, 0x12} <= chord.modifiers:
            raise ValueError("Ctrl+Alt+Pause занят включением/выключением паузы")
        if not chord.modifiers:
            if chord.vk == CYCLE_KEYS[settings.cycle_key]:
                raise ValueError("Клавиша занята ручным переключением слова")
            if manual_vk and chord.vk == manual_vk:
                raise ValueError("Клавиша занята словарным исправлением")
        bindings[chord] = lang
    from clipboard_tools import clipboard_bindings
    for chord in clipboard_bindings(settings):
        if chord in bindings:
            raise ValueError("Сочетание поиска или буфера уже назначено переключению языка")
        if chord.vk == 0x13 and {0x11, 0x12} <= chord.modifiers:
            raise ValueError("Ctrl+Alt+Pause уже занят")
    return bindings
