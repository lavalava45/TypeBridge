"""Read-only caret tracking and a non-activating, click-through language flag.

No keyboard hooks, document text, clipboard or input injection. Accessibility
queries run separately so a stalled provider cannot freeze the UI or leave a
stale flag visible. A missing caret always means hide, never use mouse position.
"""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes as w
import os
import threading
from time import monotonic
import uuid

import pythoncom
import win32api
import win32con
import win32gui

from engine import LANGS
from tray_layouts import GuiInfo, SHELL_CLASSES, layout_owner
from tray_win32 import _icon_files


class VariantValue(ctypes.Union):
    _fields_ = [("number", w.LONG), ("record", ctypes.c_void_p * 2)]


class Variant(ctypes.Structure):
    _fields_ = [("vt", w.WORD), ("reserved", w.WORD * 3), ("value", VariantValue)]


@dataclass(frozen=True)
class CaretSample:
    top: int
    focus: int
    rect: tuple[int, int, int, int]
    language: str
    work: tuple[int, int, int, int]
    dpi: int
    sampled_at: float


def flag_position(rect, work, width, height, gap=6):
    left, top, right, bottom = rect
    x = right + gap
    if x + width > work[2]:
        x = left - width - gap
    y = top - height - gap
    if y < work[1]:
        y = bottom + gap
    return (max(work[0], min(x, work[2] - width)),
            max(work[1], min(y, work[3] - height)))


def usable_rect(rect, bounds):
    left, top, right, bottom = rect
    return (0 <= right - left <= 80 and 0 < bottom - top <= 200
            and bounds[0] <= left <= right <= bounds[2]
            and bounds[1] <= top < bottom <= bounds[3])


class CaretReader:
    def __init__(self):
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        self.u.GetForegroundWindow.restype = w.HWND
        self.u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.u.GetWindowThreadProcessId.restype = w.DWORD
        self.u.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GuiInfo)]
        self.u.GetKeyboardLayout.argtypes = [w.DWORD]
        self.u.GetKeyboardLayout.restype = w.HANDLE
        self.u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM,
            ctypes.c_ssize_t, w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.u.SendMessageTimeoutW.restype = ctypes.c_size_t
        self.u.GetDpiForWindow.argtypes = [w.HWND]
        self.u.GetDpiForWindow.restype = w.UINT
        self.oleacc = ctypes.WinDLL("oleacc")
        self.oleacc.AccessibleObjectFromWindow.argtypes = [w.HWND, w.DWORD,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.oleacc.AccessibleObjectFromWindow.restype = w.LONG
        self.oleaut = ctypes.WinDLL("oleaut32")
        self.oleaut.VariantClear.argtypes = [ctypes.POINTER(Variant)]
        self.iid = ctypes.create_string_buffer(uuid.UUID("618736e0-3c3d-11cf-810c-00aa00389b71").bytes_le, 16)

    def _msg(self, hwnd, code, wp=0, lp=0):
        result = ctypes.c_size_t()
        if not self.u.SendMessageTimeoutW(hwnd, code, wp, lp, 3, 10, ctypes.byref(result)):
            raise OSError("caret-message-timeout")
        return ctypes.c_ssize_t(result.value).value

    def _accessible_caret(self, hwnd):
        pointer = ctypes.c_void_p()
        if self.oleacc.AccessibleObjectFromWindow(hwnd, 0xFFFFFFF8, self.iid,
                                                ctypes.byref(pointer)) != 0 or not pointer:
            return None
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        child = Variant(vt=3)  # VT_I4 / CHILDID_SELF, no strings allocated/read.
        state = Variant()
        try:
            get_state = ctypes.WINFUNCTYPE(w.LONG, ctypes.c_void_p, Variant,
                                           ctypes.POINTER(Variant))(vtable[14])
            if get_state(pointer, child, ctypes.byref(state)) != 0:
                return None
            if state.vt != 3 or state.value.number & (0x8000 | 0x10000):
                return None  # Invisible / offscreen.
            values = [w.LONG() for _ in range(4)]
            location = ctypes.WINFUNCTYPE(w.LONG, ctypes.c_void_p,
                *([ctypes.POINTER(w.LONG)] * 4), Variant)(vtable[22])
            if location(pointer, *(ctypes.byref(value) for value in values), child) != 0:
                return None
            x, y, width, height = (value.value for value in values)
            return x, y, x + width, y + height
        finally:
            self.oleaut.VariantClear(ctypes.byref(state))
            ctypes.WINFUNCTYPE(w.ULONG, ctypes.c_void_p)(vtable[2])(pointer)

    def snapshot(self):
        started = monotonic()
        top = int(self.u.GetForegroundWindow() or 0)
        if not top or not win32gui.IsWindowVisible(top):
            return None
        thread = self.u.GetWindowThreadProcessId(top, None)
        info = GuiInfo(cbSize=ctypes.sizeof(GuiInfo))
        if not self.u.GetGUIThreadInfo(thread, ctypes.byref(info)) or info.flags & 0x1E:
            return None
        focus = int(info.hwndFocus or 0)
        if not focus or not win32gui.IsWindowVisible(focus):
            return None
        top_class = win32gui.GetClassName(top)
        if top_class in SHELL_CLASSES or top_class.startswith("TypeBridge"):
            return None
        if focus != top and not win32gui.IsChild(top, focus):
            return None
        focus_thread = self.u.GetWindowThreadProcessId(focus, None)
        _, owner_thread = layout_owner(self.u, top, focus, thread, focus_thread)
        langid = int(self.u.GetKeyboardLayout(owner_thread) or 0) & 0xFFFF
        language = next((lang for lang, value in LANGS.items() if value == langid), "")
        if not language:
            return None
        bounds = win32gui.GetClientRect(focus)
        origin = win32gui.ClientToScreen(focus, (bounds[0], bounds[1]))
        far = win32gui.ClientToScreen(focus, (bounds[2], bounds[3]))
        bounds = (*origin, *far)
        rect = None
        if win32gui.GetClassName(focus) == "Scintilla":
            if (self._msg(focus, 2570) != 1 or self._msg(focus, 2581)
                    or self._msg(focus, 2583)):
                return None
            pos = self._msg(focus, 2008)
            x, y = self._msg(focus, 2164, 0, pos), self._msg(focus, 2165, 0, pos)
            height = self._msg(focus, 2279, self._msg(focus, 2166, pos))
            x, y = win32gui.ClientToScreen(focus, (x, y))
            rect = (x, y, x + 1, y + height)
        else:
            if focus_thread != thread:
                child_info = GuiInfo(cbSize=ctypes.sizeof(GuiInfo))
                if self.u.GetGUIThreadInfo(focus_thread, ctypes.byref(child_info)):
                    info = child_info
            caret = int(info.hwndCaret or 0)
            if caret and (caret == focus or win32gui.IsChild(focus, caret)):
                r = info.rect
                a = win32gui.ClientToScreen(caret, (r.left, r.top))
                b = win32gui.ClientToScreen(caret, (r.right, r.bottom))
                rect = (*a, *b)
            if rect is None or not usable_rect(rect, bounds):
                rect = self._accessible_caret(focus)
        if rect is None or not usable_rect(rect, bounds) or monotonic() - started > 0.15:
            return None
        # Do not publish a slow provider's answer for a window no longer active.
        check = GuiInfo(cbSize=ctypes.sizeof(GuiInfo))
        if (int(self.u.GetForegroundWindow() or 0) != top
                or not self.u.GetGUIThreadInfo(thread, ctypes.byref(check))
                or int(check.hwndFocus or 0) != focus or check.flags & 0x1E):
            return None
        monitor = win32api.MonitorFromPoint((rect[0], rect[1]), 2)
        return CaretSample(top, focus, rect, language, win32api.GetMonitorInfo(monitor)["Work"],
                           int(self.u.GetDpiForWindow(top) or 96), monotonic())


WM_REFRESH = win32con.WM_APP + 75
OVERLAY_STYLE = (win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW
                 | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_LAYERED
                 | win32con.WS_EX_TRANSPARENT)


class CaretFlagIndicator:
    def __init__(self):
        self.enabled = threading.Event()
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.sample = None
        self.hwnd = None
        self.icons = {}
        self.language = ""
        self.size = (22, 16)
        self.error = ""

    def start(self):
        threading.Thread(target=self._window_loop, daemon=True, name="caret-flag-window").start()
        threading.Thread(target=self._read_loop, daemon=True, name="caret-flag-reader").start()

    def set_enabled(self, enabled):
        (self.enabled.set if enabled else self.enabled.clear)()
        if not enabled:
            with self.lock:
                self.sample = None
        if self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, WM_REFRESH, 0, 0)
            except win32gui.error:
                pass

    def stop(self):
        self.enabled.clear()
        self.closed.set()
        if self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            except win32gui.error:
                pass

    def _read_loop(self):
        from caret_process import CaretProcess
        reader = CaretProcess()
        try:
            while not self.closed.is_set():
                sample = None
                try:
                    top = win32gui.GetForegroundWindow() if self.enabled.is_set() else 0
                    if top:
                        sample = reader.snapshot(scope=top, cancelled=lambda:
                            self.closed.is_set() or not self.enabled.is_set()
                            or win32gui.GetForegroundWindow() != top)
                        self.reader_error = reader.last_error
                    else:
                        reader.cancel()
                        reader.scope = None
                except Exception as error:
                    self.reader_error = type(error).__name__
                    reader.cancel()
                with self.lock:
                    self.sample = sample
                self.closed.wait(0.10)
        except Exception as error:
            self.error = type(error).__name__
        finally:
            reader.close()

    def _tick(self):
        with self.lock:
            sample = self.sample
        if (not self.enabled.is_set() or sample is None
                or monotonic() - sample.sampled_at > 0.4
                or win32gui.GetForegroundWindow() != sample.top):
            win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)
            return
        scale = max(1, min(3, sample.dpi / 96))
        width, height = round(22 * scale), round(16 * scale)
        x, y = flag_position(sample.rect, sample.work, width, height, round(6 * scale))
        repaint = self.language != sample.language or self.size != (width, height)
        self.language, self.size = sample.language, (width, height)
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, x, y, width, height,
                             win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
        if repaint:
            win32gui.InvalidateRect(self.hwnd, None, True)

    def _wndproc(self, hwnd, message, wp, lp):
        if message in (WM_REFRESH, win32con.WM_TIMER):
            try:
                self._tick()
            except Exception:
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            return 0
        if message == win32con.WM_MOUSEACTIVATE:
            return win32con.MA_NOACTIVATE
        if message == win32con.WM_NCHITTEST:
            return win32con.HTTRANSPARENT
        if message == win32con.WM_PAINT:
            dc, paint = win32gui.BeginPaint(hwnd)
            try:
                if self.language in self.icons:
                    win32gui.DrawIconEx(dc, 0, 0, self.icons[self.language], *self.size,
                                       0, None, win32con.DI_NORMAL)
            finally:
                win32gui.EndPaint(hwnd, paint)
            return 0
        if message == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if message == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wp, lp)

    def _window_loop(self):
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, ctypes.c_void_p]
        u.SetTimer.restype = ctypes.c_size_t
        u.KillTimer.argtypes = [w.HWND, ctypes.c_size_t]
        name = f"TypeBridgeCaretFlag{os.getpid()}_{threading.get_ident()}"
        try:
            u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            wc = win32gui.WNDCLASS()
            wc.lpszClassName, wc.lpfnWndProc = name, self._wndproc
            wc.hbrBackground = win32con.COLOR_WINDOW + 1
            win32gui.RegisterClass(wc)
            self.hwnd = win32gui.CreateWindowEx(OVERLAY_STYLE, name, "TypeBridgeCaretFlag",
                win32con.WS_POPUP, 0, 0, *self.size, 0, 0, 0, None)
            win32gui.SetLayeredWindowAttributes(self.hwnd, 0, 255, win32con.LWA_ALPHA)
            for language, path in _icon_files().items():
                if language in LANGS:
                    self.icons[language] = win32gui.LoadImage(0, str(path), win32con.IMAGE_ICON,
                                                            48, 48, win32con.LR_LOADFROMFILE)
            if not u.SetTimer(self.hwnd, 1, 80, None):
                raise OSError("caret-timer")
            self.ready.set()
            if self.closed.is_set():
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            win32gui.PumpMessages()
        except Exception as error:
            self.error = type(error).__name__
            self.ready.set()
        finally:
            self.closed.set()
            if self.hwnd and win32gui.IsWindow(self.hwnd):
                u.KillTimer(self.hwnd, 1)
                win32gui.DestroyWindow(self.hwnd)
            self.hwnd = None
            for icon in self.icons.values():
                win32gui.DestroyIcon(icon)
            self.icons.clear()
            try:
                win32gui.UnregisterClass(name, 0)
            except Exception:
                pass
