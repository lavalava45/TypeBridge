"""Optional per-user Windows logon registration; no elevation or task scheduler."""

from pathlib import Path
import subprocess
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "TypeBridge"
LEGACY_VALUE_NAME = "LayoutSwitch"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"


def _read_value(name, path=RUN_KEY):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def registration_state():
    return {VALUE_NAME: _read_value(VALUE_NAME), LEGACY_VALUE_NAME: _read_value(LEGACY_VALUE_NAME)}


def read_registration():
    state = registration_state()
    return state[VALUE_NAME] if state[VALUE_NAME] is not None else state[LEGACY_VALUE_NAME]


def startup_command():
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        # Existing shortcuts may still launch the former-name compatibility EXE.
        canonical = executable.with_name('TypeBridge.exe')
        if canonical.exists(): executable = canonical
        command = [str(executable), "--autostart"]
    else:
        windowless = executable.with_name("pythonw.exe")
        if not windowless.exists():
            raise OSError("Для автозагрузки нужен pythonw.exe или собранный TypeBridge.exe")
        command = [str(windowless), str(Path(__file__).with_name("typebridge_app.py")), "--autostart"]
    result = subprocess.list2cmdline(command)
    if len(result) > 260:
        raise ValueError("Слишком длинный путь для автозагрузки Windows")
    return result


def write_registration(value):
    # Do not recreate an unchanged registration during logon.
    state = registration_state()
    if state[VALUE_NAME] == value and state[LEGACY_VALUE_NAME] is None:
        return
    # Preserve a Windows Task Manager decision to disable the old startup item.
    if value is not None and state[LEGACY_VALUE_NAME] is not None:
        approved = _read_value(LEGACY_VALUE_NAME, APPROVED_KEY)
        if isinstance(approved, bytes) and _read_value(VALUE_NAME, APPROVED_KEY) is None:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_BINARY, approved)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if value is not None:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, value)
        elif state[VALUE_NAME] is not None:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError: pass
        if state[LEGACY_VALUE_NAME] is not None:
            try: winreg.DeleteValue(key, LEGACY_VALUE_NAME)
            except FileNotFoundError: pass
