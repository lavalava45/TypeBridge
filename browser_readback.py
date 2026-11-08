"""Opt-in browser caret samples. Read-only UIA in a disposable helper process.

No process, window, hook, clipboard or UIA access at import. A single background
worker warms the helper once and serializes bounded requests. Provider hangs kill
only that helper. A sample is deferred evidence, never document verification.
"""
import json
import queue
import threading
from time import monotonic


class ReadbackFailure(Exception):
    pass


class CaretReader:
    """Read-only browser diagnostic through the shared native COM service.

    This is a diagnostics-only feature. Its lifecycle must never terminate the
    shared UIA service used for password gating or manual word correction.
    """

    def warm(self):
        pass  # The native service starts on demand and remains available.

    def read(self, context):
        from uia_native_worker import get_uia_service
        sample = get_uia_service().request(
            {'command': 'caret', 'top': int(context.top),
             'focus': int(context.focus), 'element_id': ''}, timeout=.9)
        if not isinstance(sample, dict):
            return {'state': 'skipped', 'reason': 'uia-caret-unconfirmed'}
        before = sample.get('before')
        if not isinstance(before, str):
            return {'state': 'skipped', 'reason': 'uia-response-invalid'}
        return {'state': 'sampled', 'observed': before[-60:]}

    def close(self):
        pass  # Shared service belongs to TypeBridge, not this diagnostic.

    def cancel(self):
        pass  # Cancellation invalidates the queued job; no global COM kill.


class BrowserReadback:
    """One queue, one worker. Diagnostic failures never touch correction state."""
    def __init__(self, notify, reader_factory=CaretReader, clock=monotonic):
        self.notify, self.reader_factory, self.clock = notify, reader_factory, clock
        self.jobs = queue.Queue(maxsize=4)
        self.closed = threading.Event()
        self.reader = None
        self.retry_after = 0
        self.worker = None

    def start(self):
        self.worker = threading.Thread(target=self._run, name='BrowserReadback', daemon=True)
        self.worker.start()

    def request(self, hook, context, details, revision, created):
        job = (hook, context, dict(details), revision, created)
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            self._deliver(job, {'state': 'skipped', 'reason': 'readback-queue-full'})

    def _valid(self, job):
        hook, context, _, revision, _ = job
        if (self.closed.is_set() or hook.stopping or not hook.debug_words
                or getattr(hook, '_browser_field_revision', 0) != revision):
            return False
        current = hook.context()
        return current is not None and hook._word_scope(current) == hook._word_scope(context)

    def _deliver(self, job, result, elapsed=0):
        hook, context, details, _, created = job
        payload = {**details, 'state': result.get('state', 'failed'),
                   'reason': result.get('reason', ''), 'transport': 'uia-text',
                   'delay_ms': str(max(0, int((self.clock() - created) * 1000))),
                   'read_ms': str(max(0, int(elapsed * 1000)))}
        if result.get('state') == 'sampled':
            payload['observed'] = result['observed'][-60:]
        self.notify((hook, context, payload))

    def _read_job(self, job):
        read_started = None
        result = {'state': 'skipped', 'reason': 'context-changed-before-read'}
        try:
            # Let the held physical callback return and the browser process
            # the submitted input. This wait is on the diagnostic worker only.
            self.closed.wait(max(0, .08 - (self.clock() - job[4])))
            if self.clock() - job[4] > 1.5:
                result = {'state': 'skipped', 'reason': 'readback-request-stale'}
            elif self._valid(job):
                if self.reader is None:
                    if self.clock() < self.retry_after:
                        self._deliver(job, {'state': 'skipped', 'reason': 'helper-restarting'})
                        return
                    self._make_reader()
                if self.clock() - job[4] > 1.5:
                    result = {'state': 'skipped', 'reason': 'readback-request-stale'}
                elif self._valid(job):
                    read_started = self.clock()
                    result = self.reader.read(job[1])
                    if not self._valid(job):
                        result = {'state': 'skipped', 'reason': 'context-changed-during-read'}
        except Exception as error:
            reason = str(error) if isinstance(error, ReadbackFailure) else 'readback-error'
            result = {'state': 'failed', 'reason': reason}
            self._reset_reader()
            self.retry_after = self.clock() + 2
        self._deliver(job, result, 0 if read_started is None else self.clock() - read_started)

    def _reset_reader(self):
        reader, self.reader = self.reader, None
        if reader:
            try:
                reader.close()
            except Exception:
                pass

    def _make_reader(self):
        self.reader = self.reader_factory()
        warm = getattr(self.reader, 'warm', None)
        if warm:
            warm()

    def _run(self):
        try:
            # Warm while dictionaries are loading, not on the first keystroke.
            try:
                self._make_reader()
            except Exception:
                self._reset_reader()
                self.retry_after = self.clock() + 2
            while not self.closed.is_set():
                try:
                    job = self.jobs.get(timeout=.1)
                except queue.Empty:
                    continue
                self._read_job(job)
        finally:
            self._reset_reader()

    def close(self):
        self.closed.set()
        reader = self.reader
        if reader:
            cancel = getattr(reader, 'cancel', None)
            if cancel:
                try:
                    cancel()
                except Exception:
                    pass
        # Do not join or wait on the GUI thread. In-flight reads have a fixed
        # timeout; the daemon worker tears down its helper in finally.
