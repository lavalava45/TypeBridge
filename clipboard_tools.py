"""Opt-in, memory-only text history and explicit plain-text paste commands.

No hooks, files, clipboard writes or focus changes at import/initialization.
Native access is confined to the background reader or explicit user commands.
"""
import os
import queue
import threading
import time
from contextlib import contextmanager

from direct_hotkeys import parse_chord
from app_exclusions import effective_exclusions, target_allowed

MAX_TEXT = 100_000
MAX_TOTAL = 1_000_000


class TextHistory:
    def __init__(self, limit=30):
        self.limit = limit
        self.items = []

    def add(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or '\0' in text:
            return False
        self.items[:] = [item for item in self.items if item != text]
        self.items.insert(0, text)
        del self.items[self.limit:]
        while sum(map(len, self.items)) > MAX_TOTAL:
            self.items.pop()
        return True

    def clear(self):
        self.items.clear()


def clipboard_bindings(settings):
    result = {}
    for enabled, name, action in (
        (settings.clipboard_history, settings.clipboard_history_key, 'history'),
        (settings.plain_paste, settings.plain_paste_key, 'plain-paste'),
        (getattr(settings, 'quick_search', False), getattr(settings, 'quick_search_key', 'Ctrl+Alt+G'), 'quick-search'),
    ):
        chord = parse_chord(name)  # Validate even disabled settings.
        if chord and (not chord.modifiers or chord.vk == 0x7B):
            raise ValueError('Для поиска и команд буфера выбери сочетание с Ctrl/Alt/Shift, без F12')
        if enabled and chord:
            if chord in result:
                raise ValueError('Одно сочетание назначено нескольким командам поиска или буфера')
            result[chord] = action
    return result


class NativeClipboard:
    def __init__(self):
        import win32clipboard as cb
        self.cb = cb
        self.private_formats = [cb.RegisterClipboardFormat(name) for name in (
            'ExcludeClipboardContentFromMonitorProcessing', 'Clipboard Viewer Ignore')]
        self.history_format = cb.RegisterClipboardFormat('CanIncludeInClipboardHistory')

    def sequence(self):
        return self.cb.GetClipboardSequenceNumber()

    def read(self, allowed, *, history=False):
        """Return (sequence, text); busy clipboard raises, caller retries later."""
        import win32process
        cb = self.cb
        cb.OpenClipboard()
        try:
            owner = cb.GetClipboardOwner()
            pid = win32process.GetWindowThreadProcessId(owner)[1] if owner else None
            # Automatic retention needs an identifiable source. Explicit plain
            # paste may still use ownerless text at the user's request.
            if (history and not pid) or not allowed(pid):
                return self.sequence(), None
            if history:
                if any(cb.IsClipboardFormatAvailable(fmt) for fmt in self.private_formats):
                    return self.sequence(), None
                if cb.IsClipboardFormatAvailable(self.history_format):
                    value = cb.GetClipboardData(self.history_format)
                    if value in (0, b'\0\0\0\0', b'\0'):
                        return self.sequence(), None
            if not cb.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT only.
                return self.sequence(), None
            text = cb.GetClipboardData(13)
            if not isinstance(text, str) or len(text) > MAX_TEXT or '\0' in text:
                text = None
            return self.sequence(), text
        finally:
            cb.CloseClipboard()

    def write_plain(self, text, hwnd, expected_sequence=None):
        cb = self.cb
        cb.OpenClipboard(hwnd)
        try:
            if expected_sequence is not None and self.sequence() != expected_sequence:
                raise OSError('Буфер изменился; повтори команду')
            cb.EmptyClipboard()
            cb.SetClipboardData(13, text)
            return self.sequence()
        finally:
            cb.CloseClipboard()


class ClipboardTools:
    def __init__(self, root, notify, native=None):
        self.root, self.notify = root, notify
        self.native = native or NativeClipboard()
        self.history = TextHistory()
        self.settings = None
        self.generation = 0
        self.closed = False
        self.pending = False
        self.requests = queue.Queue()
        self.worker = None
        self.history_paused = False

    def _start_reader(self):
        if self.worker is None:
            self.worker = threading.Thread(target=self._reader, name='TypeBridgeClipboard', daemon=True)
            self.worker.start()

    def configure(self, settings):
        self.settings = settings
        self.generation += 1
        # Start with future copies; never import an old clipboard on enable.
        self.sequence = self.native.sequence()
        if not settings.clipboard_history:
            self.history.clear()
        else:
            self._start_reader()

    def clear_history(self):
        self.generation += 1
        self.sequence = self.native.sequence()
        self.history.clear()

    def delete_history(self, text):
        self.generation += 1
        self.sequence = self.native.sequence()
        if text in self.history.items:
            self.history.items.remove(text)

    def allowed_pid(self, pid):
        if not pid:
            return True
        try:
            import psutil
            return psutil.Process(pid).name().casefold() not in effective_exclusions(self.settings)
        except Exception:
            return False

    def _reader(self):
        while not self.closed:
            try:
                task = self.requests.get(timeout=.25)
            except queue.Empty:
                task = None
            if self.closed:
                return
            settings, generation = self.settings, self.generation
            try:
                if task:
                    requested_generation, target, started = task
                    if requested_generation != generation or not settings.plain_paste:
                        self.notify(("cancelled", requested_generation))
                        continue
                    seq, text = self.native.read(self.allowed_pid)
                    self.notify(('prepared', generation, target, started, seq, text))
                elif settings and settings.clipboard_history and not self.history_paused:
                    seq = self.native.sequence()
                    if seq != self.sequence:
                        actual, text = self.native.read(self.allowed_pid, history=True)
                        self.notify(('captured', generation, actual, text))
            except Exception:
                if task:
                    self.notify(('paste-error', 'Буфер недоступен; повтори команду'))
                # History retries busy clipboard on the next polling interval.

    def receive(self, message):
        if self.closed:
            return
        kind = message[0]
        if kind == 'cancelled':
            self.pending = False
            return
        if kind == 'paste-error':
            self.pending = False
            raise OSError(message[1])
        if message[1] != self.generation or self.closed:
            self.pending = False if kind == 'prepared' else self.pending
            return
        if kind == 'captured':
            self.sequence = message[2]
            if self.settings.clipboard_history:
                self.history.add(message[3])
        elif kind == 'prepared':
            _, _, target, started, seq, text = message
            if not text:
                self.pending = False
                raise OSError('В буфере нет доступного текста')
            self._paste_when_released(target, text, started, seq, self.generation)

    def request_plain(self, target):
        if self.history_paused:
            raise OSError('Буфер временно занят поиском')
        if self.closed or not self.settings.plain_paste or self.pending:
            return
        if not target:
            raise OSError('Сначала выбери поле для вставки')
        self.pending = True
        self.requests.put((self.generation, target, time.monotonic()))
        self._start_reader()

    def paste_item(self, target, text, window):
        """Only called by an explicit Paste button/double click in our window."""
        import win32gui
        if self.history_paused:
            raise OSError('Буфер временно занят поиском')
        from tray_layouts import WindowsLayouts
        if self.pending:
            return
        layouts = WindowsLayouts()
        if not layouts.valid(target) or target.pid == os.getpid():
            raise OSError('Исходное окно закрыто; скопируй запись кнопкой «Копировать»')
        foreground = win32gui.GetForegroundWindow()
        if win32gui.GetAncestor(foreground, 2) != win32gui.GetAncestor(window.winfo_id(), 2):
            raise OSError('Активное окно изменилось; повтори выбор')
        window.withdraw()
        win32gui.SetForegroundWindow(target.top)
        self.pending = True
        self._paste_when_released(target, text, time.monotonic(), None, self.generation)

    def _paste_when_released(self, target, text, started, sequence, generation):
        import ctypes
        import win32api
        import win32gui
        from tray_layouts import WindowsLayouts
        from typebridge_app import Input, KeyInput, OWN_INPUT_MARKER
        try:
            if self.closed or generation != self.generation or time.monotonic() - started > 2:
                raise OSError('Вставка отменена: клавиши не отпущены вовремя')
            layouts = WindowsLayouts()
            if not layouts.valid(target) or layouts.capture() != target:
                raise OSError('Вставка отменена: поле ввода изменилось')
            if not target_allowed(target, self.settings) or win32gui.GetWindowLong(target.focus, -16) & 0x20:
                raise OSError('Вставка в это поле отключена')
            if any(win32api.GetAsyncKeyState(vk) & 0x8000 for vk in
                   (0x10, 0x11, 0x12, 0x5B, 0x5C)):
                self.root.after(30, lambda: self._paste_when_released(target, text, started, sequence, generation))
                return
            written = self.native.write_plain(text, self.root.winfo_id(), sequence)
            if layouts.capture() != target or self.native.sequence() != written:
                raise OSError("Вставка отменена: окно или буфер изменились")
            # Ordinary Ctrl+V preserves native multi-line/Undo behavior. Only
            # Unicode text is offered; rich formats are intentionally removed.
            events = (Input * 4)()
            for item, (vk, flags) in zip(events, ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2))):
                item.type = 1
                item.u.ki = KeyInput(vk, 0, flags, 0, OWN_INPUT_MARKER)
            api = ctypes.WinDLL('user32', use_last_error=True)
            api.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(Input), ctypes.c_int]
            api.SendInput.restype = ctypes.c_uint
            sent = api.SendInput(4, events, ctypes.sizeof(Input))
            if sent != 4:
                # Release only our Ctrl; never replay a possibly delivered paste.
                release = (Input * 1)(events[3])
                api.SendInput(1, release, ctypes.sizeof(Input))
                raise OSError('Вставка не подтверждена; повтор автоматически отменён')
            self.pending = False
        except Exception as error:
            self.pending = False
            self.notify(('paste-error', str(error)))

    def close(self):
        self.closed = True
        self.generation += 1
        self.history.clear()
        self.requests.put(None)

    @contextmanager
    def selection_copy(self):
        if self.pending or self.history_paused or self.closed:
            from clipboard_selection import SelectionUnavailable
            raise SelectionUnavailable('Поиск отменён: буфер занят другой командой')
        self.history_paused = True
        self.generation += 1  # Discard queued or in-flight history captures.
        try:
            yield
        finally:
            try:
                self.sequence = self.native.sequence()
            finally:
                self.generation += 1
                self.history_paused = False
