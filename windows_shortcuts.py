"""Read Windows input-switch shortcuts without changing system preferences."""

import os
import winreg


TOGGLE_KEY = r"Keyboard Layout\Toggle"
SETTINGS_URI = "ms-settings:typing"
UNKNOWN = "Не удалось определить"
SHORTCUTS = {"1": "Левый Alt+Shift", "2": "Ctrl+Shift", "3": "Не назначено"}


def read_windows_shortcuts() -> tuple[str, str]:
    """Return language and same-language layout shortcuts, independently."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TOGGLE_KEY) as key:
            def read(name: str) -> str:
                try:
                    value, _kind = winreg.QueryValueEx(key, name)
                except OSError:
                    return UNKNOWN
                return SHORTCUTS.get(str(value), UNKNOWN)

            return read("Language Hotkey"), read("Layout Hotkey")
    except OSError:
        return UNKNOWN, UNKNOWN


def open_windows_keyboard_settings() -> None:
    os.startfile(SETTINGS_URI)
