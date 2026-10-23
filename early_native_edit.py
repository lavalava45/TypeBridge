"""Research-only range replacement for RichEdit50W fields, not EXE adapters.

No global hooks, clipboard, SendInput or focus changes. Uses the existing early
transaction protocol. Undo has NOT passed admission; never wire to production
until native Undo and physical-input gates pass. Legacy EDIT was rejected.
"""
import ctypes
from ctypes import wintypes as w
from time import perf_counter

MAX_UNITS = 32768


def utf16_position(text, units):
    raw = text.encode('utf-16-le')
    if not 0 <= units <= len(raw) // 2:
        raise ValueError('caret-outside-text')
    return len(raw[:units * 2].decode('utf-16-le', 'strict').encode('utf-8'))


def utf8_position(text, byte_offset):
    raw = text.encode('utf-8')
    if not 0 <= byte_offset <= len(raw):
        raise ValueError('range-outside-text')
    return len(raw[:byte_offset].decode('utf-8', 'strict').encode('utf-16-le')) // 2


class NativeEditBridge:
    def __init__(self, context, still_valid=lambda: False, *, pending_char='', undo_group=None):
        self.context, self.still_valid = context, still_valid
        if len(pending_char) != 1 or not pending_char.isalpha() or ord(pending_char) > 0xFFFF:
            raise ValueError('held-character-required')
        self.pending_char = pending_char
        self.undo_group = undo_group
        self.timeout = self.budget_exceeded = self.write_started = False
        self.started = perf_counter()
        self.prepared = None
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        self.u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.u.GetWindowThreadProcessId.restype = w.DWORD
        self.u.IsWindow.argtypes, self.u.IsWindow.restype = [w.HWND], w.BOOL
        self.u.IsChild.argtypes, self.u.IsChild.restype = [w.HWND, w.HWND], w.BOOL
        self.u.GetWindowLongPtrW.argtypes, self.u.GetWindowLongPtrW.restype = [w.HWND, ctypes.c_int], ctypes.c_ssize_t
        self.u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, ctypes.c_ssize_t,
                                              w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.u.SendMessageTimeoutW.restype = ctypes.c_size_t

    def __enter__(self):
        if not self.still_valid():
            raise ValueError('context-changed')
        self.check_identity()
        return self

    def __exit__(self, *args):
        pass

    def check_deadline(self):
        if perf_counter() - self.started > .045:
            self.budget_exceeded = True
            raise TimeoutError('native-edit-budget')

    def check_identity(self):
        ctx = self.context
        if not self.u.IsWindow(ctx.focus) or not self.u.IsWindow(ctx.top):
            raise ValueError('window-closed')
        name = ctypes.create_unicode_buffer(128)
        self.u.GetClassNameW(ctx.focus, name, len(name))
        if name.value.casefold() != 'richedit50w':
            raise ValueError('unsupported-field-capability')
        if ctx.focus != ctx.top and not self.u.IsChild(ctx.top, ctx.focus):
            raise ValueError('unrelated-field')
        pid = w.DWORD()
        thread = self.u.GetWindowThreadProcessId(ctx.focus, ctypes.byref(pid))
        identity = (int(thread), int(pid.value))
        if not all(identity) or (hasattr(self, 'identity') and identity != self.identity):
            raise ValueError('field-owner-changed')
        self.identity = identity
        if self.u.GetWindowLongPtrW(ctx.focus, -16) & (0x20 | 0x800):
            raise ValueError('password-or-readonly')

    def msg(self, command, wp=0, lp=0):
        self.check_deadline()
        result = ctypes.c_size_t()
        if not self.u.SendMessageTimeoutW(self.context.focus, command, wp, lp, 3, 10, ctypes.byref(result)):
            self.timeout = True
            raise TimeoutError(f'native-edit-message-{command:x}-{ctypes.get_last_error()}')
        self.check_deadline()
        return result.value

    def selection(self):
        start, end = w.DWORD(), w.DWORD()
        self.msg(0xB0, ctypes.addressof(start), ctypes.addressof(end))  # EM_GETSEL
        return start.value, end.value

    def snapshot(self, *, allow_selection=False):
        self.check_identity()
        if not self.still_valid():
            raise ValueError('context-changed')
        start, end = self.selection()
        length = self.msg(0x000E)  # WM_GETTEXTLENGTH, UTF-16 units.
        if (start != end and not allow_selection) or not 0 <= start <= end <= length <= MAX_UNITS:
            raise ValueError('selection-or-document-limit')
        buf = ctypes.create_unicode_buffer(length + 1)
        copied = self.msg(0x000D, length + 1, ctypes.addressof(buf))
        text = buf.value
        if copied != length or len(text.encode('utf-16-le')) // 2 != length:
            raise ValueError('incomplete-text')
        # RichEdit positions count each internal paragraph mark once; WM_GETTEXT
        # expands it to CRLF. Keep the transaction snapshot in native CR form.
        text = text.replace('\r\n', '\r')
        if self.selection() != (start, end) or self.msg(0x000E) != length or not self.still_valid():
            raise ValueError('snapshot-changed')
        return text.encode('utf-8'), utf16_position(text, end if allow_selection else start)

    def prepare(self, start, end, target):
        if self.undo_group is None:
            raise ValueError('undo-group-not-provided')
        if not target or len(target) > 256:
            raise ValueError('replacement-limit')
        original, caret = self.snapshot()
        if end != caret:
            raise ValueError('range-not-at-caret')
        text = original.decode('utf-8', 'strict')
        self.prepared = (utf8_position(text, start), utf8_position(text, end),
                         ctypes.create_unicode_buffer(target.decode('utf-8', 'strict')),
                         original[:end] + self.pending_char.encode() + original[end:],
                         end + len(self.pending_char.encode()))

    def replace(self, start, end, target):
        if self.prepared is None or not self.still_valid():
            raise ValueError('context-changed-before-write')
        first, last, value, delivered, delivered_caret = self.prepared
        self.check_identity()
        self.check_deadline()
        # First materialize the held source character at the existing caret.
        # Otherwise native Undo would restore a word missing that character.
        if self.selection() != (last, last):
            raise ValueError('caret-changed')
        self.write_started = True
        self.msg(0x102, ord(self.pending_char), 1)  # WM_CHAR to the fixed field.
        if self.snapshot() != (delivered, delivered_caret):
            raise ValueError('held-character-not-confirmed')
        last += len(self.pending_char.encode('utf-16-le')) // 2
        self.msg(0x458)  # EM_STOPGROUPTYPING: replacement is separate from typed source.
        # Selection is also observable. Never forward/replay a possibly already
        # inserted physical character after any uncertain phase of this commit.
        self.undo_group.begin()
        try:
            self.msg(0xB1, first, last)  # EM_SETSEL; no focus change.
            if self.selection() != (first, last) or not self.still_valid():
                raise ValueError('selection-not-confirmed')
            self.msg(0xC2, 1, ctypes.addressof(value))
        finally:
            self.undo_group.end()
