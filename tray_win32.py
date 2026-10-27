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
from time import monotonic
import ctypes
from ctypes import wintypes

import win32api
import win32con
import win32gui
from pywintypes import error as Win32Error
from tray_layouts import WindowsLayouts, SHELL_CLASSES
from ui_i18n import tr


WM_TRAY = win32con.WM_APP + 52
WM_SET_LANGUAGE = win32con.WM_APP + 53
WM_CLIPBOARD_CONFIG = win32con.WM_APP + 54
WM_RETRY_ICON = win32con.WM_APP + 55
MENU_OPEN, MENU_ENABLE, MENU_EXIT = 4001, 4002, 4005
MENU_SNIPPETS = 4006
MENU_HISTORY = 4007
MENU_CARET_FLAG = 4008
MENU_JOURNAL = 4009
MENU_RESTORE_INPUT = 4010
MENU_LAYOUT_BASE = 5000
ICON_LABELS = {"ru": "RU", "en": "EN", "he": "HE", "idle": "--"}
STATE_LABELS = {"stopped": "Автоисправление выключено", "starting": "Загрузка словарей…",
                "running": "Автоисправление включено", "paused": "Автоисправление выключено",
                "stopping": "Останавливается…", "error": "Ошибка запуска",
                "degraded": "Автоисправление ограничено (RichEdit)"}


def icon_registered(hwnd):
    """Ask Explorer about this exact icon; a live helper thread is insufficient."""
    class Identifier(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND),
                    ('uID', wintypes.UINT), ('guidItem', ctypes.c_ubyte * 16)]
    identifier = Identifier()
    identifier.cbSize = ctypes.sizeof(identifier)
    identifier.hWnd = hwnd
    rect = wintypes.RECT()
    query = ctypes.WinDLL('shell32').Shell_NotifyIconGetRect
    query.argtypes = [ctypes.POINTER(Identifier), ctypes.POINTER(wintypes.RECT)]
    query.restype = ctypes.c_long
    return query(ctypes.byref(identifier), ctypes.byref(rect)) == 0


def menu_items(state: str, detail: str = "", caret_flag: bool = False,
               journal_enabled: bool = False, recovery_available: bool = False):
    """One checked correction toggle; no separate stop/pause commands."""
    busy = state in ("starting", "stopping")
    status = STATE_LABELS[state]
    if state in ("error", "degraded") and detail:
        status = detail
    status_row = ((0, status[:180], False, False),) if busy or state in ("error", "degraded") else ()
    journal_row = ((0, "● Дневник включён", False, False),) if journal_enabled else ()
    recovery_row = ((MENU_RESTORE_INPUT, "Вернуть задержанные клавиши в исходное поле…",
                     True, False),) if recovery_available else ()
    items = status_row + journal_row + recovery_row + (
        (MENU_ENABLE, "Автоматически переключать раскладку", not busy,
         state in ("running", "degraded")),
        (MENU_CARET_FLAG, "Флажок у текстового курсора", True, caret_flag),
        (MENU_OPEN, "Открыть настройки", True, False),
        (MENU_SNIPPETS, "Автозамена и шаблоны…", True, False),
        (MENU_HISTORY, "История буфера обмена…", True, False),
        (MENU_JOURNAL, "Дневник ввода…", True, False),
        (MENU_EXIT, "Выход", True, False),
    )
    return tuple((code, tr(title), enabled, checked) for code, title, enabled, checked in items)


def _icon_files() -> dict[str, Path]:
    """Create our own flag artwork under the user's app data, not in Windows."""
    import os
    from PIL import Image, ImageDraw

    from app_paths import USER_DIR
    base = USER_DIR / 'icons'
    base.mkdir(parents=True, exist_ok=True)
    result = {}
    for language in ICON_LABELS:
        path = base / f"{language}.ico"
        result[language] = path
        if path.exists():
            try:
                with Image.open(path) as existing:
                    if existing.format != 'ICO':
                        raise ValueError('not an icon')
                    existing.verify()
                continue
            except (OSError, ValueError, SyntaxError):
                # A truncated or corrupt ICO used to permanently kill the
                # tray worker. Regenerate *only* our disposable icon art.
                pass
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
        temp = path.with_suffix('.ico.tmp')
        image.save(temp, format="ICO", sizes=[(16, 16), (32, 32), (64, 64)])
        os.replace(temp, path)
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
        self.journal_enabled = False
        self.native_recovery_available = False
        self.clipboard_settings = None
        self.clipboard_hotkeys = None
        self.next_recovery = 0.0
        self.tray_start_failures = 0
        self.last_fatal_error = None
        self.last_icon_status = None

    def configure_clipboard(self, settings):
        self.clipboard_settings = settings
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, WM_CLIPBOARD_CONFIG, 0, 0)

    def set_caret_flag(self, enabled):
        self.caret_flag = bool(enabled)

    def set_journal_enabled(self, enabled):
        self.journal_enabled = bool(enabled)
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, WM_SET_LANGUAGE, 0, 0)

    def set_native_recovery(self, enabled):
        self.native_recovery_available = bool(enabled)
        self.refresh_ui()

    def refresh_ui(self):
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, WM_SET_LANGUAGE, 0, 0)

    def _register_clipboard_hotkeys(self):
        from clipboard_hotkeys import ClipboardHotkeys
        if self.clipboard_settings is None:
            return
        if self.clipboard_hotkeys is None:
            self.clipboard_hotkeys = ClipboardHotkeys(self.layouts, self.notify)
        try:
            self.clipboard_hotkeys.configure(self.clipboard_settings)
        except OSError as error:
            self.notify(("tray-error", str(error)))

    def start(self):
        if self.closing or (self.worker and self.worker.is_alive()):
            return
        self.worker = threading.Thread(target=self._message_loop,
                                       name="TypeBridgeTray", daemon=True)
        self.worker.start()

    def maintain(self):
        """Queue recovery on the tray thread; never operate Tk or take focus."""
        if self.closing:
            return
        now = monotonic()
        if now < getattr(self, 'next_recovery', 0.0):
            return
        self.next_recovery = now + min(60.0, 2.0 * (2 ** min(5, getattr(self, 'tray_start_failures', 0))))
        if not self.worker or not self.worker.is_alive():
            self.start()
        elif self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, WM_RETRY_ICON, 0, 0)
            except (OSError, Win32Error):
                self.ready.clear()

    def _icon_status(self, message):
        if message != self.last_icon_status:
            self.last_icon_status = message
            self.notify(('tray-diagnostic', message))

    def _ensure_icon(self, *, update=False):
        """A temporarily unavailable Explorer must not kill the message loop."""
        if self.closing:
            return
        try:
            present = icon_registered(self.hwnd)
            if update or not present:
                operation = win32gui.NIM_MODIFY if present else win32gui.NIM_ADD
                try:
                    win32gui.Shell_NotifyIcon(operation, self._icon_data())
                except (OSError, Win32Error):
                    # An ADD can race Explorer's acknowledgement of an earlier
                    # ADD. MODIFY the same identity, never create a second one.
                    if operation != win32gui.NIM_ADD:
                        raise
                    win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self._icon_data())
                present = icon_registered(self.hwnd)
            if present:
                self.ready.set()
                self.tray_start_failures = 0
                self._icon_status('Значок трея подтверждён Explorer')
            else:
                self.ready.clear()
                self._icon_status('Explorer пока не подтвердил значок трея; повтор через 2 с')
        except (OSError, Win32Error, AttributeError) as error:
            self.ready.clear()
            self._icon_status('Регистрация значка трея: '
                              + str(error) + '; повтор через 2 с')

    def available(self):
        try:
            return bool(not self.closing and self.ready.is_set() and self.worker
                        and self.worker.is_alive() and self.hwnd
                        and win32gui.IsWindow(self.hwnd) and icon_registered(self.hwnd))
        except (OSError, Win32Error, AttributeError):
            return False

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
        status = tr(detail if state in ("error", "degraded") and detail else STATE_LABELS[state])
        return (self.hwnd, 0, win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                WM_TRAY, self.icons.get(self.language) or self.icons["idle"],
                f"TypeBridge — {tr('Дневник ВКЛ · ') if self.journal_enabled else ''}{status}"[:127])

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == WM_CLIPBOARD_CONFIG:
            self._register_clipboard_hotkeys()
            return 0
        if message == WM_TRAY:
            if lparam == win32con.WM_LBUTTONDBLCLK:
                self.notify("open")
            elif lparam == win32con.WM_RBUTTONUP:
                self._show_menu()
            return 0
        if message == WM_SET_LANGUAGE:
            self._ensure_icon(update=True)
            return 0
        if message == WM_RETRY_ICON:
            self._ensure_icon()
            return 0
        if message == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if message == win32con.WM_DESTROY:
            self.ready.clear()
            if self.clipboard_hotkeys:
                self.clipboard_hotkeys.close()
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, self._icon_data())
            except (OSError, Win32Error):
                pass  # Explorer may already be gone during shutdown.
            finally:
                win32gui.PostQuitMessage(0)
            return 0
        if message == self.taskbar_created:
            self.ready.clear()
            self._ensure_icon(update=True)
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
            win32gui.AppendMenu(menu, win32con.MF_POPUP, layout_menu, tr("Раскладка"))
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
                                        tr("Сначала выбери окно приложения"))
            except OSError as error:
                win32gui.AppendMenu(layout_menu, win32con.MF_GRAYED, 0, tr(str(error))[:180])
            insert_position = 0
            for code, title, enabled, checked in menu_items(*self.run_state, caret_flag=self.caret_flag,
                                                          journal_enabled=self.journal_enabled,
                                                          recovery_available=self.native_recovery_available):
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
                    MENU_CARET_FLAG: "toggle-caret-flag", MENU_JOURNAL: "journal", MENU_EXIT: "exit"}
        if chosen == MENU_HISTORY:
            self.notify(("history", target))
        if chosen == MENU_RESTORE_INPUT:
            try:
                self._return_to_target(target)
                self.notify('restore-native-input')
            except OSError as error:
                self.notify(('tray-error', str(error)))
            return
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
        registered_class = None
        try:
            self.taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = f"TypeBridgeTray{os.getpid()}_{threading.get_ident()}"
            wc.lpfnWndProc = self._wndproc
            win32gui.RegisterClass(wc)
            registered_class = wc.lpszClassName
            self.hwnd = win32gui.CreateWindow(wc.lpszClassName, "TypeBridgeTray",
                                               0, 0, 0, 0, 0, 0, 0, 0, None)
            for language, path in _icon_files().items():
                self.icons[language] = win32gui.LoadImage(
                    0, str(path), win32con.IMAGE_ICON, 32, 32,
                    win32con.LR_LOADFROMFILE)
            self._ensure_icon()
            self._register_clipboard_hotkeys()
            if self.closing:
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            win32gui.PumpMessages()
        except Exception as exc:
            self.tray_start_failures = getattr(self, 'tray_start_failures', 0) + 1
            self.next_recovery = monotonic() + min(60.0, 2.0 * (2 ** min(5, self.tray_start_failures)))
            error = str(exc)
            if error != getattr(self, 'last_fatal_error', None):
                self.last_fatal_error = error
                self.notify(("tray-error", error))
        finally:
            if self.clipboard_hotkeys:
                self.clipboard_hotkeys.close()
            self.ready.clear()
            if self.hwnd and win32gui.IsWindow(self.hwnd):
                win32gui.DestroyWindow(self.hwnd)
            self.hwnd = None
            if registered_class:
                win32gui.UnregisterClass(registered_class, None)
            for icon in self.icons.values():
                if icon:
                    win32gui.DestroyIcon(icon)
            self.icons.clear()
