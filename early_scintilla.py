"""Opt-in synchronous Notepad++ early correction, never keyboard replay.

The triggering physical KEYDOWN has not reached the editor yet. Replace only
the independently observed preceding prefix, including that new character in
the replacement. Uncertain writes must never be retried or rolled back blindly.
"""
import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
from time import perf_counter


_SAFE_FAILURE_CODES = frozenset({
    'native-edit-budget', 'early-read-budget', 'editor-total-budget',
    'context-changed', 'snapshot-changed', 'selection-not-confirmed',
    'held-character-not-confirmed', 'prefix-not-delivered',
})


def failure_reason(error):
    """Only diagnostic codes, never arbitrary editor/user text, leave the hook."""
    label = type(error).__name__
    detail = str(error)
    if detail in _SAFE_FAILURE_CODES:
        return label + ':' + detail
    return label


@dataclass(frozen=True)
class EarlyResult:
    state: str  # refused / verified / uncertain
    reason: str


def replace_prefix(bridge, expected: str, replacement: str, still_valid) -> EarlyResult:
    """Editor-independent transaction protocol, tested with a byte-buffer model."""
    mutation_attempted = False
    try:
        original, caret = bridge.snapshot()
        source = expected.encode("utf-8")
        target = replacement.encode("utf-8")
        if not source or caret < len(source) or original[caret-len(source):caret] != source:
            return EarlyResult("refused", "prefix-not-delivered")
        before = original[:caret-len(source)].decode("utf-8", "strict")
        if before and not (before[-1].isspace() or before[-1] in '([{«"'):
            return EarlyResult("refused", "not-a-word-start")
        start = caret - len(source)
        wanted = original[:start] + target + original[caret:]
        bridge.prepare(start, caret, target)
        if not still_valid() or bridge.snapshot() != (original, caret):
            return EarlyResult("refused", "context-or-document-changed")
        bridge.check_deadline()
        if not still_valid():
            return EarlyResult("refused", "context-changed-before-write")
        mutation_attempted = True
        bridge.replace(start, caret, target)
        if bridge.snapshot() != (wanted, start + len(target)):
            return EarlyResult("uncertain", "write-not-confirmed")
        if not still_valid():
            return EarlyResult("uncertain", "context-changed-after-write")
        return EarlyResult("verified", "native-replacement-confirmed")
    except Exception as error:
        # Retain a sanitized preflight failure for metadata-only diagnostics.
        # Never record arbitrary exception text: it can contain document data.
        if not getattr(bridge, 'failure', ''):
            bridge.failure = failure_reason(error)
        attempted = mutation_attempted and getattr(bridge, "write_started", True)
        return EarlyResult("uncertain" if attempted else "refused", type(error).__name__)


class ScintillaEarlyBridge:
    """Only explicitly matched Notepad++/Scintilla HWNDs, UTF-8, one caret.

    No executable memory or code injection: remote memory is a text buffer.
    On timeout the small buffer is retained until Notepad++ exits, since the
    receiving thread might still access it. The caller disables early mode.
    """
    def __init__(self, context):
        self.context = context
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handle = self.remote = None
        self.timeout = False
        self.budget_exceeded = False
        self.write_started = False
        self.started = perf_counter()
        self.capacity = 0
        self.u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM,
            ctypes.c_ssize_t, w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.u.SendMessageTimeoutW.restype = ctypes.c_size_t
        self.u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenProcess.restype = w.HANDLE
        self.k.VirtualAllocEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD, w.DWORD]
        self.k.VirtualAllocEx.restype = ctypes.c_void_p
        for name in ("ReadProcessMemory", "WriteProcessMemory"):
            function = getattr(self.k, name)
            function.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
            function.restype = w.BOOL
        self.k.VirtualFreeEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD]
        self.k.CloseHandle.argtypes = [w.HANDLE]

    def check_deadline(self):
        if perf_counter() - self.started > 0.045:
            raise TimeoutError("early-read-budget")

    def msg(self, command, wp=0, lp=0):
        # SCI_ENDUNDOACTION (2079) must be attempted after a successful
        # SCI_BEGINUNDOACTION even when a late but successful write exhausted
        # the transaction budget. This is cleanup, NOT a repeated text edit;
        # it retains the individual bounded SendMessageTimeoutW limit below.
        if (command != 2079 and
                perf_counter() - self.started > (0.09 if self.write_started else 0.045)):
            self.budget_exceeded = True
            raise TimeoutError("editor-total-budget")
        result = ctypes.c_size_t()
        if not self.u.SendMessageTimeoutW(self.context.focus, command, wp, lp,
                                          3, 15, ctypes.byref(result)):
            self.timeout = True
            raise TimeoutError("editor-message")
        return result.value

    def __enter__(self):
        # Even a same-named window in another program is insufficient.
        if self.context.process_name != "notepad++.exe":
            raise ValueError("unsupported-process")
        for hwnd, expected in ((self.context.top, "Notepad++"),
                               (self.context.focus, "Scintilla")):
            name = ctypes.create_unicode_buffer(128)
            self.u.GetClassNameW(hwnd, name, len(name))
            if name.value != expected:
                raise ValueError("unsupported-window")
        pid = w.DWORD()
        self.u.GetWindowThreadProcessId(self.context.focus, ctypes.byref(pid))
        if not pid.value:
            raise ValueError("missing-process")
        self.handle = self.k.OpenProcess(0x1038, False, pid.value)
        if not self.handle:
            raise OSError("editor-access-denied")
        return self

    def __exit__(self, *exc):
        if self.remote and not self.timeout:
            self.k.VirtualFreeEx(self.handle, self.remote, 0, 0x8000)
        if self.handle:
            self.k.CloseHandle(self.handle)

    def snapshot(self):
        if (self.msg(2137) != 65001 or self.msg(2140) or self.msg(2570) != 1
                or self.msg(2187) or self.msg(2581) or self.msg(2583)
                or not self.msg(2381)):
            raise ValueError("unsupported-edit-state")
        caret, anchor = self.msg(2008), self.msg(2009)
        length = self.msg(2006)
        if caret != anchor or not 0 <= caret <= length or length > 65536:
            raise ValueError("selection-or-document-limit")
        if not self.remote:
            self.capacity = length + 1024
            self.remote = self.k.VirtualAllocEx(self.handle, None, self.capacity, 0x3000, 4)
            if not self.remote:
                raise OSError("text-buffer-allocation")
        if length + 1 > self.capacity - 512:
            raise ValueError("document-grew")
        self.msg(2182, length + 1, self.remote)
        output = ctypes.create_string_buffer(length + 1)
        copied = ctypes.c_size_t()
        if (not self.k.ReadProcessMemory(self.handle, self.remote, output,
                                         length + 1, ctypes.byref(copied))
                or copied.value != length + 1 or output.raw[-1] != 0):
            raise OSError("text-readback")
        data = output.raw[:-1]
        data.decode("utf-8", "strict")
        if (self.msg(2006) != length or self.msg(2008) != caret or self.msg(2009) != caret
                or b"\x00" in data):
            raise ValueError("snapshot-changed")
        return data, caret

    def prepare(self, start, end, target):
        if not 0 < len(target) <= 256:
            raise ValueError("replacement-limit")
        self.target_addr = self.remote + self.capacity - 512
        payload = ctypes.create_string_buffer(target + b"\x00")
        copied = ctypes.c_size_t()
        if (not self.k.WriteProcessMemory(self.handle, self.target_addr, payload,
                                          len(payload), ctypes.byref(copied))
                or copied.value != len(payload)):
            raise OSError("replacement-buffer")

    def replace(self, start, end, target):
        self.msg(2190, start)
        self.msg(2192, end)
        if self.msg(2191) != start or self.msg(2193) != end:
            raise ValueError("target-changed")
        self.msg(2078)  # One native Undo action.
        try:
            self.write_started = True
            if self.msg(2194, len(target), self.target_addr) != len(target):
                raise OSError("replacement-count")
            self.msg(2160, start + len(target), start + len(target))
        finally:
            # BEGINUNDOACTION was confirmed above. Even if REPLACETARGET or
            # the caret update timed out, leaving this group open may merge
            # *future user typing* into our Undo action. ENDUNDOACTION is a
            # single bounded cleanup message, never a repeat text mutation.
            # Keep self.timeout set: the write remains uncertain, and remote
            # memory must not be freed while a late edit may still use it.
            self.msg(2079)
