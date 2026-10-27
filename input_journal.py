"""Opt-in encrypted snapshots of explicitly permitted text editors.

No keyboard hooks, network, document writes or native reads on initialization.
The default disabled state creates no database or editor reader. An existing
database may be pruned in the background even while recording is disabled.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
import threading
import time

MAX_TEXT_BYTES = 262_144
MAX_PAYLOAD_BYTES = 50_000_000
MAX_RECORDS = 20_000
DEFAULT_APPS = ('notepad.exe', 'notepad++.exe')
SNAPSHOT_LIMIT = 65_536
MAX_TIMEOUT_ATTEMPTS = 3
RETRY_SECONDS = 30
MAX_QUARANTINES = 256


def validate_journal(days, apps):
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError('Срок хранения дневника: целое число от 1 до 365 дней')
    if not isinstance(apps, (tuple, list)) or len(apps) > 100:
        raise ValueError('Укажи не более 100 приложений дневника')
    if any(not isinstance(name, str) or not name.endswith('.exe') or
           any(ch in name for ch in '\\/:\r\n') or len(name) > 100 for name in apps):
        raise ValueError('Для дневника нужны имена приложений вроде notepad.exe')
    return tuple(dict.fromkeys(name.casefold() for name in apps))


def encrypt_text(raw):
    import win32crypt
    return win32crypt.CryptProtectData(raw, 'TypeBridge input journal', None, None, None, 1)


def decrypt_text(raw):
    import win32crypt
    return win32crypt.CryptUnprotectData(raw, None, None, None, 1)[1]


class JournalStore:
    def __init__(self, path, *, encrypt=encrypt_text, decrypt=decrypt_text, clock=time.time):
        self.path = Path(path)
        self.encrypt, self.decrypt, self.clock = encrypt, decrypt, clock

    @contextmanager
    def connection(self):
        if not self.path.is_absolute() or any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError('Дневник должен храниться в обычной локальной папке')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=2)
        try:
            conn.execute('PRAGMA auto_vacuum=FULL')
            conn.execute('PRAGMA secure_delete=ON')
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('CREATE TABLE IF NOT EXISTS snapshots '
                         '(id INTEGER PRIMARY KEY, created REAL NOT NULL, app TEXT NOT NULL, payload BLOB NOT NULL)')
            conn.execute('CREATE INDEX IF NOT EXISTS snapshot_time ON snapshots(created)')
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _prune(conn, cutoff):
        conn.execute('DELETE FROM snapshots WHERE created < ?', (cutoff,))
        rows = conn.execute('SELECT id, length(payload) FROM snapshots ORDER BY id DESC').fetchall()
        size = 0
        for index, (identifier, length) in enumerate(rows):
            size += length
            if index >= MAX_RECORDS or size > MAX_PAYLOAD_BYTES:
                conn.execute('DELETE FROM snapshots WHERE id <= ?', (identifier,))
                break

    def exists(self):
        return self.path.is_file()

    def append(self, app, text, days, *, commit_guard=None):
        raw = text.encode('utf-8')
        if not raw or len(raw) > MAX_TEXT_BYTES or '\0' in text:
            return False
        now = self.clock()
        payload = self.encrypt(raw)  # Encryption must succeed before opening a database.
        with self.connection() as conn:
            conn.execute('INSERT INTO snapshots(created,app,payload) VALUES(?,?,?)', (now, app, payload))
            self._prune(conn, now - days * 86400)
            if commit_guard is not None:
                # Encryption, INSERT, pruning and SQLite's writer-lock wait
                # happen outside the service/configuration lock. Only final
                # publication is serialized with disabling/config changes.
                with commit_guard() as permitted:
                    if not permitted:
                        conn.rollback()
                        return False
                    conn.execute('PRAGMA busy_timeout=0')
                    conn.commit()  # No SQLite lock-wait under the config lock.
        return True

    def cleanup(self, days):
        if self.path.exists():
            with self.connection() as conn:
                self._prune(conn, self.clock() - days * 86400)

    def list(self, limit=200, offset=0):
        if not self.path.exists():
            return []
        with self.connection() as conn:
            return conn.execute('SELECT id,created,app FROM snapshots ORDER BY id DESC LIMIT ? OFFSET ?',
                                (min(MAX_RECORDS, max(1, int(limit))), max(0, int(offset)))).fetchall()

    def text(self, identifier):
        if not self.path.exists():
            return ''
        with self.connection() as conn:
            row = conn.execute('SELECT payload FROM snapshots WHERE id=?', (identifier,)).fetchone()
        return self.decrypt(row[0]).decode('utf-8') if row else ''

    def clear(self):
        if self.path.exists():
            with self.connection() as conn:
                conn.execute('DELETE FROM snapshots')


@dataclass(frozen=True)
class Snapshot:
    scope: tuple
    app: str
    text: str


class NativeJournalReader:
    """Read only known plain-text controls; unknown/password fields are refused."""
    def __init__(self, *, clock=time.monotonic):
        from tray_layouts import WindowsLayouts
        self.layouts = WindowsLayouts()
        self.clock = clock
        self.blocked = {}
        self.status = 'waiting'
        self.retry_after = 0

    def _timeout(self, scope):
        attempts = self.blocked.get(scope, (0, 0))[0] + 1
        self.blocked[scope] = (attempts, self.clock() + RETRY_SECONDS * 2 ** (attempts - 1))
        self.status = 'timeout-exhausted' if attempts >= MAX_TIMEOUT_ATTEMPTS else 'timeout'
        self.retry_after = RETRY_SECONDS * 2 ** (attempts - 1)

    def read(self, settings):
        self.status, self.retry_after = 'waiting', 0
        try:
            return self._read(settings)
        except ValueError as error:
            self.status = ('too-large' if str(error) == 'journal-document-limit'
                           else 'unsupported-state')
        except Exception:
            self.status = 'read-failed'
        return None

    def _read(self, settings):
        import ctypes
        from types import SimpleNamespace
        import win32gui
        import psutil
        from app_exclusions import target_allowed
        from early_scintilla import ScintillaEarlyBridge
        target = self.layouts.capture()
        if not target or not target_allowed(target, settings):
            self.status = 'excluded'
            return None
        process = psutil.Process(target.pid)
        name = process.name().casefold()
        scope = (target.pid, target.top, target.focus)
        if name not in settings.journal_apps:
            self.status = 'app-not-allowed'
            return None
        cls = win32gui.GetClassName(target.focus).casefold()
        if win32gui.GetWindowLong(target.focus, -16) & 0x20:
            self.status = 'password'
            return None
        scintilla = name == 'notepad++.exe' and cls == 'scintilla'
        if not scintilla and cls not in ('edit', 'richedit20w', 'richedit50w', 'richeditd2dpt', 'richeditd2d'):
            self.status = 'unsupported'
            return None
        # Do not confuse a reused PID/HWND with a previously quarantined editor.
        identity = (*scope, process.create_time())
        def message(command, wp=0, lp=0):
            result = ctypes.c_size_t()
            if not self.layouts.u.SendMessageTimeoutW(target.focus, command, wp, lp, 3, 100, ctypes.byref(result)):
                raise TimeoutError('editor-unavailable')
            return result.value
        try:
            if identity in self.blocked:
                attempts, deadline = self.blocked[identity]
                if attempts >= MAX_TIMEOUT_ATTEMPTS:
                    self.status = 'timeout-exhausted'
                    return None
                if self.clock() < deadline:
                    self.status = 'timeout'
                    self.retry_after = max(1, int(deadline - self.clock() + 0.999))
                    return None
                # WM_NULL carries no buffers. A hung editor must answer this
                # before any new text/remote allocation is attempted.
                message(0)
                if self.layouts.capture() != target or not self.layouts.valid(target):
                    self.status = 'changed'
                    return None
            elif len(self.blocked) >= MAX_QUARANTINES:
                self.status = 'quarantine-limit'
                return None
            if scintilla:
                context = SimpleNamespace(process_name=name, top=target.top, focus=target.focus)
                with ScintillaEarlyBridge(context) as bridge:
                    if bridge.msg(2006) > SNAPSHOT_LIMIT:
                        raise ValueError('journal-document-limit')
                    raw, caret = bridge.snapshot()  # Read only, no prepare/replace.
                text = raw.decode('utf-8')
            else:
                length = message(0x000E)
                if not 0 <= length < SNAPSHOT_LIMIT:
                    self.status = 'too-large'
                    return None
                buf = ctypes.create_unicode_buffer(length + 1)
                copied = message(0x000D, length + 1, ctypes.addressof(buf))
                if copied != length or message(0x000E) != length:
                    self.status = 'changed'
                    return None
                text = buf.value
        except TimeoutError:
            # At most three timed-out attempts per process/control lifetime.
            # A Scintilla timeout may retain a remote buffer until editor exit;
            # never allocate more buffers while its thread fails the probe.
            self._timeout(identity)
            return None
        if self.layouts.capture() != target or not self.layouts.valid(target):
            self.status = 'changed'
            return None
        if len(text.encode('utf-8')) > SNAPSHOT_LIMIT:
            self.status = 'too-large'
            return None
        self.status = 'ready'
        return Snapshot(scope, name, text)


class JournalService:
    def __init__(self, store, notify, *, reader_factory=NativeJournalReader, clock=time.time):
        self.store, self.notify, self.reader_factory, self.clock = store, notify, reader_factory, clock
        self.settings = None
        self.generation = 0
        self.closed = False
        self.worker = None
        self.reader = None
        self.lock = threading.RLock()
        self.poll_lock = threading.Lock()
        self.wake = threading.Event()
        self.last = None
        self.last_cleanup = None
        self.failed = False
        self.maintenance_error = False
        self.state = None
        self.capture_requested = threading.Event()
        self.stop_requested = threading.Event()

    @property
    def enabled(self):
        return bool(not self.closed and not self.stop_requested.is_set() and not self.failed
                    and self.capture_requested.is_set() and self.settings and self.settings.journal_enabled)

    def request_capture(self, enabled):
        (self.capture_requested.set if enabled else self.capture_requested.clear)()

    def request_stop(self):
        self.stop_requested.set()
        self.request_capture(False)
        self.wake.set()

    def configure(self, settings, *, update_request=True):
        validate_journal(settings.journal_days, settings.journal_apps)
        if update_request:
            self.request_capture(settings.journal_enabled)
        # A disabled installation without a database stays fully dormant.
        needs_worker = settings.journal_enabled or self.store.exists()
        with self.lock:
            if self.closed or self.stop_requested.is_set():
                return
            self.settings = settings
            self.generation += 1
            self.failed = False
            self.last_cleanup = None
            self.state = None
            if not settings.journal_enabled:
                self.last = None
                self.reader = None
            if needs_worker and self.worker is None:
                self.worker = threading.Thread(target=self._run, name='TypeBridgeJournal', daemon=True)
                self.worker.start()
        self.wake.set()

    @contextmanager
    def _commit_guard(self, generation):
        # Only final commit is serialized; SQL lock waits/encryption/pruning
        # must never hold this configuration lock. A cancelled snapshot rolls
        # back without being published after disabling has returned.
        with self.lock:
            yield self.enabled and self.generation == generation

    def _maintain(self, settings, generation):
        with self.lock:
            if self.closed or generation != self.generation:
                return
            now = self.clock()
            if self.last_cleanup is not None and now - self.last_cleanup < 3600:
                return
        try:
            self.store.cleanup(settings.journal_days)
        except Exception:
            with self.lock:
                if not self.closed and generation == self.generation:
                    # Retry maintenance with backoff, without stopping captures.
                    self.last_cleanup = now - 3300
                    self.maintenance_error = True
                    self._status('Ошибка автоочистки дневника: повтор через 5 минут')
            return
        with self.lock:
            if not self.closed and generation == self.generation:
                self.last_cleanup = now
                self.maintenance_error = False

    def poll(self):
        # Serialize storage work with manual clear, without blocking configure.
        with self.poll_lock:
            self._poll()

    def _poll(self):
        with self.lock:
            if self.closed or self.stop_requested.is_set() or self.settings is None:
                return
            settings, generation = self.settings, self.generation
            enabled = self.enabled
            reader = self.reader
        if not enabled:
            self._maintain(settings, generation)
            return
        if reader is None:
            reader = self.reader_factory()
            with self.lock:
                if not self.enabled or generation != self.generation:
                    return
                self.reader = reader
        snapshot = reader.read(settings)
        with self.lock:
            if not self.enabled or generation != self.generation:
                return
        self._maintain(settings, generation)
        with self.lock:
            if not self.enabled or generation != self.generation:
                return
            if snapshot is None or not snapshot.text:
                self._status(self._reader_status(reader))
                return
            key = (snapshot.scope, hashlib.sha256(snapshot.text.encode('utf-8')).digest())
            if key == self.last:
                return
        saved = self.store.append(snapshot.app, snapshot.text, settings.journal_days,
                                  commit_guard=lambda: self._commit_guard(generation))
        with self.lock:
            if saved and self.enabled and generation == self.generation:
                self.last = key
                now = self.clock()
                self._status('Дневник включён: снимок сохранён ' + time.strftime('%H:%M:%S', time.localtime(now)))

    @staticmethod
    def _reader_status(reader):
        status = getattr(reader, 'status', 'waiting')
        messages = {
            'too-large': 'Дневник: документ больше 64 КБ UTF-8; снимок пропущен',
            'timeout-exhausted': 'Дневник: чтение поля остановлено после 3 таймаутов; выключи и включи дневник для повтора',
            'quarantine-limit': 'Дневник: достигнут предел карантина; выключи и включи дневник для повтора',
            'unsupported': 'Дневник: этот тип поля не поддерживается',
            'unsupported-state': 'Дневник: выделение или состояние редактора не поддерживается',
            'password': 'Дневник: парольное поле пропущено',
            'app-not-allowed': 'Дневник: приложение не входит в разрешённый список',
            'excluded': 'Дневник: нет разрешённого активного поля',
            'changed': 'Дневник: поле или документ изменились во время чтения; снимок пропущен',
            'read-failed': 'Дневник: не удалось прочитать поле; следующий опрос через 2 секунды',
        }
        if status == 'timeout':
            seconds = getattr(reader, 'retry_after', RETRY_SECONDS)
            return f'Дневник: таймаут чтения; проверка ответа редактора через {seconds} с'
        return messages.get(status, 'Дневник включён: ожидание доступного текстового поля')

    def _status(self, value):
        if self.maintenance_error and not value.startswith('Ошибка автоочистки'):
            value += ' · Автоочистка не выполнена; повтор через 5 минут'
        if value != self.state:
            self.state = value
            self.notify((self.generation, value))

    def _run(self):
        while not self.closed:
            try:
                self.poll()
            except Exception as error:
                with self.lock:
                    if self.settings and self.settings.journal_enabled and not self.closed:
                        code = getattr(error, 'sqlite_errorcode', 0) & 0xFF
                        if isinstance(error, sqlite3.OperationalError) and code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                            self._status('Дневник: база занята; повтор записи через 2 секунды')
                        else:
                            self.failed = True
                            self.reader = None
                            self.notify((self.generation, 'Ошибка дневника: запись остановлена. Проверь доступ к папке и настройки.'))
            self.wake.wait(2 if self.enabled else 60)
            self.wake.clear()

    def clear(self):
        with self.poll_lock:
            self.store.clear()
            # Keep the last hash, so unchanged text isn't immediately re-added.

    def close(self):
        self.request_stop()
        with self.lock:
            self.closed = True
            self.generation += 1
            self.reader = None
            self.last = None
        self.wake.set()
