"""Context-aware clipboard/search hotkeys on the tray thread, without global reservation.

Install explicitly only when enabled. Excluded/unknown applications receive
their original keyboard events; no replay of swallowed keys is used.
"""
import ctypes
from ctypes import wintypes as w

from app_exclusions import target_allowed
from clipboard_tools import clipboard_bindings
from direct_hotkeys import Chord


class KeyboardEvent(ctypes.Structure):
    _fields_ = [('vkCode', w.DWORD), ('scanCode', w.DWORD), ('flags', w.DWORD),
                ('time', w.DWORD), ('extra', ctypes.c_size_t)]


class ClipboardHotkeys:
    def __init__(self, layouts, notify):
        self.layouts, self.notify = layouts, notify
        self.settings = None
        self.bindings = {}
        self.suppressed = set()
        self.hook = None
        self.callback = None
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = w.HMODULE
        self.api.CallNextHookEx.argtypes = [w.HANDLE, ctypes.c_int, w.WPARAM, w.LPARAM]
        self.api.CallNextHookEx.restype = ctypes.c_ssize_t
        self.api.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, w.HINSTANCE, w.DWORD]
        self.api.SetWindowsHookExW.restype = w.HANDLE
        self.api.UnhookWindowsHookEx.argtypes = [w.HANDLE]
        self.api.UnhookWindowsHookEx.restype = w.BOOL
        self.api.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.api.GetAsyncKeyState.restype = ctypes.c_short

    def configure(self, settings):
        self.settings = settings
        self.bindings = clipboard_bindings(settings)
        if self.bindings and not self.hook:
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM)
            self.callback = callback_type(self.handle)
            # Low-level hooks execute here, on the tray's message-loop thread.
            self.hook = self.api.SetWindowsHookExW(13, self.callback, self.kernel.GetModuleHandleW(None), 0)
            if not self.hook:
                raise OSError('Не удалось включить горячие клавиши буфера')
        # Keep an existing hook inert until release of any consumed key. This
        # avoids forwarding the repeat of a key consumed just before disabling.
        if not self.bindings and not self.suppressed:
            self.close()

    def handle(self, code, message, param):
        def forward():
            return self.api.CallNextHookEx(self.hook, code, message, param)
        if code < 0:
            return forward()
        try:
            event = ctypes.cast(param, ctypes.POINTER(KeyboardEvent)).contents
            if event.flags & 0x10:  # Pass all injected input, including our paste.
                return forward()
            vk = int(event.vkCode)
            if message in (0x101, 0x105):
                if vk in self.suppressed:
                    self.suppressed.discard(vk)
                    return 1
                return forward()
            if message not in (0x100, 0x104):
                return forward()
            if vk in self.suppressed:
                return 1
            if not any(chord.vk == vk for chord in self.bindings):
                return forward()
            if any(self.api.GetAsyncKeyState(key) & 0x8000 for key in (0x5B, 0x5C)):
                return forward()
            chord = Chord(vk, frozenset(key for key in (0x10, 0x11, 0x12)
                                       if self.api.GetAsyncKeyState(key) & 0x8000))
            action = self.bindings.get(chord)
            if not action:
                return forward()
            target = self.layouts.capture()
            if not target_allowed(target, self.settings):
                return forward()
            self.notify((action, target))
            self.suppressed.add(vk)
            return 1
        except Exception:
            return forward()

    def close(self):
        self.bindings = {}
        if self.hook:
            if not self.api.UnhookWindowsHookEx(self.hook):
                raise OSError('Не удалось отключить горячие клавиши буфера')
            self.hook = None
        self.suppressed.clear()
