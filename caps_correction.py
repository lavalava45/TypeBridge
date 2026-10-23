"""Conservative accidental CapsLock detection, without input or OS calls."""

from engine import Decision


def caps_decision(engine, keys, language):
    if language not in ("ru", "en"):
        return None
    typed = engine.mapper.word(keys, language)
    if (not 2 <= len(typed) <= 40 or not typed.isalpha()
            or not typed[0].islower() or not typed[1:].isupper()
            or engine._excluded(language, typed)):
        return None
    replacement = typed[0].upper() + typed[1:].lower()
    if (not engine._known(language, replacement)
            or engine._excluded(language, replacement)):
        return None
    return Decision(typed, replacement, language, "accidental CapsLock")
