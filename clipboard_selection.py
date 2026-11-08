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

# Independent of optional user exclusions: Ctrl+C can interrupt a running command.
CONSOLE_PROCESSES = frozenset((
    'windowsterminal.exe', 'openconsole.exe', 'conhost.exe', 'cmd.exe',
    'powershell.exe', 'pwsh.exe', 'mintty.exe', 'putty.exe', 'kitty.exe',
    'alacritty.exe', 'wezterm-gui.exe', 'hyper.exe', 'tabby.exe',
))
CONSOLE_CLASSES = frozenset((
    'consolewindowclass', 'cascadia_hosting_window_class', 'virtualconsoleclass',
    'mintty', 'putty', 'kitty', 'alacritty', 'org.wezfurlong.wezterm',
))


def ensure_copy_target_safe(target, *, window_class=None, process_name=None):
    """Metadata only; unknown identity forbids fallback, never UIA selection."""
    if window_class is None:
        import win32gui
        window_class = win32gui.GetClassName
    if process_name is None:
        import psutil
        process_name = lambda pid: psutil.Process(pid).name()
    try:
        classes = {window_class(hwnd).casefold() for hwnd in {target.top, target.focus}}
        names = {process_name(pid).casefold() for pid in {target.pid, target.host_pid or target.pid}}
        safe = (all(classes) and all(names) and not classes & CONSOLE_CLASSES
                and not names & CONSOLE_PROCESSES)
    except Exception:
        safe = False
    if not safe:
        raise SelectionUnavailable('Резервное Ctrl+C недоступно для этого окна; используй выделение через UIA')


@dataclass(frozen=True)
class SearchPreview:
    text: str
    url: str = ''


@dataclass
class BitmapBackup:
    handle: int
    size: int


def copy_transaction(native, target, still_valid, guard=nullcontext, clock=time.monotonic,
                     sleep=time.sleep, *, deadline=None):
    """One Copy, no retries, bounded by one end-to-end request deadline.

    A slow snapshot or lock acquisition must never authorize a late Copy.
    Restoration after an already-issued Copy is still attempted on failure.
    """
    with guard() as allowed:
        captured_valid = still_valid
        still_valid = lambda: captured_valid() and (not callable(allowed) or allowed())
        expires = min(clock() + 3, deadline) if deadline is not None else clock() + 3
        if clock() >= expires:
            raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
        keys_deadline = min(clock() + 2, expires)
        while not native.keys_released():
            if clock() >= keys_deadline or not still_valid():
                raise SelectionUnavailable('Поиск отменён: отпусти клавиши и повтори команду')
            sleep(.02)
        if clock() >= expires or not still_valid():
            raise SelectionUnavailable('Поиск отменён: активное поле изменилось')
        snapshot, original_sequence = native.snapshot()
        if clock() >= expires:
            raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
        if not still_valid() or native.sequence() != original_sequence:
            raise SelectionUnavailable('Поиск отменён: окно или буфер изменились')
        native.protect_snapshot(snapshot)
        copy_deadline = min(clock() + 1, expires)
        observed = None
        try:
            if clock() >= expires or not still_valid():
                raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
            native.copy(still_valid=still_valid, deadline=expires)
            while clock() < copy_deadline:
                sequence = native.sequence()
                if sequence != original_sequence:
                    # A foreign clipboard owner must not be overwritten.
                    if not native.owned_by(target):
                        raise SelectionUnavailable('Поиск отменён: буфер изменён другим приложением')
                    observed = sequence
                    text = native.text(sequence)
                    if clock() >= expires or not still_valid():
                        raise SelectionUnavailable('Поиск отменён: активное поле изменилось')
                    if not isinstance(text, str) or not text.strip() or len(text) > 2000 or '\0' in text:
                        raise SelectionUnavailable('Выдели текст длиной от 1 до 2000 символов')
                    return SearchPreview(text.strip())
                if clock() >= expires or not still_valid():
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
    def __init__(self, target, *, verify_copy_target=True):
        # A plain-paste rollback only snapshots/restores the clipboard and
        # does not issue Ctrl+C. Its target policy is checked by the caller.
        if verify_copy_target:
            ensure_copy_target_safe(target)
        self.target = target
        import win32clipboard as cb
        import win32gui
        self.cb, self.gui = cb, win32gui
        from clipboard_tools import NativeClipboard
        self._privacy_check = NativeClipboard()._require_public_content
        self.bitmaps = []
        self._checkpoint = None
        self._mutation_possible = False
        self.hwnd = win32gui.CreateWindowEx(0, 'STATIC', 'TypeBridgeClipboardSearch', 0,
                                           0, 0, 0, 0, -3, 0, 0, None)

    def close(self):
        try:
            if not getattr(self, '_mutation_possible', False):
                self.discard_checkpoint()
        finally:
            try:
                for bitmap in self.bitmaps:
                    if bitmap.handle:
                        self.gui.DeleteObject(bitmap.handle)
                        bitmap.handle = 0
            finally:
                self.gui.DestroyWindow(self.hwnd)

    def protect_snapshot(self, snapshot):
        """Durable, verified copy must precede Copy, paste or restoration."""
        from clipboard_recovery import Checkpoint
        try:
            checkpoint = getattr(self, '_checkpoint', None)
            if checkpoint is None:
                self._checkpoint = Checkpoint.create(snapshot, self.cb.GetClipboardFormatName)
            elif not checkpoint.matches(snapshot, self.cb.GetClipboardFormatName):
                raise ValueError('clipboard-backup-snapshot-changed')
        except Exception as error:
            raise SelectionUnavailable(
                'Операция отменена: не удалось создать и проверить зашифрованную копию буфера') from error

    def discard_checkpoint(self):
        checkpoint = getattr(self, '_checkpoint', None)
        if checkpoint is not None:
            checkpoint.discard()
            self._checkpoint = None

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

    def snapshot(self, *, strict=False, preflight=None):
        cb = self.cb
        cb.OpenClipboard(self.hwnd)
        try:
            preflight = preflight or getattr(self, '_privacy_check', None)
            if preflight is not None:
                # Enforce privacy flags while the same clipboard lock is held,
                # *before* enumerating or reading any clipboard payload.
                preflight()
            rows, size, fmt, skipped_ole = [], 0, 0, False
            while True:
                fmt = cb.EnumClipboardFormats(fmt)
                if not fmt:
                    if skipped_ole and (strict or not rows):
                        # A temporary plain-paste conversion promises to
                        # restore the *entire* original clipboard on refusal.
                        # It cannot silently discard OLE bookkeeping formats.
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

    def copy(self, *, still_valid=lambda: False, deadline=None):
        # Recheck immediately before injection, not just before clipboard backup.
        ensure_copy_target_safe(self.target)
        import ctypes
        from typebridge_app import Input, KeyInput, OWN_INPUT_MARKER
        events = (Input * 4)()
        for item, (key, flags) in zip(events, ((0x11, 0), (0x43, 0), (0x43, 2), (0x11, 2))):
            item.type = 1
            item.u.ki = KeyInput(key, 0, flags, 0, OWN_INPUT_MARKER)
        api = ctypes.WinDLL('user32', use_last_error=True)
        api.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(Input), ctypes.c_int]
        api.SendInput.restype = ctypes.c_uint
        # Metadata APIs above can themselves be slow. Never turn a late safety
        # answer into permission for a stale Ctrl+C.
        if not still_valid() or deadline is None or time.monotonic() >= deadline:
            raise SelectionUnavailable('Поиск отменён: окно изменилось или истёк срок запроса')
        if getattr(self, '_checkpoint', None) is None:
            raise SelectionUnavailable('Копирование отменено: резервная копия буфера не подготовлена')
        self._mutation_possible = True
        sent = int(api.SendInput(4, events, ctypes.sizeof(Input)))
        if sent != 4:
            # Release *only* unmatched accepted keydowns. A fixed C-up /
            # Ctrl-up packet was incorrect for 0, 1 or 3 accepted events.
            # Never resend Ctrl+C: the recipient may have copied already.
            if 0 <= sent <= 4:
                from input_transport import unmatched_keydowns
                dangling = unmatched_keydowns(events, sent)
                release = (Input * len(dangling))()
                for dest, (vk, scan, flags) in zip(release, reversed(dangling)):
                    dest.type = 1
                    dest.u.ki = KeyInput(vk, scan, flags | 2, 0, OWN_INPUT_MARKER)
                if len(release) and api.SendInput(
                        len(release), release, ctypes.sizeof(Input)) != len(release):
                    raise SelectionUnavailable(
                        'Копирование отменено: отпускание удержанных клавиш не подтверждено')
            raise SelectionUnavailable('Копирование не подтверждено; автоматический повтор отменён')

    def restore(self, snapshot, expected, target, *, owner_hwnd=None, explicit_recovery=False,
                still_valid=lambda: True):
        """Allocate every HGLOBAL before emptying clipboard; transfer ownership once."""
        import ctypes
        from ctypes import wintypes as w
        self.protect_snapshot(snapshot)
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
                # Rollback after OUR temporary plain-paste write: require the
                # exact owner HWND, not merely any window in the target PID.
                # Never overwrite a clipboard modified by another process.
                owner_ok = (self.cb.GetClipboardOwner() == owner_hwnd
                            if owner_hwnd is not None or explicit_recovery else self.owned_by(target))
                if self.sequence() != expected or not owner_ok or not still_valid():
                    return False
                self._mutation_possible = True
                self.cb.EmptyClipboard()
                for row in prepared:
                    if not api.SetClipboardData(row[0], row[1]):
                        raise OSError('clipboard-restore')
                    if row[2] is not None:
                        row[2].handle = 0
                    row[1] = None  # Owned by Windows from now on.
            finally:
                self.cb.CloseClipboard()
            self.discard_checkpoint()
            self._mutation_possible = False
            return True
        except Exception as error:
            if getattr(self, '_mutation_possible', False):
                raise SelectionUnavailable(
                    'Восстановление буфера не завершено. Зашифрованная копия сохранена; '
                    'выбери в трее «Восстановить резервную копию буфера…»') from error
            raise
        finally:
            for _, handle, bitmap in prepared:
                if handle and bitmap is None:
                    kernel.GlobalFree(handle)


def copy_selection(target, guard=nullcontext, *, deadline=None):
    from tray_layouts import WindowsLayouts
    layouts = WindowsLayouts()
    native = NativeCopy(target)
    try:
        return copy_transaction(native, target, lambda: layouts.valid(target) and layouts.capture() == target,
                                guard, deadline=deadline)
    finally:
        native.close()
