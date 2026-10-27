"""One bounded journal command worker; GUI receives completion messages only."""
import queue
import threading


class JournalIo:
    def __init__(self, service, notify, capacity=64):
        self.service, self.notify = service, notify
        self.jobs = queue.Queue(maxsize=capacity)
        self.lock = threading.Lock()
        self.callbacks = {}
        self.serial = 0
        self.pending_config = None
        self.worker = None
        self.closing = False
        self.finished = threading.Event()
        self.finished.set()

    def submit(self, kind, args, callback):
        with self.lock:
            # Even a saturated viewer queue must not prevent cancellation.
            # Confirmation still requires successful configure completion.
            if kind == 'configure' and not args.journal_enabled:
                self.service.request_capture(False)
            if self.closing:
                raise OSError('Дневник завершает работу')
            if self.jobs.full():
                raise OSError('Очередь дневника занята; повтори команду позже')
            self.serial += 1
            identifier = self.serial
            if kind == 'configure':
                self.service.request_capture(args.journal_enabled)
                if self.pending_config is not None:
                    self.callbacks.pop(self.pending_config, None)
                self.pending_config = identifier
            self.callbacks[identifier] = callback
            self.jobs.put_nowait((identifier, kind, args))
            if self.worker is None:
                self.finished.clear()
                self.worker = threading.Thread(target=self._run, name='JournalIo', daemon=True)
                try:
                    self.worker.start()
                except RuntimeError:
                    self.callbacks.clear()
                    self.service.request_stop()
                    self.finished.set()
                    self.closing = True
                    raise OSError('Не удалось запустить очередь дневника')
            return identifier

    def cancel(self, identifier):
        with self.lock:
            self.callbacks.pop(identifier, None)

    def receive(self, message):
        identifier, kind, result, error = message
        with self.lock:
            callback = self.callbacks.pop(identifier, None)
            if self.closing:
                return
            if kind == 'configure':
                if identifier != self.pending_config:
                    return
                self.pending_config = None
        if callback is not None:
            callback(result, error)

    def close(self):
        self.service.request_stop()  # Non-blocking capture cancellation.
        with self.lock:
            self.closing = True
            self.callbacks.clear()

    def _run(self):
        try:
            while True:
                with self.lock:
                    if self.closing:
                        break
                try:
                    identifier, kind, args = self.jobs.get(timeout=.1)
                except queue.Empty:
                    continue
                with self.lock:
                    if self.closing:
                        break
                    if identifier not in self.callbacks:
                        continue
                result, error = None, None
                try:
                    if kind == 'configure':
                        self.service.configure(args, update_request=False)
                        result = {'enabled': self.service.enabled, 'state': self.service.state}
                    elif kind == 'list':
                        result = self.service.store.list(*args)
                    elif kind == 'text':
                        result = self.service.store.text(args)
                    elif kind == 'clear':
                        self.service.clear()
                    else:
                        raise ValueError('journal-command')
                except Exception as failure:
                    error = type(failure).__name__  # Never disclose decrypted text.
                self.notify((identifier, kind, result, error))
        finally:
            try:
                self.service.close()  # Commit waits happen here, never on the GUI.
            finally:
                self.finished.set()
