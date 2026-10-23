"""Generic explicit Copy fallback, with bounded clipboard backup and a preview.

No input, clipboard reads or native initialization at import. Caller must have
validated the foreground field and wait for released physical modifiers.
"""
from dataclasses import dataclass
from contextlib import nullcontext
import time


class SelectionUnavailable(Exception):
    """Only fixed, safe-to-display messages, never document content."""


# OLE bookkeeping is bound to the original owner, not a user payload to replay.
# Preserve the actual offered formats; embedded/link/storage data is separate.
OLE_BOOKKEEPING = frozenset(('dataobject', 'ole private data'))
OWNER_DEPENDENT_DATA = frozenset(('embed source', 'embedded object', 'link source', 'filecontents',
                                'filegroupdescriptor', 'filegroupdescriptorw'))


@dataclass(frozen=True)
class SearchPreview:
    text: str
    url: str = ''


@dataclass
class BitmapBackup:
    handle: int
    size: int


def copy_transaction(native, target, still_valid, guard=nullcontext, clock=time.monotonic, sleep=time.sleep):
    """One Copy, no retries. Restore only our observed result, never a newer copy."""
    with guard() as allowed:
        captured_valid = still_valid
        still_valid = lambda: captured_valid() and (not callable(allowed) or allowed())
        deadline = clock() + 2
        while not native.keys_released():
            if clock() >= deadline or not still_valid():
                raise SelectionUnavailable('Поиск отменён: отпусти клавиши и повтори команду')
            sleep(.02)
        if not still_valid():
            raise SelectionUnavailable('Поиск отменён: активное поле изменилось')
        snapshot, original_sequence = native.snapshot()
        if not still_valid() or native.sequence() != original_sequence:
            raise SelectionUnavailable('Поиск отменён: окно или буфер изменились')
        deadline = clock() + 1
        observed = None
        try:
            native.copy()
            while clock() < deadline:
                sequence = native.sequence()
                if sequence != original_sequence:
                    # A foreign clipboard owner must not be overwritten.
                    if not native.owned_by(target):
                        raise SelectionUnavailable('Поиск отменён: буфер изменён другим приложением')
                    observed = sequence
                    text = native.text(sequence)
                    if not still_valid():
                        raise SelectionUnavailable('Поиск отменён: активное поле изменилось')
                    if not isinstance(text, str) or not text.strip() or len(text) > 2000 or '\0' in text:
                        raise SelectionUnavailable('Выдели текст длиной от 1 до 2000 символов')
                    return SearchPreview(text.strip())
                if not still_valid():
                    raise SelectionUnavailable('Поиск отменён: активное поле изменилось')
                sleep(.02)
            raise SelectionUnavailable('Приложение не предоставило текст по команде копирования')
        finally:
            if observed is None and native.sequence() != original_sequence and native.owned_by(target):
                observed = native.sequence()
            if observed is not None and not native.restore(snapshot, observed, target):
                # Do not send a query if restoration could not be confirmed.
                raise SelectionUnavailable('Поиск отменён: буфер изменился, восстановление не выполнено')


class NativeCopy:
    def __init__(self):
        import win32clipboard as cb
        import win32gui
        self.cb, self.gui = cb, win32gui
        self.bitmaps = []
        self.hwnd = win32gui.CreateWindowEx(0, 'STATIC', 'TypeBridgeClipboardSearch', 0,
                                           0, 0, 0, 0, -3, 0, 0, None)

    def close(self):
        for bitmap in self.bitmaps:
            if bitmap.handle:
                self.gui.DeleteObject(bitmap.handle)
                bitmap.handle = 0
        self.gui.DestroyWindow(self.hwnd)

    def sequence(self):
        return self.cb.GetClipboardSequenceNumber()

    def keys_released(self):
        import win32api
        return not any(win32api.GetAsyncKeyState(key) & 0x8000 for key in
                       (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def global_bytes(self, fmt, limit):
        import ctypes
        from ctypes import wintypes as w
        # These formats hold GDI/owner handles rather than transferable HGLOBAL bytes.
        if fmt in (2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E) or 0x200 <= fmt <= 0x2FF:
            raise SelectionUnavailable('Поиск отменён: этот формат буфера нельзя безопасно восстановить')
        api = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        api.GetClipboardData.argtypes, api.GetClipboardData.restype = [w.UINT], w.HANDLE
        kernel.GlobalSize.argtypes, kernel.GlobalSize.restype = [w.HGLOBAL], ctypes.c_size_t
        kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = [w.HGLOBAL], ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [w.HGLOBAL]
        handle = api.GetClipboardData(fmt)
        size = kernel.GlobalSize(handle) if handle else 0
        if not size or size > limit:
            raise SelectionUnavailable('Поиск отменён: буфер слишком большой для резервного копирования')
        address = kernel.GlobalLock(handle)
        if not address:
            raise SelectionUnavailable('Поиск отменён: этот формат буфера нельзя безопасно восстановить')
        try:
            return ctypes.string_at(address, size)
        finally:
            kernel.GlobalUnlock(handle)

    def snapshot(self):
        cb = self.cb
        cb.OpenClipboard(self.hwnd)
        try:
            rows, size, fmt, skipped_ole = [], 0, 0, False
            while True:
                fmt = cb.EnumClipboardFormats(fmt)
                if not fmt:
                    if skipped_ole and not rows:
                        raise SelectionUnavailable('Поиск отменён: этот формат буфера нельзя безопасно восстановить')
                    return rows, self.sequence()
                if fmt >= 0xC000:
                    name = cb.GetClipboardFormatName(fmt).casefold()
                    if name in OLE_BOOKKEEPING:
                        skipped_ole = True
                        continue
                    if name in OWNER_DEPENDENT_DATA:
                        raise SelectionUnavailable('Поиск отменён: этот формат буфера нельзя безопасно восстановить')
                # Raw HGLOBAL bytes preserve Unicode, ANSI, HTML, RTF, DIB and file lists.
                value = self.bitmap_backup(fmt, 8_000_000 - size) if fmt in (2, 0x82) else self.global_bytes(fmt, 8_000_000 - size)
                size += value.size if isinstance(value, BitmapBackup) else len(value)
                if size > 8_000_000 or len(rows) >= 64:
                    raise SelectionUnavailable('Поиск отменён: буфер слишком большой для резервного копирования')
                rows.append((fmt, value))
        finally:
            cb.CloseClipboard()

    def bitmap_backup(self, fmt, limit):
        import ctypes
        from ctypes import wintypes as w
        class Bitmap(ctypes.Structure):
            _fields_ = [('type', w.LONG), ('width', w.LONG), ('height', w.LONG),
                        ('width_bytes', w.LONG), ('planes', w.WORD), ('bits_pixel', w.WORD),
                        ('bits', ctypes.c_void_p)]
        api = ctypes.WinDLL('user32', use_last_error=True)
        gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        api.GetClipboardData.argtypes, api.GetClipboardData.restype = [w.UINT], w.HANDLE
        api.CopyImage.argtypes, api.CopyImage.restype = [w.HANDLE, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT], w.HANDLE
        gdi.GetObjectW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p]
        original = api.GetClipboardData(fmt)
        info = Bitmap()
        if not original or not gdi.GetObjectW(original, ctypes.sizeof(info), ctypes.byref(info)):
            raise SelectionUnavailable('Поиск отменён: этот формат буфера нельзя безопасно восстановить')
        size = abs(info.height) * info.width_bytes * max(1, info.planes)
        if not 0 < size <= limit:
            raise SelectionUnavailable('Поиск отменён: буфер слишком большой для резервного копирования')
        clone = api.CopyImage(original, 0, 0, 0, 0x2000)
        if not clone:
            raise SelectionUnavailable('Поиск отменён: этот формат буфера нельзя безопасно восстановить')
        saved = BitmapBackup(clone, size)
        self.bitmaps.append(saved)
        return saved

    def owned_by(self, target):
        import ctypes
        from ctypes import wintypes as w
        import win32process
        api = ctypes.WinDLL('user32', use_last_error=True)
        api.GetClipboardOwner.argtypes, api.GetClipboardOwner.restype = [], w.HWND
        owner = api.GetClipboardOwner()  # NULL is legal for a flushed OLE clipboard.
        return bool(owner and win32process.GetWindowThreadProcessId(owner)[1] in
                    {target.pid, target.host_pid or target.pid})

    def text(self, sequence):
        cb = self.cb
        cb.OpenClipboard(self.hwnd)
        try:
            if self.sequence() != sequence:
                return None
            if not cb.IsClipboardFormatAvailable(13):
                return None
            raw = self.global_bytes(13, 8002)
            end = next((i for i in range(0, len(raw) - 1, 2) if raw[i:i+2] == b'\0\0'), None)
            return raw[:end].decode('utf-16-le', 'strict') if end is not None else None
        finally:
            cb.CloseClipboard()

    def copy(self):
        import ctypes
        from typebridge_app import Input, KeyInput, OWN_INPUT_MARKER
        events = (Input * 4)()
        for item, (key, flags) in zip(events, ((0x11, 0), (0x43, 0), (0x43, 2), (0x11, 2))):
            item.type = 1
            item.u.ki = KeyInput(key, 0, flags, 0, OWN_INPUT_MARKER)
        api = ctypes.WinDLL('user32', use_last_error=True)
        api.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(Input), ctypes.c_int]
        api.SendInput.restype = ctypes.c_uint
        if api.SendInput(4, events, ctypes.sizeof(Input)) != 4:
            # Release our modifiers, never replay Copy after a partial batch.
            release = (Input * 2)(events[2], events[3])
            api.SendInput(2, release, ctypes.sizeof(Input))
            raise SelectionUnavailable('Копирование не подтверждено; автоматический повтор отменён')

    def restore(self, snapshot, expected, target):
        """Allocate every HGLOBAL before emptying clipboard; transfer ownership once."""
        import ctypes
        from ctypes import wintypes as w
        api = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GlobalAlloc.argtypes, kernel.GlobalAlloc.restype = [w.UINT, ctypes.c_size_t], w.HGLOBAL
        kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = [w.HGLOBAL], ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [w.HGLOBAL]
        kernel.GlobalFree.argtypes = [w.HGLOBAL]
        api.SetClipboardData.argtypes, api.SetClipboardData.restype = [w.UINT, w.HANDLE], w.HANDLE
        prepared = []
        try:
            for fmt, raw in snapshot:
                if isinstance(raw, BitmapBackup):
                    prepared.append([fmt, raw.handle, raw])
                    continue
                handle = kernel.GlobalAlloc(0x42, max(1, len(raw)))
                if not handle:
                    raise OSError('clipboard-allocation')
                prepared.append([fmt, handle, None])
                address = kernel.GlobalLock(handle)
                if not address:
                    raise OSError('clipboard-lock')
                try:
                    ctypes.memmove(address, raw, len(raw))
                finally:
                    kernel.GlobalUnlock(handle)
            self.cb.OpenClipboard(self.hwnd)
            try:
                if self.sequence() != expected or not self.owned_by(target):
                    return False
                self.cb.EmptyClipboard()
                for row in prepared:
                    if not api.SetClipboardData(row[0], row[1]):
                        raise OSError('clipboard-restore')
                    if row[2] is not None:
                        row[2].handle = 0
                    row[1] = None  # Owned by Windows from now on.
                return True
            finally:
                self.cb.CloseClipboard()
        finally:
            for _, handle, bitmap in prepared:
                if handle and bitmap is None:
                    kernel.GlobalFree(handle)


def copy_selection(target, guard=nullcontext):
    from tray_layouts import WindowsLayouts
    layouts = WindowsLayouts()
    native = NativeCopy()
    try:
        return copy_transaction(native, target, lambda: layouts.valid(target) and layouts.capture() == target, guard)
    finally:
        native.close()
