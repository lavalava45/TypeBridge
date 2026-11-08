"""Debug-only, metadata-only traces for diagnosing real keyboard delivery.

Never records physical keys, word contents, window titles or document text.
Called on the hook thread: formatting and queueing only, no file writes.
"""

import re
import queue
import threading
from time import monotonic


EVENTS = frozenset({
    'hook-installed', 'hook-keydown', 'context-rejected', 'context-ready',
    'token-start', 'token-reset', 'token-blocked', 'delimiter',
    'decision-none', 'decision-found', 'transport', 'layout-result',
    'early-found', 'settings', 'input-filtered', 'watchdog', 'callback-error',
    'manual-cycle', 'transport-quarantine', 'input-barrier-aborted',
    'dictionary-diagnostic',
})
FIELDS = frozenset({'reason', 'app', 'language', 'target', 'hkl', 'observed',
                    'length', 'separator', 'blocked', 'paused', 'early',
                    'rare', 'manual', 'result', 'method', 'enabled', 'native', 'words',
                    'thread', 'keyboard', 'mouse', 'alive', 'ping', 'pongs',
                    'callbacks', 'keydowns', 'mice', 'errors', 'wininput'})
FIELDS = FIELDS | frozenset({'raw', 'raw_events', 'raw_errors', 'raw_error',
                             'raw_alive', 'slow', 'max_ms', 'raw_delta', 'll_delta'})
FIELDS = FIELDS | frozenset({'mouse_max_us', 'mouse_slow'})
FIELDS = FIELDS | frozenset({'elapsed_ms', 'scoped', 'pending', 'uncertain',
                             'events'})
FIELDS = FIELDS | frozenset({
    'raw_wm', 'raw_pump', 'raw_pump_input', 'raw_reg', 'raw_reg_error',
    'raw_flags', 'control', 'control_error', 'control_alive',
    'control_callbacks', 'control_delta', 'control_keydowns',
    'control_ping', 'control_pongs',
})
SAFE_VALUE = re.compile(r'^[a-zA-Z0-9_.+\-]{1,64}$')


def emit_pipeline(hook, event, *, interval=0, **fields):
    """Emit a validated event; repeated context/heartbeat probes are throttled."""
    if not getattr(hook, 'pipeline_debug', False) or event not in EVENTS:
        return
    now = monotonic()
    if interval:
        previous = getattr(hook, '_pipeline_last', None)
        if previous is None:
            previous = {}
            hook._pipeline_last = previous
        # The reason is deliberately part of the key: report distinct failures.
        key = (event, fields.get('reason', ''))
        if now - previous.get(key, -1e30) < interval:
            return
        previous[key] = now
    safe = []
    for name, value in fields.items():
        if name not in FIELDS:
            continue
        if isinstance(value, bool):
            value = 'yes' if value else 'no'
        else:
            value = str(value)
        if not SAFE_VALUE.fullmatch(value):
            value = 'other'
        safe.append(f'{name}={value}')
    hook.emit('probe', 'pipeline:' + event + (':' + ':'.join(safe) if safe else ''))


def decision_failure(engine, keys, current, *, allow_rare=False):
    """Conservative diagnostic classification. Does not influence a decision."""
    if not keys:
        return 'empty-token'
    if current not in engine.words or len(keys) > 40:
        return 'unsupported-context'
    typed = engine.mapper.word(keys, current)
    if len(typed) != len(keys):
        return 'source-mapping-incomplete'
    if engine._excluded(current, typed):
        return 'source-excluded'
    if not allow_rare and engine._known(current, typed):
        return 'already-valid-source-word'
    matches = []
    for lang in engine.words:
        if lang == current:
            continue
        word = engine.mapper.word(keys, lang)
        if len(word) == len(keys) and engine._known(lang, word):
            matches.append((lang, word))
    if not matches:
        return 'no-target-dictionary-match'
    if len(matches) > 1:
        return 'multiple-targets-policy'
    lang, word = matches[0]
    if engine._excluded(lang, word):
        return 'target-excluded'
    if engine.conservative and not allow_rare and len(keys) > 1 and not engine._confident(lang, word):
        return 'rare-word-policy'
    return 'mapping-or-punctuation-policy'


# Explicitly bounded vocabulary: even a faulty diagnostic classifier must
# never turn a typed word, physical key sequence, or exception into a log.
DICTIONARY_REASONS = frozenset({
    'empty-token', 'unsupported-context', 'source-mapping-incomplete',
    'source-excluded', 'already-valid-source-word',
    'no-target-dictionary-match', 'multiple-targets-policy',
    'target-excluded', 'rare-word-policy',
    'mapping-or-punctuation-policy', 'diagnostic-classifier-failed',
    'diagnostic-classifier-unexpected',
})


class DeferredDictionaryDiagnostics:
    """Opt-in, bounded decision explanations strictly outside the LL hook.

    Caller submits at most 40 physical key descriptors without waiting.
    Neither the queued keys nor any mapped words are persisted or emitted.
    Classification is best-effort: live dictionary/configuration changes can
    make a late explanation differ from the earlier production decision.
    """

    def __init__(self, engine, on_result, *, capacity=32):
        self.engine = engine
        self.on_result = on_result
        self.jobs = queue.Queue(maxsize=max(1, min(64, capacity)))
        self.closed = False
        self.worker = threading.Thread(target=self._run,
                                       name='TypeBridgeDictionaryDiagnostic', daemon=True)
        self.worker.start()  # Caller constructs this BEFORE hook installation.

    def submit(self, keys, language, *, allow_rare=False):
        if self.closed or not isinstance(language, str):
            return False
        # Keep the in-memory diagnostic snapshot finite. It is never a source
        # of authority for text mutation or automatic replay.
        sample = tuple(keys)
        if (not 1 <= len(sample) <= 40
                or any(not isinstance(item, tuple) or len(item) != 2
                       or type(item[0]) is not int or not 0 <= item[0] <= 255
                       or type(item[1]) is not bool for item in sample)):
            return False
        try:
            self.jobs.put_nowait((sample, language, bool(allow_rare)))
            return True
        except queue.Full:
            return False  # Never wait for diagnostics in the keyboard hook.

    def _run(self):
        while True:
            job = self.jobs.get()
            try:
                if job is None:
                    return
                keys, language, allow_rare = job
                try:
                    reason = decision_failure(self.engine, keys, language,
                                              allow_rare=allow_rare)
                except Exception:
                    reason = 'diagnostic-classifier-failed'
                if reason not in DICTIONARY_REASONS:
                    reason = 'diagnostic-classifier-unexpected'
                try:
                    self.on_result(reason, language, len(keys), allow_rare)
                except Exception:
                    pass  # The background probe cannot affect input delivery.
            finally:
                self.jobs.task_done()

    def close(self):
        self.closed = True
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            # Drop one pending *diagnostic* job to admit shutdown. No key is
            # ever blocked, replayed or mutated as a consequence.
            try:
                self.jobs.get_nowait()
                self.jobs.task_done()
            except queue.Empty:
                pass
            try:
                self.jobs.put_nowait(None)
            except queue.Full:
                pass
