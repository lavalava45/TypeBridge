"""Experimental opt-in range replacement for Unicode RichEdit fields.

No global hooks, clipboard, SendInput or focus changes. Uses the existing early
transaction protocol. Physical-input and cross-application release gates are
still open; the main app keeps this path disabled by default. EDIT is unsupported.
"""
import ctypes
from ctypes import wintypes as w
from time import perf_counter

MAX_UNITS = 32768
# Keep fast failing reads fast. A timed-out native mutation cannot be retried:
# the editor may apply the message after SendMessageTimeoutW returns. Windows
# Notepad has repeatedly exceeded the old 10 ms WM_CHAR limit under physical
# typing (ERROR_TIMEOUT/1460); allow bounded scheduling slack only for writes.
NATIVE_READ_TIMEOUT_MS = 10
NATIVE_MUTATION_TIMEOUT_MS = 35
# A native WM_CHAR is sent once per UTF-16 code unit from inside WH_KEYBOARD_LL.
# With a slow yet responsive RichEdit, each may consume the full 35 ms, so
# accepting 40 characters could stall the hook for >1 s without any timeout.
# Fail BEFORE the first mutating message instead of abandoning a half-written
# word. This is a conservative admission limit, not a real-time guarantee:
# the host thread can still be descheduled by Windows after admission.
NATIVE_MAX_INLINE_CHARS = 12
NATIVE_MAX_INLINE_ESTIMATE_MS = 700
NATIVE_MAX_WORKER_CHARS = 32
NATIVE_MAX_WORKER_ESTIMATE_MS = 1500
NATIVE_WORKER_PREFLIGHT_SECONDS = 0.15


def supports_native_edit(class_name):
    # Unicode RichEdit implementations with the same selection/text protocol.
    # D2DPT is used by current Notepad; this is a control capability, not an app rule.
    return class_name.casefold() in ("richedit50w", "richeditd2dpt")


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
    def __init__(self, context, still_valid=lambda: False, *, pending_char='', off_hook=False):
        self.context, self.still_valid = context, still_valid
        self.off_hook = bool(off_hook)
        if pending_char and (len(pending_char) != 1 or not pending_char.isprintable() or ord(pending_char) > 0xFFFF):
            raise ValueError('held-character-required')
        self.pending_char = pending_char
        self.timeout = self.budget_exceeded = self.write_started = False
        self.started = perf_counter()
        self.prepared = None
        self.phase = 'preflight'
        self.failure = ''
        self.trace = []
        self.last_snapshot = None
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
        # The 45 ms inline admission budget is only for reads / preflight.
        # The dedicated worker can allow more scheduling slack without
        # blocking the low-level keyboard callback. Once a
        # selection or character has been submitted, stopping midway through
        # the native typing group would leave a truncated word. The remaining
        # bounded Win32 messages must finish or report a genuine message
        # timeout; an uncertain result is then quarantined by LiveHook.
        limit = (NATIVE_WORKER_PREFLIGHT_SECONDS if getattr(self, 'off_hook', False)
                 else .045)
        if not getattr(self, 'write_started', False) and perf_counter() - self.started > limit:
            self.budget_exceeded = True
            label = 'budget-150ms' if getattr(self, 'off_hook', False) else 'budget-45ms'
            self.failure = f'native:{getattr(self, "phase", "unknown")}:{label}'
            raise TimeoutError('native-edit-budget')

    def check_identity(self):
        ctx = self.context
        if not self.u.IsWindow(ctx.focus) or not self.u.IsWindow(ctx.top):
            raise ValueError('window-closed')
        name = ctypes.create_unicode_buffer(128)
        self.u.GetClassNameW(ctx.focus, name, len(name))
        if not supports_native_edit(name.value):
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
        started = perf_counter()
        result = ctypes.c_size_t()
        ok, error, timeout_ms = False, 0, 0
        try:
            self.check_deadline()
            ctypes.set_last_error(0)
            if command in (0x102, 0xB1):  # WM_CHAR / EM_SETSEL.
                # Set only after the deadline precheck, immediately before send.
                # A timeout from this send can still mean delayed execution.
                self.write_started = True
            timeout_ms = (NATIVE_MUTATION_TIMEOUT_MS if command in (0x102, 0xB1)
                          else NATIVE_READ_TIMEOUT_MS)
            ok = bool(self.u.SendMessageTimeoutW(self.context.focus, command, wp, lp, 3,
                                                 timeout_ms, ctypes.byref(result)))
            if not ok:
                error = ctypes.get_last_error()
                self.timeout = True
                self.failure = f'native:{getattr(self, "phase", "unknown")}:message-{command:x}:error-{error}'
                raise TimeoutError(f'native-edit-message-{command:x}-{error}')
            self.check_deadline()
            return result.value
        finally:
            trace = getattr(self, 'trace', None)
            if trace is not None and len(trace) < 128:
                trace.append(f'native-step:phase={self.phase}:msg={command:x}:limit={timeout_ms}:ms={(perf_counter()-started)*1000:.3f}'
                             f':ok={int(ok)}:error={error}:write={int(self.write_started)}')

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
        result = text.encode('utf-8'), utf16_position(text, end if allow_selection else start)
        self.last_snapshot = result
        return result

    def prepare(self, start, end, target):
        self.phase = 'prepare'
        if not target or len(target) > 256:
            raise ValueError('replacement-limit')
        replacement = target.decode('utf-8', 'strict')
        if len(replacement) > 64 or any(not ch.isprintable() or ord(ch) > 0xFFFF for ch in replacement):
            raise ValueError('unsupported-replacement-character')
        # Admission is performed before EM_SETSEL / WM_CHAR, while refusing is
        # still safe. Worst-case message latency must fit an explicit budget,
        # including a held physical character, selection, readback, and HKL.
        # No partial truncation or approximate replacement is permitted.
        preflight_ms = (int(NATIVE_WORKER_PREFLIGHT_SECONDS * 1000)
                        if getattr(self, 'off_hook', False) else 45)
        predicted = (NATIVE_MUTATION_TIMEOUT_MS * (len(replacement) + 2)
                     + NATIVE_READ_TIMEOUT_MS * 10 + preflight_ms + 30)
        size_limit = NATIVE_MAX_WORKER_CHARS if getattr(self, 'off_hook', False) else NATIVE_MAX_INLINE_CHARS
        time_limit = (NATIVE_MAX_WORKER_ESTIMATE_MS if getattr(self, 'off_hook', False)
                      else NATIVE_MAX_INLINE_ESTIMATE_MS)
        if len(replacement) > size_limit or predicted > time_limit:
            self.failure = 'native:prepare:callback-admission-too-large'
            raise ValueError('callback-admission-too-large')
        # replace_prefix always takes a fresh snapshot again before committing;
        # use its first snapshot for offset preparation, without a duplicate read.
        original, caret = getattr(self, 'last_snapshot', None) or self.snapshot()
        if end != caret:
            raise ValueError('range-not-at-caret')
        text = original.decode('utf-8', 'strict')
        self.prepared = (utf8_position(text, start), utf8_position(text, end),
                         replacement,
                         original[:end] + self.pending_char.encode() + original[end:],
                         end + len(self.pending_char.encode()))

    def replace(self, start, end, target):
        self.phase = 'precommit'
        if self.prepared is None or not self.still_valid():
            raise ValueError('context-changed-before-write')
        first, last, value, delivered, delivered_caret = self.prepared
        self.check_identity()
        self.check_deadline()
        # First materialize the held source character at the existing caret.
        # Otherwise native Undo would restore a word missing that character.
        if self.pending_char:
            if self.selection() != (last, last):
                raise ValueError('caret-changed')
            self.phase = 'deliver-held'
            self.msg(0x102, ord(self.pending_char), 1)  # WM_CHAR to the fixed field.
        self.phase = 'verify-source'
        if self.snapshot() != (delivered, delivered_caret):
            raise ValueError('held-character-not-confirmed')
        last += len(self.pending_char.encode('utf-16-le')) // 2
        self.msg(0x458)  # EM_STOPGROUPTYPING: replacement is separate from typed source.
        # Selection is also observable. Never forward/replay a possibly already
        # inserted physical character after any uncertain phase of this commit.
        self.phase = 'select-range'
        self.msg(0xB1, first, last)  # EM_SETSEL; no focus change.
        if self.selection() != (first, last) or not self.still_valid():
            raise ValueError('selection-not-confirmed')
        try:
            # EM_REPLACESEL loses the replaced source in native Undo for our
            # Hebrew cases. Native typing records the deletion plus insertion
            # together, without unbounded TOM/COM edit collections.
            self.phase = 'write-replacement'
            for char in value:
                if not self.still_valid():
                    raise ValueError('context-changed-during-write')
                self.msg(0x102, ord(char), 1)
        finally:
            # A following physical character must be its own Undo operation.
            if not self.timeout and not self.budget_exceeded:
                self.msg(0x458)  # EM_STOPGROUPTYPING.
        self.phase = 'verify-result'
