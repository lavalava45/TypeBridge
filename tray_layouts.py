"""Enumerate loaded Windows layouts and request a layout for one saved window.

No layout is installed/loaded, no text is injected and no window is activated.
The tray restores the user's selected application separately, on menu selection.
"""
from dataclasses import dataclass
from functools import lru_cache
import ctypes
from ctypes import wintypes as w
import winreg
from engine import configured_layout_list


class GuiInfo(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD),
                ("hwndActive", w.HWND), ("hwndFocus", w.HWND),
                ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND), ("rect", w.RECT)]


@dataclass(frozen=True)
class LayoutChoice:
    hkl: int
    label: str


@dataclass(frozen=True)
class LayoutTarget:
    top: int
    focus: int
    thread: int
    pid: int
    host_pid: int = 0
    layout_window: int = 0
    layout_thread: int = 0


def layout_owner(api, top: int, focus: int, top_thread: int,
                 focus_thread: int) -> tuple[int, int]:
    """Use one foreground-window owner for both reading and switching HKL.

    Hosted input fields may belong to a different thread with a stale HKL.
    Keep their HWND for text operations, not for deciding the active language.
    This policy is shared by correction, direct hotkeys and the tray.
    """
    return top, top_thread


SHELL_CLASSES = frozenset(("Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                          "NotifyIconOverflowWindow", "Progman", "WorkerW",
                          "TopLevelWindowForOverflowXamlIsland"))


@lru_cache(maxsize=128)
def keyboard_name(hkl: int) -> str:
    language = hkl & 0xFFFF
    high = (hkl >> 16) & 0xFFFF
    base = r"SYSTEM\CurrentControlSet\Control\Keyboard Layouts"
    try:
        if high & 0xF000 == 0xF000:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    name = winreg.EnumKey(parent, index)
                    if not name.lower().endswith(f"{language:04x}"):
                        continue
                    with winreg.OpenKey(parent, name) as entry:
                        try:
                            identifier = int(winreg.QueryValueEx(entry, "Layout Id")[0], 16)
                        except (OSError, ValueError):
                            continue
                        if identifier == high & 0xFFF:
                            return str(winreg.QueryValueEx(entry, "Layout Text")[0])
        else:
            klid = f"{0 if high == language else high:04x}{language:04x}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + klid) as entry:
                return str(winreg.QueryValueEx(entry, "Layout Text")[0])
    except OSError:
        pass
    return f"{hkl & 0xFFFFFFFF:08X}"


class WindowsLayouts:
    def __init__(self):
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(w.HANDLE)]
        self.u.GetKeyboardLayoutList.restype = ctypes.c_int
        self.u.GetKeyboardLayout.argtypes = [w.DWORD]
        self.u.GetKeyboardLayout.restype = w.HANDLE
        self.u.GetForegroundWindow.restype = w.HWND
        self.u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.u.GetWindowThreadProcessId.restype = w.DWORD
        self.u.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GuiInfo)]
        self.u.GetGUIThreadInfo.restype = w.BOOL
        self.u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.u.GetClassNameW.restype = ctypes.c_int
        self.u.IsWindow.argtypes = [w.HWND]
        self.u.IsWindow.restype = w.BOOL
        self.u.IsChild.argtypes = [w.HWND, w.HWND]
        self.u.IsChild.restype = w.BOOL
        self.u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM,
            ctypes.c_ssize_t, w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.u.SendMessageTimeoutW.restype = ctypes.c_size_t
        self.k.GetLocaleInfoW.argtypes = [w.DWORD, w.DWORD, w.LPWSTR, ctypes.c_int]
        self.k.GetLocaleInfoW.restype = ctypes.c_int

    def installed(self) -> list[LayoutChoice]:
        count = self.u.GetKeyboardLayoutList(0, None)
        if count <= 0:
            raise OSError("Windows не вернула список раскладок")
        handles = (w.HANDLE * count)()
        found = self.u.GetKeyboardLayoutList(count, handles)
        if found <= 0:
            raise OSError("Не удалось прочитать список раскладок")
        result = []
        configured = {hkl for _, _, hkl in configured_layout_list()}
        for hkl in dict.fromkeys(int(x) for x in handles[:found] if x):
            if configured and hkl & 0xFFFFFFFF not in configured:
                continue
            language = ctypes.create_unicode_buffer(128)
            self.k.GetLocaleInfoW(hkl & 0xFFFF, 2, language, len(language))
            name = keyboard_name(hkl)
            label = f"{language.value} — {name}" if language.value else name
            result.append(LayoutChoice(hkl, label))
        return result

    def capture(self) -> LayoutTarget | None:
        top = int(self.u.GetForegroundWindow() or 0)
        if not top:
            return None
        cls = ctypes.create_unicode_buffer(128)
        self.u.GetClassNameW(top, cls, len(cls))
        if cls.value in SHELL_CLASSES or cls.value.startswith("TypeBridgeTray"):
            return None
        pid = w.DWORD()
        thread = self.u.GetWindowThreadProcessId(top, ctypes.byref(pid))
        if not thread or not pid.value:
            return None
        info = GuiInfo(cbSize=ctypes.sizeof(GuiInfo))
        focus = top
        if self.u.GetGUIThreadInfo(thread, ctypes.byref(info)):
            focus = int(info.hwndFocus or top)
        focus_pid = w.DWORD()
        focus_thread = self.u.GetWindowThreadProcessId(focus, ctypes.byref(focus_pid))
        if (not focus_pid.value or not focus_thread
                or (focus != top and not self.u.IsChild(top, focus))):
            return None
        owner, owner_thread = layout_owner(self.u, top, focus, int(thread), int(focus_thread))
        return LayoutTarget(top, focus, int(focus_thread), focus_pid.value, pid.value,
                            owner, owner_thread)

    def valid(self, target: LayoutTarget | None) -> bool:
        if target is None or not self.u.IsWindow(target.top) or not self.u.IsWindow(target.focus):
            return False
        top_pid, focus_pid = w.DWORD(), w.DWORD()
        self.u.GetWindowThreadProcessId(target.top, ctypes.byref(top_pid))
        thread = self.u.GetWindowThreadProcessId(target.focus, ctypes.byref(focus_pid))
        return (top_pid.value == (target.host_pid or target.pid)
                and focus_pid.value == target.pid and thread == target.thread
                and (target.focus == target.top or bool(self.u.IsChild(target.top, target.focus))))

    def current(self, target: LayoutTarget | None) -> int:
        return int(self.u.GetKeyboardLayout(target.layout_thread or target.thread) or 0) if self.valid(target) else 0

    def request(self, target: LayoutTarget, hkl: int) -> None:
        if not self.valid(target):
            raise OSError("Окно для переключения раскладки уже закрыто")
        if hkl not in {layout.hkl for layout in self.installed()}:
            raise OSError("Выбранная раскладка больше не установлена")
        if self.current(target) & 0xFFFFFFFF == hkl & 0xFFFFFFFF:
            return
        result = ctypes.c_size_t()
        self.u.SendMessageTimeoutW(target.layout_window or target.focus, 0x0050, 0, ctypes.c_ssize_t(hkl).value,
                                  3, 250, ctypes.byref(result))
        # Some applications return 0 despite switching; inspect the actual HKL.
        if self.current(target) & 0xFFFFFFFF != hkl & 0xFFFFFFFF:
            raise OSError("Приложение не подтвердило смену раскладки")
