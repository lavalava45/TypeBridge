"""Bounded asynchronous diagnostic persistence. No Tk, hook or native input.

Only the GUI's optional log copy is dropped on overflow; application control
events remain in the independent GUI queue. One worker batches file writes.
"""
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import queue
import threading

from diagnostic_identity import computer_id
from dictionary_diagnostics import debug_message


class DiagnosticWriter:
    def __init__(self, folder, *, debug=False, words=False, diagnostics=False,
                 capacity=2048, batch_size=256):
        self.folder = Path(folder)
        self.debug, self.words, self.diagnostics = debug, words and debug and diagnostics, diagnostics
        self.jobs = queue.Queue(maxsize=max(1, capacity))
        self.batch_size = max(1, min(256, batch_size))
        self.lock = threading.Lock()
        self.closing = False
        self.dropped = 0
        self.error = ''
        self.worker = None
        self.finished = threading.Event()
        self.finished.set()

    def submit(self, kind, payload):
        if (kind == 'word' and not self.words
                or kind in ('debug', 'comparison') and not self.debug
                or kind == 'comparison' and not self.diagnostics
                or kind not in ('word', 'debug', 'runtime', 'comparison')):
            return False
        # Bound retained data, including malformed future callers. Actual word
        # fields are validated again by safe_word_event on the worker.
        if kind == 'word':
            if not isinstance(payload, dict) or len(payload) > 24:
                return False
            payload = {k: v[:256] for k, v in payload.items()
                       if isinstance(k, str) and isinstance(v, str) and len(k) <= 32}
            if len(payload) > 24:
                return False
        elif kind == 'debug':
            level, text = payload
            if not isinstance(text, str):
                return False
            payload = (level, text[:4096])
        elif kind == 'runtime':
            if not isinstance(payload, str):
                return False
            payload = payload[:4096]
        with self.lock:
            if self.closing:
                return False
            try:
                self.jobs.put_nowait((kind, payload))
            except queue.Full:
                self.dropped += 1
                return False
            if self.worker is None:
                self.finished.clear()
                self.worker = threading.Thread(target=self._run,
                                               name='DiagnosticWriter', daemon=True)
                try:
                    self.worker.start()
                except RuntimeError as error:
                    self.error = type(error).__name__
                    self.closing = True
                    self.finished.set()
                    return False
        return True

    def close(self):
        # Non-blocking: the GUI polls finished while continuing its event loop.
        with self.lock:
            self.closing = True

    def _encode(self, kind, payload):
        if kind == 'word':
            from word_trace import safe_word_event
            text = safe_word_event(payload)
            return ('debug-words.log', 'word-trace:' + text) if text is not None else None
        if kind == 'comparison':
            from comparison_diagnostics import context_report
            payload = ('diagnostic', context_report(payload))
            kind = 'debug'
        if kind == 'debug':
            text = debug_message(*payload)
            return ('debug.log', text) if text is not None else None
        return 'replacement-diagnostics.log', payload

    def _write_batch(self, batch):
        grouped = defaultdict(list)
        stamp = datetime.now(timezone.utc).isoformat()
        prefix = f'{stamp} [pc={computer_id()}] '
        for kind, payload in batch:
            try:
                item = self._encode(kind, payload)
                if item is not None:
                    filename, text = item
                    grouped[filename].append(prefix + text + '\n')
            except Exception as error:
                self.error = type(error).__name__
        with self.lock:
            dropped, self.dropped = self.dropped, 0
        if dropped:
            grouped['replacement-diagnostics.log'].append(
                prefix + f'diagnostic-queue-dropped:{dropped}\n')
        if not grouped:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        for filename, lines in grouped.items():
            path = self.folder / filename
            limit = 65_536 if filename == 'replacement-diagnostics.log' else 1_048_576
            if path.exists() and path.stat().st_size > limit:
                if filename == 'replacement-diagnostics.log':
                    path.write_text('', encoding='utf-8')
                else:
                    path.replace(path.with_suffix('.previous.log'))
            with path.open('a', encoding='utf-8', errors='backslashreplace') as stream:
                stream.writelines(lines)

    def _run(self):
        try:
            while True:
                try:
                    first = self.jobs.get(timeout=.1)
                except queue.Empty:
                    with self.lock:
                        if self.closing:
                            break
                    continue
                batch = [first]
                for _ in range(self.batch_size - 1):
                    try:
                        batch.append(self.jobs.get_nowait())
                    except queue.Empty:
                        break
                try:
                    self._write_batch(batch)
                except Exception as error:
                    self.error = type(error).__name__
        finally:
            self.finished.set()
