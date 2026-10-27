"""Read Windows input-switch shortcuts without changing system preferences."""

import os
import winreg


TOGGLE_KEY = r"Keyboard Layout\Toggle"
SETTINGS_URI = "ms-settings:typing"
UNKNOWN = "Не удалось определить"
NOT_CONFIGURED = "Дополнительное сочетание не указано в реестре"
WIN_SPACE = "Win+Space"
# No saved override does not mean that Windows disabled its default sequence.
# This is a labelled fallback, not proof of the currently active hotkey.
DEFAULT_LANGUAGE = "Левый Alt+Shift (стандартное; не подтверждено)"
SHORTCUTS = {"1": "Левый Alt+Shift", "2": "Ctrl+Shift", "3": "Не назначено"}


def read_windows_shortcuts() -> tuple[str, str]:
    """Report optional legacy shortcuts without hiding the built-in Win+Space.

    A missing value is not a registry read error. Win+Space is a Windows
    feature, not stored in the legacy Keyboard Layout\\Toggle values.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TOGGLE_KEY) as key:
            def read(name: str) -> str:
                try:
                    value, _kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    return NOT_CONFIGURED
                except OSError:
                    return UNKNOWN
                return SHORTCUTS.get(str(value), UNKNOWN)

            language = read("Language Hotkey")
            if language in (UNKNOWN, NOT_CONFIGURED):
                legacy = read("Hotkey")
                if legacy not in (UNKNOWN, NOT_CONFIGURED):
                    language = legacy + " (сохранено в Hotkey; применение Windows не подтверждено)"
                elif legacy == UNKNOWN:
                    language = UNKNOWN
            if language == NOT_CONFIGURED:
                language = DEFAULT_LANGUAGE
            elif language == 'Не назначено':
                language = 'Не назначено (дополнительно; Win+Space доступно)'
            elif language == UNKNOWN:
                language = WIN_SPACE + ' (параметры недоступны)'
            return language, read("Layout Hotkey")
    except FileNotFoundError:
        return DEFAULT_LANGUAGE, NOT_CONFIGURED
    except OSError:
        return WIN_SPACE + ' (параметры недоступны)', UNKNOWN


def open_windows_keyboard_settings() -> None:
    os.startfile(SETTINGS_URI)
