"""Passive native-COM password metadata guard with isolated persistent worker.

The hook only sees immutable metadata; no password or document text is read.
The shared UIA subprocess services password, caret and selection requests.
Provider failures expire permissions under strict policy, while known positive
password evidence persists until a focus transition is independently verified.
"""
from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class Snapshot:
    top: int = 0
    focus: int = 0
    state: str = 'unknown'
    checked: float = 0.0
    revision: int = 0
    reason: str = 'starting'
    element_id: str = ''
    addressable: bool = False


class PasswordFieldGuard:
    def __init__(self, changed=lambda: None, *, clock=time.monotonic,
                 require_element_id=False, native_only=False, native_fast_path=False,
                 explicit_password_only=False, disabled=False):
        self.changed, self.clock = changed, clock
        # Explicit simple-mode opt-out. Preserve the complete legacy guard
        # implementation without starting a COM watcher or reading fields.
        # This is intentionally not a synthetic safe/password determination.
        self.disabled = bool(disabled)
        # The application blocks positive password evidence, not missing UIA.
        # Keep strict metadata consumers compatible (e.g. addressed probes).
        self.explicit_password_only = bool(explicit_password_only)
        # The real GUI/headless opt-in must never accept the legacy 3/5-field
        # protocol, because it cannot distinguish different HTML inputs.
        # Legacy model fixtures may still explicitly exercise that protocol.
        self.require_element_id = bool(require_element_id)
        # Kept for constructor compatibility with older harnesses only. No
        # production policy refuses a browser based on this historical flag.
        self.native_only = bool(native_only)
        self.native_fast_path = bool(native_fast_path)
        self.snapshot = Snapshot()
        self.invalidated_at = 0.0
        self.last_response = self.clock()
        self.state_since = self.clock()
        self.unknown_notified = False
        self.stopped = threading.Event()
        self.lock = threading.RLock()
        self.refreshed = threading.Condition(self.lock)
        self.process = None
        self.worker = None
        self._event_generation = None
        self._generation_fault = False

    def _update(self, top, focus, state, reason='checked', *, started=None,
                element_id='', checked_at=None, addressable=None, generation=None):
        if addressable is None:
            # Legacy fixtures calling _update directly still model an
            # addressable safe editor; unknown/password are never writable.
            addressable = state == 'safe'
        with self.lock:
            if started is not None and started <= self.invalidated_at:
                return
            previous = self.snapshot
            generation_changed = (generation is not None
                                  and generation != self._event_generation)
            if generation is not None and not self._generation_fault:
                self._event_generation = generation
            changed = (top, focus, state, element_id, addressable) != (
                previous.top, previous.focus, previous.state, previous.element_id,
                previous.addressable)
            revision_changed = changed or generation_changed
            if self.explicit_password_only:
                # Readiness/TTL is not a field change. A first or missing UIA
                # answer must not discard the word already tracked by the hook.
                revision_changed = (generation_changed
                                    or (state == 'password') != (previous.state == 'password')
                                    or reason == 'focus-change')
                target = getattr(self, '_last_target', None)
                if top > 0 and focus > 0:
                    if target is not None:
                        revision_changed |= ((top, focus) != target[:2]
                            or bool(element_id and target[2] and element_id != target[2]))
                    retained_id = (element_id or (target[2] if target is not None
                                   and target[:2] == (top, focus) else ''))
                    self._last_target = (top, focus, retained_id)
                elif reason == 'focus-change':
                    self._last_target = None
            if changed:
                self.state_since = self.clock()
                self.unknown_notified = False
            notice_changed = changed or generation_changed or reason != previous.reason
            if state == 'unknown' and not self.unknown_notified and self.clock() - self.state_since >= 2:
                self.unknown_notified = True
                notice_changed = True
            self.snapshot = Snapshot(top, focus, state,
                                     self.clock() if checked_at is None else checked_at,
                                     previous.revision + int(revision_changed), reason, element_id,
                                     addressable)
            self.refreshed.notify_all()
        if notice_changed:
            self.changed()

    def ingest(self, line):
        if self.disabled:
            return
        started = None
        checked_at = None
        addressable = False
        generation = None
        try:
            parts = line.strip().split('|')
            if len(parts) not in (3, 5, 6, 7, 8):
                raise ValueError()
            top, focus, state = parts[:3]
            top, focus = int(top), int(focus)
            if state not in ('safe', 'password', 'unknown') or min(top, focus) < 0:
                raise ValueError()
            reason = parts[4] if len(parts) >= 5 else 'checked'
            element_id = parts[5] if len(parts) >= 6 else ''
            addressable = (parts[6] == '1' if len(parts) >= 7 else state == 'safe')
            if len(parts) >= 7 and parts[6] not in ('0', '1'):
                raise ValueError()
            if len(parts) == 8:
                if not parts[7].isascii() or not parts[7].isdecimal() or len(parts[7]) > 19:
                    raise ValueError()
                generation = int(parts[7])
                if generation > (1 << 63) - 1:
                    raise ValueError()
                if (self._generation_fault or self._event_generation is not None
                        and generation < self._event_generation):
                    state, reason, element_id, addressable = ('unknown', 'event-generation-regressed', '', False)
                    self._generation_fault = True
            if self.require_element_id and state == 'safe' and len(parts) < 6:
                state, reason = 'unknown', 'missing-element-id'
                addressable = False
            valid_uia = (element_id.startswith('uia:') and
                         bool(element_id[4:]) and
                         all(item.lstrip('-').isdigit() and item not in ('', '-')
                             for item in element_id[4:].split(',')))
            valid_win32 = (element_id == f'win32:{focus}' and focus > 0)
            if len(parts) >= 6 and state == 'safe' and (
                    not element_id or len(element_id) > 256
                    or not (valid_uia or valid_win32)):
                state, reason, element_id, addressable = 'unknown', 'missing-element-id', '', False
            if len(parts) >= 5:
                started = (int(parts[3]) - 621355968000000000) / 10_000_000
                # A buffered answer started before the click cannot authorize its new field.
                if started <= self.invalidated_at:
                    return
                if len(parts) >= 6 and state == 'safe':
                    # The provider can stall before writing a response. A
                    # recently *received* answer is not necessarily recent
                    # metadata: bound the wall-clock age of the query itself.
                    age = time.time() - started
                    if not -0.1 <= age <= 0.5:
                        state, reason, element_id, addressable = 'unknown', 'stale-helper-response', '', False
                    else:
                        # TTL also starts at query initiation, not at receipt.
                        checked_at = self.clock() - max(0.0, age)
        except (ValueError, TypeError):
            top, focus, state = 0, 0, 'unknown'
            reason = 'protocol-error'
            element_id = ''
            addressable = False
        self.last_response = self.clock()
        self._update(top, focus, state, reason, started=started,
                     element_id=element_id, checked_at=checked_at,
                     addressable=addressable, generation=generation)

    def invalidate(self):
        if self.disabled:
            return
        with self.lock:
            self.invalidated_at = time.time()
            self._update(0, 0, 'unknown', 'focus-change')

    def permit_revision(self, top, focus):
        if self.disabled:
            return 0
        snapshot = self.snapshot
        if self.explicit_password_only:
            if (snapshot.state == 'password'
                    and (snapshot.top, snapshot.focus) == (top, focus)):
                return None
            return snapshot.revision
        now = self.clock()
        if (snapshot.state == 'safe' and snapshot.addressable
                and (snapshot.top, snapshot.focus) == (top, focus)
                and 0 <= now - snapshot.checked <= .5):
            return snapshot.revision
        if snapshot.state == 'safe' and now - snapshot.checked > .5:
            self._update(0, 0, 'unknown', 'stale')
        return None

    def start(self):
        if self.disabled:
            return
        if self.worker is not None:
            return
        self.worker = threading.Thread(target=self._watch, name='password-metadata', daemon=True)
        self.worker.start()

    def revalidate(self, top, focus, revision, *, timeout=.35):
        """Off-hook check using metadata queried after this request.

        This narrows the asynchronous UIA race; it cannot atomically bind a
        SendInput call to a DOM element. No field contents are read.
        """
        if self.disabled:
            return revision == 0
        if self.explicit_password_only:
            return self.permit_revision(top, focus) == revision
        started = self.clock()
        deadline = time.monotonic() + timeout
        with self.refreshed:
            while not self.stopped.is_set():
                permitted = self.permit_revision(top, focus)
                sample = self.snapshot
                if permitted != revision:
                    return False
                # Windows monotonic can have a coarse clock tick: equality
                # is NOT evidence that another provider query has finished.
                if sample.element_id.startswith('win32:') or sample.checked > started:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.refreshed.wait(min(remaining, .05))
        return False

    def _watch(self):
        while not self.stopped.is_set():
            self._watch_once()
            if not self.stopped.is_set():
                self._update(0, 0, 'unknown', 'restarting')
                self.stopped.wait(2)

    def _watch_once(self):
        """Poll metadata through the same isolated COM child as caret reads.

        Neither the hook nor this parent thread calls into a UIA provider.
        Service request timeouts terminate only the external COM worker.
        """
        if self.disabled:
            return
        from uia_native_worker import get_uia_service
        service = get_uia_service()
        # The service is shared with bounded caret/selection reads. A temporary
        # lock conflict is NOT proof the COM child restarted: preserve the
        # last event generation and its original checked timestamp. The 500ms
        # strict context TTL still expires normally while metadata is busy.
        busy_marker = 'metadata-service-busy'
        self.last_response = self.clock()
        while not self.stopped.is_set():
            try:
                sample = service.request({'command': 'metadata'},
                                         valid=lambda: not self.stopped.is_set(),
                                         on_busy=busy_marker)
            except Exception:
                sample = None
            if sample == busy_marker:
                # Do not refresh checked/last_response, infer a new identity,
                # or change the revision. All permission still expires at the
                # exact original snapshot time, never at this busy response.
                pass
            elif not isinstance(sample, str) or len(sample) > 1024:
                # A missing response cannot prove that a known password field
                # stopped being a password; retain positive password evidence.
                # Previously safe fields expire through their checked TTL.
                if self.snapshot.state != 'password':
                    self._update(0, 0, 'unknown', 'helper-unavailable', addressable=False)
                # The watchdog terminated or refused the former COM worker.
                # A replacement process starts a new generation epoch.
                with self.lock:
                    self._event_generation = None
                    self._generation_fault = False
                # Do not poison target identity with a missing provider answer.
            else:
                parts = sample.split('|')
                if len(parts) != 8:
                    # The live native worker MUST provide event generations.
                    # A polling-only / unsubscribed helper is unsupported.
                    self._update(0, 0, 'unknown', 'event-subscription-unavailable',
                                 addressable=False)
                    with self.lock:
                        self._event_generation = None
                        self._generation_fault = False
                else:
                    # _update compares both target identity and event generation
                    # atomically with its revision, including A->B->A.
                    self.ingest(sample)
            self.stopped.wait(.1)

    def _terminate(self):
        # The shared service is owned by its singleton, not the guard; a
        # closing watcher must not kill a concurrent caret request.
        self.process = None

    def close(self):
        if self.disabled:
            self.stopped.set()
            return
        self.stopped.set()
        self._terminate()
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)
        self._update(0, 0, 'unknown', 'stopped')
