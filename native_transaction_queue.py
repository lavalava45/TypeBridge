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
MODIFIER_VKS = frozenset({0x10, 0x11, 0x12, 0x5B, 0x5C,
                          0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5})
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
        self.owner = 0
        self.finishing = False
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
        self.modifier_states = {}
        self.expected_hkl = None
        self.expected_language = None
        self.replay_modifiers = None
        self.replay_thread_id = 0

    def begin(self, initial=None, *, target_scope=None, command_trigger=False,
              expected_hkl=None, expected_language=None, replay_modifiers=None,
              replay_thread_id=0, recovery_events=()):
        with self._lock:
            if (self.busy or len(recovery_events) > 2 * self.limit
                    or (initial is not None and recovery_events)):
                return False
            self.owner += 1
            self.finishing = False
            self.active, self.busy, self.overflow = True, True, False
            self.aborted_reason = ''
            # Explicit recovery may add one compensating KEYUP per saved
            # KEYDOWN. Admit that bounded batch atomically, without routing
            # it through capture's ordinary live-input capacity.
            self._events = list(recovery_events) if recovery_events else (
                [initial] if initial is not None else [])
            self.trigger_vk = initial.vk if initial is not None else None
            self.trigger_up_pending = initial is not None
            self.command_trigger = bool(command_trigger)
            self.trigger_repeated = False
            self.trigger_irreversible = False
            self.target_scope = target_scope
            self.modifier_states = {}
            self.expected_hkl = expected_hkl
            self.expected_language = expected_language
            self.replay_modifiers = replay_modifiers
            self.replay_thread_id = replay_thread_id
            return True

    def capture(self, event):
        """Returns True if the physical event must be held by the hook."""
        with self._lock:
            if self.finishing:
                return False
            if self.busy and event.vk in MODIFIER_VKS:
                # Modifier state belongs to Windows, not to the text queue.
                # In particular, holding a KEYUP can strand an already
                # delivered KEYDOWN when focus changes or replay fails.
                self.modifier_states[event.vk] = event.message not in KEYUP_MESSAGES
                self.active = False
                if not self.aborted_reason:
                    self.aborted_reason = 'modifier-state-changed'
                return False
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
                # The accepted prefix is retained for explicit recovery; the
                # worker MUST NOT automatically replay it after overflow.
                return False
            else:
                self._events.append(event)
            return True

    def capture_trigger_keyup(self, event):
        """Capture only a held command/delimiter's matching physical KEYUP.

        The source HKL and UIA revision may change after a successful edit,
        even before Windows delivers the KEYUP for its already-held trigger.
        This exact release is NOT a new text mutation or new recipient; it
        belongs to the original suppressed KEYDOWN. A hook may call this
        before a post-commit context comparison, while every OTHER physical
        event still goes through the strict scope-sensitive capture path.

        Never intercept a keyup when no matching trigger KEYDOWN was buffered,
        when the barrier is aborting/finishing, or after a repeat release.
        """
        with self._lock:
            if (not self.busy or not self.active or self.finishing
                    or self.aborted_reason or not self.trigger_up_pending
                    or self.trigger_vk is None or event.vk != self.trigger_vk
                    or event.message not in KEYUP_MESSAGES):
                return False
            if len(self._events) >= self.limit:
                self.overflow = True
                self.active = False
                self.aborted_reason = 'buffer-overflow'
                return False
            self._events.append(replace(
                event, trigger=(self.command_trigger or not self.trigger_repeated)))
            self.trigger_up_pending = False
            return True

    def mark_trigger_irreversible(self, *, owner=None):
        """A held trigger might already have affected text: never replay it."""
        with self._lock:
            if owner is not None and owner != self.owner:
                return
            if self.busy:
                self.trigger_irreversible = True

    def reset_unaccepted_trigger(self, *, owner=None):
        """Only after SendInput explicitly reports zero accepted events."""
        with self._lock:
            if owner is not None and owner != self.owner:
                return
            self.trigger_irreversible = False

    def owns(self, owner):
        with self._lock:
            return self.owner == owner

    def abort(self, reason, *, owner=None):
        """Stop swallowing input immediately; worker still owns cleanup.

        Keeping busy=True prevents a second transaction from racing the
        original worker after focus changes. Already held events cannot be
        delivered safely to a new target and are explicitly reported lost.
        """
        with self._lock:
            if (owner is not None and owner != self.owner) or not self.busy or self.finishing:
                return 0
            self.active = False
            if not self.aborted_reason:
                self.aborted_reason = reason
            return len(self._events)

    def take(self, *, owner=None):
        """Atomically take buffered input; still capture arrivals while replaying."""
        with self._lock:
            if owner is not None and owner != self.owner:
                return []
            events, self._events = self._events, []
            return events

    def close_if_drained(self, *, owner=None, release=True):
        with self._lock:
            if owner is not None and owner != self.owner:
                return False
            if self._events:
                return False
            self.active = False
            self.busy = not release
            self.finishing = True
            self.trigger_up_pending = False
            self.target_scope = None
            return True

    def close(self, *, owner=None, release=True):
        """Emergency shutdown: return unreplayed keys, do not leave a barrier."""
        with self._lock:
            if owner is not None and owner != self.owner:
                return []
            events, self._events = self._events, []
            self.active = False
            self.busy = not release
            self.finishing = True
            self.trigger_up_pending = False
            self.target_scope = None
            return events
