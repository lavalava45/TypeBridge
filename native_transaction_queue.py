"""Bounded physical-key barrier for an off-hook editor transaction.

The low-level callback does not edit text or wait for the worker. It only
records native key events while one exclusive editor job is in progress.
The worker replays the physical scan codes, in order, after committing the
replacement and (when verified) switching HKL. Never replay into a different
focused field. All state transitions use a very small lock, not a Win32 call.

This is intentionally limited to one job and a bounded input queue. Failure
to replay is reported, not disguised as verified input. No unbounded backlog.
"""

from dataclasses import dataclass, replace
from threading import Lock

KEYUP_MESSAGES = frozenset({0x101, 0x105})
# A bounded, generous buffer for several seconds of unusually fast typing.
MAX_BUFFERED_EVENTS = 256


@dataclass(frozen=True)
class PhysicalKey:
    vk: int
    scan: int
    flags: int
    message: int
    trigger: bool = False


class NativeTransactionQueue:
    def __init__(self, limit=MAX_BUFFERED_EVENTS):
        self._lock = Lock()
        self._events = []
        self.active = False
        self.busy = False
        self.overflow = False
        self.aborted_reason = ''
        self.limit = limit
        self.trigger_vk = None
        self.trigger_up_pending = False
        self.command_trigger = False
        self.trigger_repeated = False
        self.trigger_irreversible = False
        self.target_scope = None

    def begin(self, initial=None, *, target_scope=None, command_trigger=False):
        with self._lock:
            if self.busy:
                return False
            self.active, self.busy, self.overflow = True, True, False
            self.aborted_reason = ''
            self._events = [initial] if initial is not None else []
            self.trigger_vk = initial.vk if initial is not None else None
            self.trigger_up_pending = initial is not None
            self.command_trigger = bool(command_trigger)
            self.trigger_repeated = False
            self.trigger_irreversible = False
            self.target_scope = target_scope
            return True

    def capture(self, event):
        """Returns True if the physical event must be held by the hook."""
        with self._lock:
            if not self.active:
                return False
            if self.trigger_up_pending and event.vk == self.trigger_vk:
                # Only command hotkeys (Pause/F8) consume ALL repeats. An
                # ordinary letter may auto-repeat while the worker is busy:
                # its extra KEYDOWNs are independent text and must survive.
                if event.message in KEYUP_MESSAGES:
                    event = replace(event, trigger=(self.command_trigger or
                                                    not self.trigger_repeated))
                elif self.command_trigger:
                    event = replace(event, trigger=True)
                else:
                    self.trigger_repeated = True
                    event = replace(event, trigger=False)
                if event.message in KEYUP_MESSAGES:
                    self.trigger_up_pending = False
            if len(self._events) >= self.limit:
                self.overflow = True
                self.active = False
                self.aborted_reason = 'buffer-overflow'
                # Fail open for this and all subsequent physical events.
                # The accepted prefix is retained only for diagnostics; the
                # worker MUST NOT replay it into a possibly changed document.
                return False
            else:
                self._events.append(event)
            return True

    def mark_trigger_irreversible(self):
        """A held trigger might already have affected text: never replay it."""
        with self._lock:
            if self.busy:
                self.trigger_irreversible = True

    def reset_unaccepted_trigger(self):
        """Only after SendInput explicitly reports zero accepted events."""
        with self._lock:
            self.trigger_irreversible = False

    def abort(self, reason):
        """Stop swallowing input immediately; worker still owns cleanup.

        Keeping busy=True prevents a second transaction from racing the
        original worker after focus changes. Already held events cannot be
        delivered safely to a new target and are explicitly reported lost.
        """
        with self._lock:
            if not self.busy:
                return 0
            self.active = False
            if not self.aborted_reason:
                self.aborted_reason = reason
            return len(self._events)

    def take(self):
        """Atomically take buffered input; still capture arrivals while replaying."""
        with self._lock:
            events, self._events = self._events, []
            return events

    def close_if_drained(self):
        with self._lock:
            if self._events:
                return False
            self.active = False
            self.busy = False
            self.trigger_up_pending = False
            self.target_scope = None
            return True

    def close(self):
        """Emergency shutdown: return unreplayed keys, do not leave a barrier."""
        with self._lock:
            events, self._events = self._events, []
            self.active = False
            self.busy = False
            self.trigger_up_pending = False
            self.target_scope = None
            return events
