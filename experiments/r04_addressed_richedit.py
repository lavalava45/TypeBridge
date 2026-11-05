"""R04 research ONLY: a target-bound RichEdit transaction model.

No Win32 imports, hooks, clipboard, focus switching, or live integration.
An injected API models the HWND-specific EM_SETSEL/EM_REPLACESEL protocol.
This demonstrates a narrow property, NOT browser/DOM or physical safety.
"""
from dataclasses import dataclass

EM_SETSEL = 0x00B1
EM_REPLACESEL = 0x00C2
SMTO_FLAGS = 0x0001 | 0x0002 | 0x0020  # BLOCK | ABORTIFHUNG | ERRORONEXIT.
RICHEDIT_CLASSES = frozenset(('richedit50w', 'richeditd2dpt'))


@dataclass(frozen=True)
class TargetLease:
    hwnd: int
    pid: int
    thread_id: int
    class_name: str
    focus_epoch: int
    hkl: int
    guard_revision: int


@dataclass(frozen=True)
class Outcome:
    state: str                    # refused, verified, uncertain
    reason: str
    accepted_events: None = None  # HWND messages never report SendInput counts.
    tail_for_manual_recovery: tuple = ()
    tail_ready_for_ordered_replay: tuple = ()


class AddressedRichEditExperiment:
    """Only a model candidate, not an API adapter for production.

    api.identity(hwnd) -> (pid, thread_id, class_name)
    api.context() -> (focused_hwnd, focus_epoch, hkl, guard_revision, safety)
    api.document(hwnd) -> (text, caret, selection)
    api.message(hwnd, code, wparam, lparam, flags) -> bool

    A stable *distinct* HWND is the necessary scope of the B-unchanged claim.
    Same-HWND DOM elements and HWND reuse are explicitly OUT OF SCOPE.
    """

    def __init__(self, api, lease: TargetLease):
        self.api, self.lease = api, lease
        self.attempted = False

    def _valid(self):
        lease = self.lease
        if lease.class_name.casefold() not in RICHEDIT_CLASSES:
            return False
        try:
            identity = self.api.identity(lease.hwnd)
            focused, epoch, hkl, revision, safety = self.api.context()
        except Exception:
            return False  # A stuck/failing provider is never safe authority.
        if identity != (lease.pid, lease.thread_id, lease.class_name):
            return False
        return (focused == lease.hwnd and epoch == lease.focus_epoch
                and hkl == lease.hkl and revision == lease.guard_revision
                and safety == 'safe')

    def commit(self, expected: str, replacement: str, *, held_tail=()):
        if self.attempted:
            return Outcome('refused', 'duplicate-attempt')
        self.attempted = True
        tail = tuple(held_tail)
        if not expected or not replacement or not self._valid():
            return Outcome('refused', 'preflight-denied', tail_for_manual_recovery=tail)
        try:
            before_text, caret, selection = self.api.document(self.lease.hwnd)
        except Exception:
            return Outcome('refused', 'source-read-unavailable',
                           tail_for_manual_recovery=tail)
        start = caret - len(expected)
        if (selection != (caret, caret) or start < 0
                or before_text[start:caret] != expected or not self._valid()):
            return Outcome('refused', 'source-changed', tail_for_manual_recovery=tail)
        desired = before_text[:start] + replacement + before_text[caret:]
        try:
            # These are explicit messages to the ORIGINAL hwnd; never read
            # GetForegroundWindow() again to choose a mutation destination.
            if not self.api.message(self.lease.hwnd, EM_SETSEL,
                                    start, caret, SMTO_FLAGS):
                return Outcome('uncertain', 'selection-dispatch-unconfirmed',
                               tail_for_manual_recovery=tail)
            if (self.api.document(self.lease.hwnd)[2] != (start, caret)
                    or not self._valid()):
                return Outcome('refused', 'identity-or-selection-lost',
                               tail_for_manual_recovery=tail)
            # EM_REPLACESEL is one HWND-addressed text mutation; it can time
            # out AFTER changing A. A retry or speculative Undo is forbidden.
            if not self.api.message(self.lease.hwnd, EM_REPLACESEL,
                                    1, replacement, SMTO_FLAGS):
                return Outcome('uncertain', 'write-dispatch-unconfirmed',
                               tail_for_manual_recovery=tail)
            observed_text, observed_caret, _ = self.api.document(self.lease.hwnd)
            if (observed_text, observed_caret) != (desired, start + len(replacement)):
                return Outcome('uncertain', 'readback-mismatch',
                               tail_for_manual_recovery=tail)
            if not self._valid():
                return Outcome('uncertain', 'focus-changed-after-write',
                               tail_for_manual_recovery=tail)
            return Outcome('verified', 'target-readback-matched',
                           tail_ready_for_ordered_replay=tail)
        except Exception:
            # Unknown whether a previously dispatched message was processed.
            return Outcome('uncertain', 'provider-or-dispatch-error',
                           tail_for_manual_recovery=tail)
