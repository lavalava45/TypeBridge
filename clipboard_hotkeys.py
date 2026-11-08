"""Context-aware clipboard/search hotkeys on a dedicated message-loop thread, without global reservation.

Install explicitly only when enabled. Excluded/unknown applications receive
their original keyboard events; no replay of swallowed keys is used.
"""
import ctypes
from ctypes import wintypes as w
import queue
import threading
from dataclasses import dataclass
from time import monotonic

WAKE = 0x8000 + 91
CONTEXT_TTL = .2

from app_exclusions import target_allowed
from clipboard_tools import clipboard_bindings
from direct_hotkeys import Chord


class KeyboardEvent(ctypes.Structure):
    _fields_ = [('vkCode', w.DWORD), ('scanCode', w.DWORD), ('flags', w.DWORD),
                ('time', w.DWORD), ('extra', ctypes.c_size_t)]


@dataclass(frozen=True)
class PreparedContext:
    target: object
    settings: object
    generation: int
    started: float


class ClipboardHotkeys:
    def __init__(self, layouts, notify):
        self.layouts, self.notify = layouts, notify
        self.settings = None
        self.bindings = {}
        self.suppressed = set()
        self.hook = None
        self.callback = None
        self.worker = None
        self.thread_id = None
        self.commands = queue.Queue()
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.accepting = threading.Event()
        self.lifecycle_lock = threading.Lock()
        self.worker_error = None
        self.context_worker = None
        self.context_stop = threading.Event()
        self.context_cache = None
        self.context_generation = 0
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
        self.kernel.GetCurrentThreadId.restype = w.DWORD
        self.api.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
        self.api.PostThreadMessageW.restype = w.BOOL
        self.api.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
        self.api.PeekMessageW.restype = w.BOOL
        self.api.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        self.api.GetMessageW.restype = w.BOOL
        self.api.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        self.api.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        self.api.DispatchMessageW.restype = ctypes.c_ssize_t

    def configure(self, settings):
        bindings = clipboard_bindings(settings)
        with self.lifecycle_lock:
            self.accepting.clear()
            self.context_generation += 1
            self.context_cache = None
            if self.worker is None or not self.worker.is_alive():
                if self.hook:
                    raise OSError('Предыдущий hook буфера не снят')
                if not bindings:
                    self.settings, self.bindings = settings, bindings
                    return
                self.ready.clear()
                self.stopping.clear()
                self.worker_error = None
                try:
                    self.worker = threading.Thread(target=self._message_loop,
                                                   name='TypeBridgeClipboardHook', daemon=True)
                    self.worker.start()
                except RuntimeError as error:
                    self.stopping.set()
                    if self.worker is not None and not self.worker.is_alive():
                        self.worker = None
                    raise OSError('Не удалось запустить поток горячих клавиш буфера') from error
            if not self.ready.wait(1) or self.stopping.is_set():
                self.stopping.set()
                self._wake()
                raise OSError('Поток горячих клавиш буфера недоступен')
            done, cancelled, errors = threading.Event(), threading.Event(), []
            self.commands.put((settings, bindings, done, cancelled, errors))
            if not self._wake() or not done.wait(1):
                cancelled.set()
                self.accepting.clear()
                self.stopping.set()
                self._wake()
                raise OSError('Поток горячих клавиш буфера не отвечает')
            if errors:
                raise OSError('Не удалось настроить горячие клавиши буфера') from errors[0]

    def _wake(self):
        return bool(self.thread_id and self.api.PostThreadMessageW(self.thread_id, WAKE, 0, 0))

    def _sync_hook(self):
        # All native hook lifecycle calls and suppressed-key state belong here.
        if self.bindings and not self.hook:
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM)
            self.callback = callback_type(self.handle)
            self.hook = self.api.SetWindowsHookExW(13, self.callback, self.kernel.GetModuleHandleW(None), 0)
            if not self.hook:
                raise OSError('Не удалось включить горячие клавиши буфера')
        if not self.bindings and not self.suppressed:
            self._unhook()

    def _unhook(self):
        if self.hook:
            if not self.api.UnhookWindowsHookEx(self.hook):
                raise OSError('Не удалось отключить горячие клавиши буфера')
            self.hook = None

    def _refresh_context(self, stop):
        # Keep the last verified target during a refresh. The callback checks
        # its original timestamp and live foreground identity, so a stalled
        # reader cannot extend authority beyond CONTEXT_TTL.
        settings, generation, started = self.settings, self.context_generation, monotonic()
        if settings is None or not self.accepting.is_set() or stop.is_set():
            self.context_cache = None
            return
        try:
            target = self.layouts.capture()
            if (target is not None and target_allowed(target, settings)
                    and self.layouts.matches_foreground(target)
                    and not stop.is_set() and not self.stopping.is_set()
                    and self.accepting.is_set() and settings is self.settings
                    and generation == self.context_generation
                    and monotonic() - started <= CONTEXT_TTL):
                self.context_cache = PreparedContext(target, settings, generation, started)
                return
        except Exception:
            pass
        # A denied, failed or late read must revoke the old permission. Do not
        # erase a newer generation published during a settings change.
        if settings is self.settings and generation == self.context_generation:
            self.context_cache = None

    def _context_loop(self, stop):
        while not stop.is_set():
            self._refresh_context(stop)
            stop.wait(.05)

    def _start_context_reader(self):
        if self.context_worker is not None and self.context_worker.is_alive():
            raise OSError('Предыдущая проверка контекста ещё завершается')
        self.context_stop = threading.Event()
        self.context_worker = threading.Thread(target=self._context_loop,
                                              args=(self.context_stop,),
                                              name='TypeBridgeHotkeyContext', daemon=True)
        try:
            self.context_worker.start()
        except RuntimeError:
            if not self.context_worker.is_alive():
                self.context_worker = None
            raise

    def _message_loop(self):
        try:
            msg = w.MSG()
            self.thread_id = self.kernel.GetCurrentThreadId()
            self.api.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            self._start_context_reader()
            self.ready.set()  # PostThreadMessage is legal only after queue creation.
            while not self.stopping.is_set():
                while not self.commands.empty():
                    settings, bindings, done, cancelled, errors = self.commands.get_nowait()
                    try:
                        if not cancelled.is_set() and not self.stopping.is_set():
                            # Outside HOOKPROC, async state has caught up. A lost
                            # KEYUP must not suppress the next physical press.
                            for vk in tuple(self.suppressed):
                                if not self.api.GetAsyncKeyState(vk) & 0x8000:
                                    self.suppressed.discard(vk)
                            self.settings, self.bindings = settings, bindings
                            self._sync_hook()
                            if bindings and not cancelled.is_set() and not self.stopping.is_set():
                                self.accepting.set()
                    except Exception as error:
                        self.accepting.clear()
                        self.bindings = {}
                        errors.append(error)
                    finally:
                        done.set()
                self._sync_hook()
                if self.stopping.is_set():
                    break
                result = self.api.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == -1:
                    raise OSError('Ошибка очереди горячих клавиш буфера')
                if result == 0:
                    break
                if msg.hWnd:
                    self.api.TranslateMessage(ctypes.byref(msg))
                    self.api.DispatchMessageW(ctypes.byref(msg))
        except Exception as error:
            self.worker_error = error
            self.accepting.clear()
            try:
                self.notify(('tray-error', 'Поток горячих клавиш буфера остановлен'))
            except Exception:
                pass
        finally:
            self.accepting.clear()
            self.context_stop.set()
            self.context_cache = None
            self.stopping.set()
            self.ready.set()
            try:
                self._unhook()
            except OSError as error:
                self.worker_error = error
            if not self.hook:
                self.suppressed.clear()
            while not self.commands.empty():
                _, _, done, _, errors = self.commands.get_nowait()
                errors.append(OSError('Поток горячих клавиш буфера остановлен'))
                done.set()
            if self.context_worker is not None:
                self.context_worker.join(.1)

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
                    if not self.bindings:
                        self._wake()
                    return 1
                return forward()
            if message not in (0x100, 0x104):
                return forward()
            if vk in self.suppressed:
                return 1
            if not self.accepting.is_set() or not any(chord.vk == vk for chord in self.bindings):
                return forward()
            if any(self.api.GetAsyncKeyState(key) & 0x8000 for key in (0x5B, 0x5C)):
                return forward()
            chord = Chord(vk, frozenset(key for key in (0x10, 0x11, 0x12)
                                       if self.api.GetAsyncKeyState(key) & 0x8000))
            action = self.bindings.get(chord)
            if not action:
                return forward()
            context = self.context_cache
            if (context is None or context.settings is not self.settings
                    or context.generation != self.context_generation
                    or not 0 <= monotonic() - context.started <= CONTEXT_TTL
                    or not self.layouts.matches_foreground(context.target)
                    or not 0 <= monotonic() - context.started <= CONTEXT_TTL
                    or context.generation != self.context_generation
                    or not self.accepting.is_set()):
                return forward()
            self.notify((action, context.target))
            self.suppressed.add(vk)
            return 1
        except Exception:
            return forward()

    def close(self):
        # Immediately fail open, even if the owner is stuck in a native API.
        self.accepting.clear()
        self.context_stop.set()
        self.context_cache = None
        with self.lifecycle_lock:
            self.accepting.clear()
            self.context_stop.set()
            self.context_cache = None
            self.stopping.set()
            self._wake()
            if self.worker is not None:
                self.worker.join(1)
                if self.worker.is_alive():
                    raise OSError('Поток горячих клавиш буфера ещё завершается')
            if self.hook:
                raise OSError('Не удалось отключить горячие клавиши буфера')
            self.bindings = {}
