"""Debug-only independent, minimal WH_KEYBOARD_LL control receiver.

Counts callback notifications only. No key codes, hook structures, text,
foreground windows, titles or device identifiers are read or stored.
Does not swallow or modify input. Has its own message-pump thread.
"""

import ctypes
import threading
from ctypes import wintypes as w


WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x100
WM_SYSKEYDOWN = 0x104
WM_QUIT = 0x12
WM_CONTROL_PING = 0x8006
HookProc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM)


class ControlKeyboardProbe:
    def __init__(self):
        self.status = 'not-started'
        self.error = 0
        self.callbacks = 0
        self.keydowns = 0
        self.pongs = 0
        self.thread_id = 0
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run,
                                        name='TypeBridgeControlKeyboard', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._post(WM_QUIT)

    def _post(self, message):
        if not self.thread_id:
            return False
        u = ctypes.WinDLL('user32', use_last_error=True)
        u.PostThreadMessageW.argtypes = (w.DWORD, w.UINT, w.WPARAM, w.LPARAM)
        u.PostThreadMessageW.restype = w.BOOL
        return bool(u.PostThreadMessageW(self.thread_id, message, 0, 0))

    def ping(self):
        return self._post(WM_CONTROL_PING) if self.status == 'registered' else False

    def snapshot(self):
        return dict(status=self.status, error=self.error,
                    callbacks=self.callbacks, keydowns=self.keydowns,
                    pongs=self.pongs,
                    alive=bool(self._thread and self._thread.is_alive()))

    def _run(self):
        self.status = 'starting'
        hook = None
        self.thread_id = 0
        try:
            u = ctypes.WinDLL('user32', use_last_error=True)
            k = ctypes.WinDLL('kernel32', use_last_error=True)
            u.SetWindowsHookExW.argtypes = (ctypes.c_int, HookProc, w.HINSTANCE, w.DWORD)
            u.SetWindowsHookExW.restype = w.HANDLE
            u.CallNextHookEx.argtypes = (w.HANDLE, ctypes.c_int, w.WPARAM, w.LPARAM)
            u.CallNextHookEx.restype = ctypes.c_ssize_t
            u.UnhookWindowsHookEx.argtypes = (w.HANDLE,)
            u.UnhookWindowsHookEx.restype = w.BOOL
            u.PeekMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT)
            u.GetMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
            u.GetMessageW.restype = ctypes.c_int
            k.GetCurrentThreadId.restype = w.DWORD
            k.GetModuleHandleW.argtypes = (w.LPCWSTR,)
            k.GetModuleHandleW.restype = w.HMODULE
            self.thread_id = int(k.GetCurrentThreadId())
            message = w.MSG()
            u.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            if self._stop.is_set():
                return

            def counter(code, event, param):
                self.callbacks += 1
                if code >= 0 and event in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    self.keydowns += 1
                return u.CallNextHookEx(None, code, event, param)

            self._callback = HookProc(counter)
            hook = u.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback,
                                       k.GetModuleHandleW(None), 0)
            if not hook:
                self.status = 'hook-error'
                self.error = ctypes.get_last_error()
                return
            self.status = 'registered'
            while not self._stop.is_set():
                rc = u.GetMessageW(ctypes.byref(message), None, 0, 0)
                if rc == -1:
                    self.status = 'message-error'
                    self.error = ctypes.get_last_error()
                    break
                if rc == 0:
                    break
                if message.message == WM_CONTROL_PING:
                    self.pongs += 1
                else:
                    u.TranslateMessage(ctypes.byref(message))
                    u.DispatchMessageW(ctypes.byref(message))
        except Exception:
            self.status = 'internal-error'
        finally:
            if hook:
                u.UnhookWindowsHookEx(hook)
            self.thread_id = 0
            if self.status == 'registered':
                self.status = 'stopped'
