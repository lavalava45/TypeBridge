"""Cancellable caret provider process. IPC carries geometry only, never text."""
from dataclasses import asdict
import json
import multiprocessing
from pathlib import Path
import sys
import time


def run_caret_worker(connection, synthetic=False, languages=None):
    initialized = False
    try:
        if not synthetic:
            import pythoncom
            from caret_indicator import CaretReader
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            initialized = True
            reader = CaretReader(languages=languages)
        connection.send_bytes(b'ready')
        while connection.recv_bytes(32) == b'snapshot':
            try:
                if synthetic:
                    # Synthetic IPC exercise for an external-only language
                    # set; the default legacy smoke test still reports EN.
                    synthetic_language = ('en' if languages is None or 'en' in languages
                                          else next(iter(languages), 'idle'))
                    sample = {'top': 1, 'focus': 2, 'rect': [1, 1, 2, 16],
                              'language': synthetic_language,
                              'work': [0, 0, 100, 100], 'dpi': 96}
                else:
                    value = reader.snapshot()
                    sample = asdict(value) if value is not None else None
                    if sample is not None:
                        sample.pop('sampled_at', None)
                response = {'sample': sample}
            except Exception as error:
                response = {'error': type(error).__name__}
            connection.send_bytes(json.dumps(response).encode('utf-8'))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()
        if initialized:
            pythoncom.CoUninitialize()


class CaretProcess:
    def __init__(self, *, synthetic=False, clock=time.monotonic, context=None,
                 languages=None):
        self.context = context or multiprocessing.get_context('spawn')
        self.synthetic, self.clock = synthetic, clock
        # The subprocess only needs code/LANGID metadata, not the full
        # dictionary engine (or its costly package imports).
        if languages is None:
            from language_registry import LANGUAGE_REGISTRY
            self.languages = {item.code: item.langid for item in LANGUAGE_REGISTRY}
        else:
            self.languages = dict(languages)
        self.process = self.connection = None
        self.retry_at = 0
        self.failures = 0
        self.scope = None
        self.last_error = ''
        self.disabled = False
        self.diagnostic = ''

    def reset_failures(self):
        """Explicit user retry, called only by the reader thread."""
        self.retry_at = self.failures = 0
        self.disabled = False
        self.last_error = self.diagnostic = ''

    def _runtime_health(self):
        # A onefile child reuses its parent's extracted runtime. Detect missing
        # startup files before the bootloader can open an error dialog.
        if not getattr(sys, 'frozen', False):
            return 'source'
        home = Path(sys._MEIPASS)
        missing = [name for name in ('base_library.zip', 'python312.dll')
                   if not (home / name).is_file()]
        return 'missing=' + ','.join(missing) if missing else 'present'

    def _receive(self, timeout, cancelled):
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            if cancelled():
                raise InterruptedError('cancelled')
            if self.connection.poll(min(.05, max(0, deadline - self.clock()))):
                return self.connection.recv_bytes(4096)
            if not self.process.is_alive():
                raise EOFError('caret-helper-exited')
        raise TimeoutError('caret-helper-timeout')

    def _start(self, cancelled):
        parent, child = self.context.Pipe(duplex=True)
        self.connection = parent
        try:
            self.process = self.context.Process(target=run_caret_worker,
                                                args=(child, self.synthetic, self.languages), daemon=True)
            self.process.start()
        finally:
            child.close()
        if self._receive(5, cancelled) != b'ready':
            raise ValueError('caret-helper-startup')

    def snapshot(self, *, scope=None, cancelled=lambda: False,
                 lifecycle_cancelled=None):
        # Focus changes invalidate samples, not the process or its retry budget.
        # Finish/drain an outstanding response before reusing the connection.
        self.scope = scope
        if self.disabled or self.clock() < self.retry_at or cancelled():
            return None
        lifecycle_cancelled = lifecycle_cancelled or cancelled
        phase, runtime = 'receive', 'unchecked'
        try:
            if self.process is not None and self.connection is None:
                self.cancel()
                if self.process is not None:
                    raise OSError('caret-helper-still-stopping')
            if self.process is None:
                phase = 'startup'
                runtime = self._runtime_health()
                if runtime.startswith('missing='):
                    raise OSError('caret-runtime-missing')
                self._start(lifecycle_cancelled)
            if cancelled():
                return None
            phase = 'receive'
            self.connection.send_bytes(b'snapshot')
            response = self._receive(.3, lifecycle_cancelled)
            if cancelled():
                return None
            result = json.loads(response)
            if not isinstance(result, dict) or set(result) not in ({'sample'}, {'error'}):
                raise ValueError('caret-helper-format')
            if 'error' in result:
                self.last_error = 'reader-error'
                return None
            sample = result.get('sample')
            if sample is None:
                self.last_error, self.failures = '', 0
                return None
            if (not isinstance(sample, dict)
                    or set(sample) != {'top', 'focus', 'rect', 'language', 'work', 'dpi'}
                    or any(type(sample[k]) is not int or sample[k] <= 0 for k in ('top', 'focus', 'dpi'))
                    or sample['language'] not in self.languages):
                raise ValueError('caret-helper-format')
            for key in ('rect', 'work'):
                if (not isinstance(sample[key], list) or len(sample[key]) != 4
                        or any(type(v) is not int or abs(v) > 1_000_000 for v in sample[key])):
                    raise ValueError('caret-helper-format')
                sample[key] = tuple(sample[key])
            from caret_indicator import CaretSample
            self.last_error, self.failures = '', 0
            return CaretSample(**sample, sampled_at=self.clock())
        except InterruptedError:
            self.cancel()
            return None
        except Exception as error:
            self.last_error = type(error).__name__
            runtime = self._runtime_health()
            exitcode = getattr(self.process, 'exitcode', None)
            if type(exitcode) is not int:
                exitcode = None
            self.cancel()
            self.failures += 1
            self.disabled = self.failures >= 3 or runtime.startswith('missing=')
            self.diagnostic = (f'{self.last_error}; phase={phase}; exit={exitcode}; '
                               f'runtime={runtime}; attempts={self.failures}; '
                               f'retries-disabled={self.disabled}')
            self.retry_at = self.clock() + min(10, 2 ** min(self.failures, 4))
            return None

    def cancel(self):
        process = self.process
        connection, self.connection = self.connection, None
        if connection is not None:
            connection.close()
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
                process.join(.2)
                if process.is_alive():
                    process.kill()
                    process.join(.2)
                if not process.is_alive():
                    process.close()
                    self.process = None
            except (OSError, ValueError, AssertionError):
                # A failed start has no child to retain. A live child keeps its
                # ownership slot, so failure to terminate cannot multiply it.
                if process.pid is None:
                    self.process = None

    close = cancel


def smoke_test():
    """Frozen-spawn check with synthetic geometry; never touches desktop APIs."""
    reader = CaretProcess(synthetic=True)
    try:
        sample = reader.snapshot(scope='synthetic')
        if sample is None or sample.top != 1 or sample.language != 'en':
            return False
        pid = reader.process.pid
        second = reader.snapshot(scope='another-synthetic-window')
        return second is not None and reader.process.pid == pid
    finally:
        reader.close()
