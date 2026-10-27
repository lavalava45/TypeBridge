"""Opt-in debug-only, read-only keyboard presence counter using Win32 Raw Input.

Owns a hidden top-level window and message pump on a separate daemon thread.
It registers only the keyboard usage with RIDEV_INPUTSINK (not RIDEV_NOLEGACY),
and reads ONLY the RAWINPUTHEADER.dwType, never keystrokes, codes, or text.
No keyboard hook, SendInput, focus/HKL changes, or device enumeration.
"""

import ctypes
from ctypes import wintypes as w
import threading


WM_INPUT = 0x00FF
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
RID_HEADER = 0x10000005
RIM_TYPEKEYBOARD = 1
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001
WS_POPUP = 0x80000000


class RawInputDevice(ctypes.Structure):
    _fields_ = [('usage_page', w.USHORT), ('usage', w.USHORT),
                ('flags', w.DWORD), ('target', w.HWND)]


class RawInputHeader(ctypes.Structure):
    _fields_ = [('type', w.DWORD), ('size', w.DWORD),
                ('device', w.HANDLE), ('wparam', w.WPARAM)]


WndProcType = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WindowClass(ctypes.Structure):
    _fields_ = [('style', w.UINT), ('proc', WndProcType),
                ('class_extra', ctypes.c_int), ('window_extra', ctypes.c_int),
                ('instance', w.HINSTANCE), ('icon', w.HICON),
                ('cursor', w.HANDLE), ('background', w.HBRUSH),
                ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]


class RawKeyboardProbe:
    def __init__(self):
        self.status = 'not-started'
        self.error = 0
        self.keyboard_events = 0
        self.wm_input_messages = 0
        self.pump_input_messages = 0
        self.pump_messages = 0
        self.message_errors = 0
        self.reg_state = 'not-checked'
        self.reg_error = 0
        self.reg_flags = 0
        self.thread_id = 0
        self._thread = None
        self._window = 0
        self._stop = threading.Event()

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name='TypeBridgeRawInputProbe', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self.thread_id:
            try:
                u = ctypes.WinDLL('user32', use_last_error=True)
                u.PostThreadMessageW.argtypes = (w.DWORD, w.UINT, w.WPARAM, w.LPARAM)
                u.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            except (OSError, AttributeError):
                pass

    def snapshot(self):
        return dict(status=self.status, events=self.keyboard_events,
                    errors=self.message_errors, error=self.error,
                    alive=bool(self._thread and self._thread.is_alive()),
                    wm_input=self.wm_input_messages,
                    pump_input=self.pump_input_messages,
                    pump=self.pump_messages,
                    reg=self.reg_state, reg_error=self.reg_error,
                    reg_flags=self.reg_flags)

    def verify_registration(self):
        """Check the actual process registration; never expose the HWND."""
        if self.status != 'registered' or not self._window:
            self.reg_state = 'not-ready'
            return
        try:
            u = ctypes.WinDLL('user32', use_last_error=True)
            u.GetRegisteredRawInputDevices.argtypes = (
                ctypes.POINTER(RawInputDevice), ctypes.POINTER(w.UINT), w.UINT)
            u.GetRegisteredRawInputDevices.restype = w.UINT
            count = w.UINT(0)
            ctypes.set_last_error(0)
            result = u.GetRegisteredRawInputDevices(None, ctypes.byref(count),
                                                    ctypes.sizeof(RawInputDevice))
            if result == 0xFFFFFFFF and ctypes.get_last_error() != 122:
                self.reg_state = 'query-error'
                self.reg_error = ctypes.get_last_error()
                return
            if not count.value:
                self.reg_state = 'missing'
                self.reg_error = 0
                return
            devices = (RawInputDevice * count.value)()
            returned = u.GetRegisteredRawInputDevices(
                devices, ctypes.byref(count), ctypes.sizeof(RawInputDevice))
            if returned == 0xFFFFFFFF:
                self.reg_state = 'query-error'
                self.reg_error = ctypes.get_last_error()
                return
            entry = next((d for d in devices[:returned]
                          if d.usage_page == 1 and d.usage == 6), None)
            if entry is None:
                self.reg_state = 'missing'
                self.reg_flags = 0
            elif int(entry.target or 0) != self._window:
                self.reg_state = 'other-target'
                self.reg_flags = int(entry.flags)
            else:
                self.reg_state = 'ours'
                self.reg_flags = int(entry.flags)
            self.reg_error = 0
        except Exception:
            self.reg_state = 'query-error'
            self.reg_error = -1

    def _count_input(self, user32, raw_handle):
        header = RawInputHeader()
        size = w.UINT(ctypes.sizeof(header))
        result = user32.GetRawInputData(
            raw_handle, RID_HEADER, ctypes.byref(header), ctypes.byref(size),
            ctypes.sizeof(header))
        if result == 0xFFFFFFFF or result != ctypes.sizeof(header):
            self.message_errors += 1
            return
        if header.type == RIM_TYPEKEYBOARD:
            self.keyboard_events += 1

    def _run(self):
        self.status = 'starting'
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        user32.RegisterClassW.argtypes = (ctypes.POINTER(WindowClass),)
        user32.RegisterClassW.restype = w.ATOM
        user32.UnregisterClassW.argtypes = (w.LPCWSTR, w.HINSTANCE)
        user32.CreateWindowExW.argtypes = (
            w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p)
        user32.CreateWindowExW.restype = w.HWND
        user32.DestroyWindow.argtypes = (w.HWND,)
        user32.DefWindowProcW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RawInputDevice), w.UINT, w.UINT)
        user32.RegisterRawInputDevices.restype = w.BOOL
        user32.GetRawInputData.argtypes = (w.HANDLE, w.UINT, ctypes.c_void_p,
                                          ctypes.POINTER(w.UINT), w.UINT)
        user32.GetRawInputData.restype = w.UINT
        user32.GetMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = (ctypes.POINTER(w.MSG),)
        user32.DispatchMessageW.argtypes = (ctypes.POINTER(w.MSG),)
        kernel32.GetModuleHandleW.argtypes = (w.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = w.HMODULE
        kernel32.GetCurrentThreadId.restype = w.DWORD
        instance = kernel32.GetModuleHandleW(None)
        self.thread_id = int(kernel32.GetCurrentThreadId())
        name = 'TypeBridgeRawInputDiagnosticV1'
        registered_class = False
        registered_device = False
        try:
            # Keep the Python callback alive for the entire window lifetime.
            def on_message(hwnd, message, wp, lp):
                if message == WM_INPUT:
                    self.wm_input_messages += 1
                    try:
                        self._count_input(user32, lp)
                    except Exception:
                        self.message_errors += 1
                return user32.DefWindowProcW(hwnd, message, wp, lp)

            self._wnd_proc = WndProcType(on_message)
            klass = WindowClass(proc=self._wnd_proc, instance=instance, name=name)
            if not user32.RegisterClassW(ctypes.byref(klass)):
                self.status = 'class-error'
                self.error = ctypes.get_last_error()
                return
            registered_class = True
            hwnd = user32.CreateWindowExW(0, name, '', WS_POPUP,
                                          0, 0, 1, 1, None, None, instance, None)
            if not hwnd:
                self.status = 'window-error'
                self.error = ctypes.get_last_error()
                return
            self._window = int(hwnd)
            device = RawInputDevice(1, 6, RIDEV_INPUTSINK, hwnd)
            if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
                self.status = 'register-error'
                self.error = ctypes.get_last_error()
                return
            registered_device = True
            self.status = 'registered'
            self.verify_registration()
            if self._stop.is_set():
                return
            message = w.MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    if result == -1:
                        self.status = 'message-error'
                        self.error = ctypes.get_last_error()
                    break
                self.pump_messages += 1
                if message.message == WM_INPUT:
                    self.pump_input_messages += 1
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception:
            self.status = 'internal-error'
            self.message_errors += 1
        finally:
            if registered_device:
                # Never deregister a different window's later registration.
                if self.status == 'registered':
                    self.verify_registration()
                if self.reg_state == 'ours':
                    remove = RawInputDevice(1, 6, RIDEV_REMOVE, None)
                    user32.RegisterRawInputDevices(ctypes.byref(remove), 1,
                                                   ctypes.sizeof(remove))
            if self._window:
                user32.DestroyWindow(self._window)
                self._window = 0
            if registered_class:
                user32.UnregisterClassW(name, instance)
            if self.status == 'registered':
                self.status = 'stopped'
            self.thread_id = 0
