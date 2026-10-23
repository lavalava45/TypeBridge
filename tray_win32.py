"""Native Win32 notification-area integration, independent of keyboard hooks.

The tray owns an invisible message-only-ish helper window on its own thread.
It never activates or moves another application window. Menu selections are
forwarded to the Tk owner's queue rather than invoking Tk from this thread.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading

import win32api
import win32con
import win32gui
from tray_layouts import WindowsLayouts, SHELL_CLASSES


WM_TRAY = win32con.WM_APP + 52
WM_SET_LANGUAGE = win32con.WM_APP + 53
WM_CLIPBOARD_CONFIG = win32con.WM_APP + 54
MENU_OPEN, MENU_ENABLE, MENU_EXIT = 4001, 4002, 4005
MENU_SNIPPETS = 4006
MENU_HISTORY = 4007
MENU_CARET_FLAG = 4008
MENU_LAYOUT_BASE = 5000
ICON_LABELS = {"ru": "RU", "en": "EN", "he": "HE", "idle": "--"}
STATE_LABELS = {"stopped": "Автоисправление выключено", "starting": "Загрузка словарей…",
                "running": "Автоисправление включено", "paused": "Автоисправление выключено",
                "stopping": "Останавливается…", "error": "Ошибка запуска"}


def menu_items(state: str, detail: str = "", caret_flag: bool = False):
    """One checked correction toggle; no separate stop/pause commands."""
    busy = state in ("starting", "stopping")
    status = STATE_LABELS[state]
    if state == "error" and detail:
        status = detail
    status_row = ((0, status[:180], False, False),) if busy or state == "error" else ()
    return status_row + (
        (MENU_ENABLE, "Автоисправление", not busy, state == "running"),
        (MENU_CARET_FLAG, "Флажок у текстового курсора", True, caret_flag),
        (MENU_OPEN, "Открыть настройки", True, False),
        (MENU_SNIPPETS, "Автозамена и шаблоны…", True, False),
        (MENU_HISTORY, "История буфера обмена…", True, False),
        (MENU_EXIT, "Выход", True, False),
    )


def _icon_files() -> dict[str, Path]:
    """Create our own flag artwork under the user's app data, not in Windows."""
    from PIL import Image, ImageDraw

    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "TypeBridge" / "icons"
    base.mkdir(parents=True, exist_ok=True)
    result = {}
    for language in ICON_LABELS:
        path = base / f"{language}.ico"
        result[language] = path
        if path.exists():
            continue
        image = Image.new("RGBA", (64, 64), (248, 248, 248, 255))
        draw = ImageDraw.Draw(image)
        if language == "ru":
            draw.rectangle((1, 1, 62, 21), fill="white")
            draw.rectangle((1, 22, 62, 42), fill=(0, 57, 166))
            draw.rectangle((1, 43, 62, 62), fill=(213, 43, 30))
        elif language == "en":
            draw.rectangle((1, 1, 62, 62), fill=(0, 36, 125))
            draw.polygon([(1, 1), (10, 1), (62, 53), (62, 62),
                          (53, 62), (1, 10)], fill="white")
            draw.polygon([(62, 1), (53, 1), (1, 53), (1, 62),
                          (10, 62), (62, 10)], fill="white")
            draw.rectangle((25, 1, 38, 62), fill="white")
            draw.rectangle((1, 25, 62, 38), fill="white")
            draw.rectangle((29, 1, 34, 62), fill=(200, 16, 46))
            draw.rectangle((1, 29, 62, 34), fill=(200, 16, 46))
        elif language == "he":
            draw.rectangle((1, 1, 62, 62), fill="white")
            draw.rectangle((1, 12, 62, 19), fill=(0, 56, 184))
            draw.rectangle((1, 44, 62, 51), fill=(0, 56, 184))
            # Central Magen David, two interlaced triangles.
            points_a = [(32, 23), (21, 40), (43, 40)]
            points_b = [(32, 43), (21, 26), (43, 26)]
            draw.line(points_a + [points_a[0]], fill=(0, 56, 184), width=3)
            draw.line(points_b + [points_b[0]], fill=(0, 56, 184), width=3)
        else:
            draw.ellipse((8, 8, 56, 56), fill=(100, 100, 100))
        draw.rectangle((0, 0, 63, 63), outline=(65, 65, 65), width=1)
        image.save(path, format="ICO", sizes=[(16, 16), (32, 32), (64, 64)])
    return result


class TrayController:
    """Start explicitly in a desktop GUI; no hook or background typing."""

    def __init__(self, notify):
        self.notify = notify  # Receives one of: open, toggle, stop, exit.
        self.hwnd = None
        self.language = "idle"
        self.icons = {}
        self.closing = False
        self.worker = None
        self.ready = threading.Event()
        self.run_state = ("stopped", "")
        self.layouts = WindowsLayouts()
        self.last_target = None
        self.caret_flag = False
        self.clipboard_settings = None
        self.clipboard_hotkeys = {}

    def configure_clipboard(self, settings):
        self.clipboard_settings = settings
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, WM_CLIPBOARD_CONFIG, 0, 0)

    def set_caret_flag(self, enabled):
        self.caret_flag = bool(enabled)

    def _register_clipboard_hotkeys(self):
        from clipboard_tools import clipboard_bindings
        for identifier in self.clipboard_hotkeys:
            win32gui.UnregisterHotKey(self.hwnd, identifier)
        self.clipboard_hotkeys.clear()
        if self.clipboard_settings is None:
            return
        for identifier, (chord, action) in enumerate(clipboard_bindings(self.clipboard_settings).items(), 6100):
            flags = 0x4000  # MOD_NOREPEAT
            for key, flag in ((0x11, 2), (0x12, 1), (0x10, 4)):
                if key in chord.modifiers:
                    flags |= flag
            try:
                win32gui.RegisterHotKey(self.hwnd, identifier, flags, chord.vk)
                self.clipboard_hotkeys[identifier] = action
            except Exception:
                self.notify(("tray-error", "Горячая клавиша буфера занята; выбери другое сочетание в настройках"))

    def start(self):
        if self.worker:
            return
        self.worker = threading.Thread(target=self._message_loop,
                                       name="TypeBridgeTray", daemon=True)
        self.worker.start()

    def set_language(self, language: str):
        if language not in ICON_LABELS or self.closing:
            return
        self.language = language
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, WM_SET_LANGUAGE, 0, 0)

    def stop(self):
        self.closing = True
        self.ready.clear()
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)

    def set_state(self, state: str, detail: str = ""):
        if state not in STATE_LABELS or self.closing:
            return
        current = (state, detail)
        if current == self.run_state:
            return
        self.run_state = current
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, WM_SET_LANGUAGE, 0, 0)

    def observe_foreground(self):
        target = self.layouts.capture()
        if target is not None:
            self.last_target = target

    def _return_to_target(self, target):
        if not self.layouts.valid(target):
            raise OSError("Сначала выбери окно, в котором нужно изменить раскладку")
        foreground = win32gui.GetForegroundWindow()
        if (foreground not in (self.hwnd, target.top)
                and win32gui.GetClassName(foreground) not in SHELL_CLASSES):
            raise OSError("Активное окно изменилось; выбери раскладку ещё раз")
        win32gui.SetForegroundWindow(target.top)
        if win32gui.GetForegroundWindow() != target.top:
            raise OSError("Не удалось вернуть фокус выбранному приложению")

    def switch_layout(self, target, choice):
        self._return_to_target(target)
        self.layouts.request(target, choice.hkl)

    def _icon_data(self):
        state, detail = self.run_state
        status = detail if state == "error" and detail else STATE_LABELS[state]
        return (self.hwnd, 0, win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                WM_TRAY, self.icons.get(self.language) or self.icons["idle"],
                f"TypeBridge — {status}"[:127])

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == WM_CLIPBOARD_CONFIG:
            self._register_clipboard_hotkeys()
            return 0
        if message == win32con.WM_HOTKEY:
            action = self.clipboard_hotkeys.get(wparam)
            if action:
                self.notify((action, self.layouts.capture()))
            return 0
        if message == WM_TRAY:
            if lparam == win32con.WM_LBUTTONDBLCLK:
                self.notify("open")
            elif lparam == win32con.WM_RBUTTONUP:
                self._show_menu()
            return 0
        if message == WM_SET_LANGUAGE:
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self._icon_data())
            return 0
        if message == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if message == win32con.WM_DESTROY:
            for identifier in self.clipboard_hotkeys:
                win32gui.UnregisterHotKey(hwnd, identifier)
            self.clipboard_hotkeys.clear()
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, self._icon_data())
            win32gui.PostQuitMessage(0)
            return 0
        if message == self.taskbar_created:
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self._icon_data())
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _show_menu(self):
        self.observe_foreground()
        target = self.last_target
        choices = {}
        menu = win32gui.CreatePopupMenu()
        try:
            layout_menu = win32gui.CreatePopupMenu()
            # Attach immediately: destroying the parent then owns its cleanup.
            win32gui.AppendMenu(menu, win32con.MF_POPUP, layout_menu, "Раскладка")
            try:
                current_hkl = self.layouts.current(target)
                for index, choice in enumerate(self.layouts.installed()):
                    code = MENU_LAYOUT_BASE + index
                    choices[code] = choice
                    flags = win32con.MF_STRING
                    if not self.layouts.valid(target):
                        flags |= win32con.MF_GRAYED
                    if choice.hkl & 0xFFFFFFFF == current_hkl & 0xFFFFFFFF:
                        flags |= win32con.MF_CHECKED
                    win32gui.AppendMenu(layout_menu, flags, code, choice.label)
                if not self.layouts.valid(target):
                    win32gui.AppendMenu(layout_menu, win32con.MF_GRAYED, 0,
                                        "Сначала выбери окно приложения")
            except OSError as error:
                win32gui.AppendMenu(layout_menu, win32con.MF_GRAYED, 0, str(error)[:180])
            insert_position = 0
            for code, title, enabled, checked in menu_items(*self.run_state, caret_flag=self.caret_flag):
                flags = win32con.MF_STRING
                if not enabled:
                    flags |= win32con.MF_GRAYED
                if checked:
                    flags |= win32con.MF_CHECKED
                if code in (0, MENU_ENABLE):
                    win32gui.InsertMenu(menu, insert_position,
                                        flags | win32con.MF_BYPOSITION, code, title)
                    insert_position += 1
                else:
                    win32gui.AppendMenu(menu, flags, code, title)
            x, y = win32gui.GetCursorPos()
            win32gui.SetForegroundWindow(self.hwnd)
            chosen = win32gui.TrackPopupMenu(
                menu, win32con.TPM_LEFTALIGN | win32con.TPM_RETURNCMD |
                win32con.TPM_NONOTIFY, x, y, 0, self.hwnd, None)
        finally:
            win32gui.DestroyMenu(menu)
        commands = {MENU_OPEN: "open", MENU_SNIPPETS: "snippets", MENU_ENABLE: "toggle",
                    MENU_CARET_FLAG: "toggle-caret-flag", MENU_EXIT: "exit"}
        if chosen == MENU_HISTORY:
            self.notify(("history", target))
        if chosen in (0, MENU_ENABLE, MENU_CARET_FLAG) and self.layouts.valid(target):
            try:
                self._return_to_target(target)
            except OSError:
                pass  # Never move focus away from a newly selected application.
            except Exception:
                pass
        if chosen in commands:
            self.notify(commands[chosen])
        elif chosen in choices:
            try:
                self.switch_layout(target, choices[chosen])
                self.notify(("layout-changed", choices[chosen].label))
            except OSError as error:
                self.notify(("tray-error", str(error)))
            except Exception as error:
                self.notify(("tray-error", f"Не удалось сменить раскладку: {error}"))

    def _message_loop(self):
        try:
            self.taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = f"TypeBridgeTray{os.getpid()}_{threading.get_ident()}"
            wc.lpfnWndProc = self._wndproc
            win32gui.RegisterClass(wc)
            self.hwnd = win32gui.CreateWindow(wc.lpszClassName, "TypeBridgeTray",
                                               0, 0, 0, 0, 0, 0, 0, 0, None)
            for language, path in _icon_files().items():
                self.icons[language] = win32gui.LoadImage(
                    0, str(path), win32con.IMAGE_ICON, 32, 32,
                    win32con.LR_LOADFROMFILE)
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self._icon_data())
            self._register_clipboard_hotkeys()
            self.ready.set()
            if self.closing:
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            win32gui.PumpMessages()
        except Exception as exc:
            self.notify(("tray-error", str(exc)))
        finally:
            self.ready.clear()
            self.hwnd = None
            for icon in self.icons.values():
                if icon:
                    win32gui.DestroyIcon(icon)
            self.icons.clear()
