"""R10 Scintilla range adapter, used by the off-hook manual command.

UTF-8, one stream selection, selected range only, bounded remote buffers.
Existing early-word transport/limits are unchanged. A native adapter cannot
make separate cross-process messages atomic against arbitrary host edits.
"""
import ctypes
from ctypes import wintypes as w
from time import perf_counter

from early_scintilla import ScintillaEarlyBridge
from selection_cycle import MAX_SELECTION, SelectionSnapshot

MAX_BYTES = MAX_SELECTION * 4


class TextRange(ctypes.Structure):
    # Sci_TextRangeFull: pointer-sized positions, independently of the host's
    # legacy Sci_PositionCR typedef (Notepad++ changes that typedef to intptr_t).
    _fields_ = [('first', ctypes.c_ssize_t), ('last', ctypes.c_ssize_t),
                ('text', ctypes.c_void_p)]


class ScintillaRangeTransport(ScintillaEarlyBridge):
    """Notepad++-bound Win32 transport; use outside the keyboard callback."""
    def __init__(self, context):
        super().__init__(context)
        self.pid = self.tid = None
        self.closed = False
        self.u.GetWindowThreadProcessId.restype = w.DWORD
        self.k.IsWow64Process.argtypes = [w.HANDLE, ctypes.POINTER(w.BOOL)]
        self.k.IsWow64Process.restype = w.BOOL

    def bind_process(self, pid, tid):
        # Called by __enter__; an isolated test subclass binds ONLY its own child.
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise ValueError('selection-transport-requires-64-bit')
        wow = w.BOOL()
        if not self.k.IsWow64Process(self.handle, ctypes.byref(wow)) or wow.value:
            raise ValueError('unsupported-target-bitness')
        self.pid, self.tid = pid, tid

    def __enter__(self):
        try:
            super().__enter__()
            pid = w.DWORD()
            tid = self.u.GetWindowThreadProcessId(self.context.focus, ctypes.byref(pid))
            self.bind_process(pid.value, tid)
            self.check_identity()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def check_identity(self):
        pid = w.DWORD()
        tid = self.u.GetWindowThreadProcessId(self.context.focus, ctypes.byref(pid))
        name = ctypes.create_unicode_buffer(128)
        self.u.GetClassNameW(self.context.focus, name, len(name))
        if not self.pid or pid.value != self.pid or tid != self.tid or name.value != 'Scintilla':
            raise ValueError('selection-window-changed')

    def __exit__(self, *exc):
        try:
            super().__exit__(*exc)
        finally:
            self.closed = True
            self.handle = self.remote = None

    def check_open(self):
        if getattr(self, 'closed', False):
            raise ValueError('selection-transport-closed')
        if self.timeout:
            raise TimeoutError('selection-transport-faulted')

    def check_deadline(self):
        if perf_counter() - self.started > .75:
            self.budget_exceeded = True
            raise TimeoutError('selection-operation-budget')

    def msg(self, command, wp=0, lp=0):
        self.check_open()
        self.check_identity()
        self.check_deadline()
        result = ctypes.c_size_t()
        if not self.u.SendMessageTimeoutW(self.context.focus, command, wp, lp,
                                          0x23, 40, ctypes.byref(result)):
            self.timeout = True
            raise TimeoutError('selection-editor-message')
        return result.value

    def allocate(self):
        self.check_open()
        if not self.remote:
            self.capacity = ctypes.sizeof(TextRange) + 2 * (MAX_BYTES + 1)
            self.remote = self.k.VirtualAllocEx(self.handle, None, self.capacity, 0x3000, 4)
            if not self.remote:
                raise OSError('selection-buffer-allocation')
        return self.remote + ctypes.sizeof(TextRange)

    def write_memory(self, address, payload):
        self.check_open()
        data = ctypes.create_string_buffer(payload)
        count = ctypes.c_size_t()
        if (not self.k.WriteProcessMemory(self.handle, address, data, len(payload), ctypes.byref(count))
                or count.value != len(payload)):
            raise OSError('selection-buffer-write')

    def read_range(self, first, last):
        if not 0 <= first <= last <= 0x7FFFFFFF or last-first > MAX_BYTES:
            raise ValueError('selection-byte-limit')
        address = self.allocate()
        request = TextRange(first, last, address)
        self.write_memory(self.remote, bytes(request))
        # FULL has a stable pointer-sized ABI. Never fall back to legacy 2162:
        # its layout differs between stock Scintilla and Notepad++ on x64.
        if self.msg(2039, 0, self.remote) != last-first:
            raise ValueError('selection-incomplete-range')
        data = ctypes.create_string_buffer(last-first+1)
        count = ctypes.c_size_t()
        if (not self.k.ReadProcessMemory(self.handle, address, data, len(data), ctypes.byref(count))
                or count.value != len(data) or data.raw[-1] != 0 or b'\0' in data.raw[:-1]):
            raise OSError('selection-buffer-read')
        return data.raw[:-1]

    def prepare_text(self, text):
        payload = text.encode('utf-8')
        if not 0 < len(payload) <= MAX_BYTES or b'\0' in payload:
            raise ValueError('selection-replacement-limit')
        self.target_addr = self.allocate() + MAX_BYTES + 1
        self.write_memory(self.target_addr, payload+b'\0')
        return len(payload)

class ScintillaSelectionAdapter:
    def __init__(self, transport, scope, revision, still_valid):
        self.transport, self.scope, self.revision = transport, scope, revision
        self.still_valid = still_valid
        self.write_started = False
        self.prepared = None
        self.document = None

    def metadata(self):
        if not self.still_valid():
            raise ValueError('selection-context-changed')
        msg = self.transport.msg
        if (msg(2137) != 65001 or msg(2140) or msg(2570) != 1 or msg(2423) != 0
                or msg(2372) or msg(2581, 0) or msg(2583, 0) or not msg(2019)):
            raise ValueError('selection-unsupported-edit-state')
        document, anchor, caret, length = msg(2357), msg(2009), msg(2008), msg(2006)
        if (not document or not 0 <= min(anchor,caret) <= max(anchor,caret) <= length <= 0x7FFFFFFF
                or abs(anchor-caret) > MAX_BYTES):
            raise ValueError('selection-range-limit')
        if self.document is None:
            self.document = document
        if document != self.document:
            raise ValueError('selection-document-changed')
        return document, anchor, caret, length

    def snapshot(self):
        before = self.metadata()
        document, anchor, caret, _ = before
        data = self.transport.read_range(min(anchor,caret),max(anchor,caret)) if anchor != caret else b''
        text = data.decode('utf-8', 'strict')
        if self.metadata() != before or not self.still_valid():
            raise ValueError('selection-changed-during-read')
        return SelectionSnapshot(self.scope+('scintilla-document',document), self.revision,
                                 anchor,caret,text,'utf8')

    def prepare(self, plan):
        if plan.before.unit != 'utf8' or plan.after.unit != 'utf8':
            raise ValueError('selection-requires-utf8-positions')
        self.write_started = False
        self.prepared = None
        self.transport.prepare_text(plan.after.text)
        self.prepared = plan

    def replace(self, plan):
        if self.prepared is not plan or self.snapshot() != plan.before:
            raise ValueError('selection-plan-changed-before-commit')
        msg = self.transport.msg
        first, last = sorted((plan.before.anchor,plan.before.caret))
        msg(2190, first)
        msg(2192, last)
        # Preserve target virtual-space semantics: never insert implicit spaces.
        msg(2728, 0)
        msg(2730, 0)
        if msg(2191) != first or msg(2193) != last or self.snapshot() != plan.before:
            raise ValueError('selection-target-changed')
        # REPLACETARGET has an internal UndoGroup. A single call avoids an
        # external BEGIN whose timeout could leave an unconfirmed group open.
        self.write_started = True
        self.transport.write_started = True
        size = len(plan.after.text.encode('utf-8'))
        if msg(2194, size, self.transport.target_addr) != size:
            raise OSError('selection-replacement-count')
        msg(2160, plan.after.anchor, plan.after.caret)
