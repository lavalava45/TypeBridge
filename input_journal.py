"""Opt-in encrypted snapshots of explicitly permitted text editors.

No keyboard hooks, network, document writes or native reads on initialization.
The default disabled state creates neither a worker nor a database.
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

    def append(self, app, text, days):
        raw = text.encode('utf-8')
        if not raw or len(raw) > MAX_TEXT_BYTES or '\0' in text:
            return False
        now = self.clock()
        payload = self.encrypt(raw)  # Encryption must succeed before opening a database.
        with self.connection() as conn:
            conn.execute('INSERT INTO snapshots(created,app,payload) VALUES(?,?,?)', (now, app, payload))
            self._prune(conn, now - days * 86400)
        return True

    def cleanup(self, days):
        if self.path.exists():
            with self.connection() as conn:
                self._prune(conn, self.clock() - days * 86400)

    def list(self, limit=200):
        if not self.path.exists():
            return []
        with self.connection() as conn:
            return conn.execute('SELECT id,created,app FROM snapshots ORDER BY id DESC LIMIT ?',
                                (min(MAX_RECORDS, max(1, int(limit))),)).fetchall()

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
    def __init__(self):
        from tray_layouts import WindowsLayouts
        self.layouts = WindowsLayouts()
        self.blocked = set()

    def read(self, settings):
        try:
            return self._read(settings)
        except Exception:
            # Closing windows, selections and unsupported editor states are
            # refusals, not reasons to read other fields or stop the service.
            return None

    def _read(self, settings):
        import ctypes
        from ctypes import wintypes as w
        from types import SimpleNamespace
        import win32gui
        import psutil
        from app_exclusions import target_allowed
        from early_scintilla import ScintillaEarlyBridge
        target = self.layouts.capture()
        if not target or not target_allowed(target, settings):
            return None
        name = psutil.Process(target.pid).name().casefold()
        scope = (target.pid, target.top, target.focus)
        if name not in settings.journal_apps or scope in self.blocked:
            return None
        cls = win32gui.GetClassName(target.focus).casefold()
        if win32gui.GetWindowLong(target.focus, -16) & 0x20:  # ES_PASSWORD
            return None
        try:
            if name == 'notepad++.exe' and cls == 'scintilla':
                context = SimpleNamespace(process_name=name, top=target.top, focus=target.focus)
                with ScintillaEarlyBridge(context) as bridge:
                    raw, caret = bridge.snapshot()  # Never call prepare()/replace().
                text = raw.decode('utf-8')
            elif cls in ('edit', 'richedit20w', 'richedit50w', 'richeditd2dpt', 'richeditd2d'):
                api = self.layouts.u
                def message(command, wp=0, lp=0):
                    result = ctypes.c_size_t()
                    if not api.SendMessageTimeoutW(target.focus, command, wp, lp, 3, 100, ctypes.byref(result)):
                        raise TimeoutError('editor-unavailable')
                    return result.value
                length = message(0x000E)  # WM_GETTEXTLENGTH
                if not 0 <= length < 65_536:
                    return None
                buf = ctypes.create_unicode_buffer(length + 1)
                copied = message(0x000D, length + 1, ctypes.addressof(buf))
                if copied != length or message(0x000E) != length:
                    return None
                text = buf.value
            else:
                return None
        except TimeoutError:
            # Do not repeatedly allocate remote buffers for a hung editor.
            self.blocked.add(scope)
            return None
        if (self.layouts.capture() != target or not self.layouts.valid(target)
                or len(text.encode('utf-8')) > 65_536):
            return None
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
        self.wake = threading.Event()
        self.last = None
        self.last_cleanup = 0
        self.failed = False
        self.state = None

    @property
    def enabled(self):
        return bool(not self.closed and not self.failed and self.settings and self.settings.journal_enabled)

    def configure(self, settings):
        validate_journal(settings.journal_days, settings.journal_apps)
        with self.lock:
            self.settings = settings
            self.generation += 1
            self.failed = False
            self.last_cleanup = 0
            self.state = None
            if not settings.journal_enabled:
                self.last = None
                self.reader = None
            if settings.journal_enabled and self.worker is None:
                self.worker = threading.Thread(target=self._run, name='TypeBridgeJournal', daemon=True)
                self.worker.start()
        self.wake.set()

    def poll(self):
        with self.lock:
            if not self.enabled:
                return
            settings, generation = self.settings, self.generation
            if self.reader is None:
                self.reader = self.reader_factory()
            reader = self.reader
        snapshot = reader.read(settings)
        with self.lock:
            if not self.enabled or generation != self.generation:
                return  # In-flight read cannot write after disabling returns.
            now = self.clock()
            if now - self.last_cleanup >= 3600:
                self.store.cleanup(settings.journal_days)
                self.last_cleanup = now
            if snapshot is None or not snapshot.text:
                self._status('Дневник включён: ожидание доступного текстового поля')
                return
            key = (snapshot.scope, hashlib.sha256(snapshot.text.encode('utf-8')).digest())
            if key != self.last and self.store.append(snapshot.app, snapshot.text, settings.journal_days):
                self.last = key
                self._status('Дневник включён: снимок сохранён ' + time.strftime('%H:%M:%S', time.localtime(now)))

    def _status(self, value):
        if value != self.state:
            self.state = value
            self.notify((self.generation, value))

    def _run(self):
        while not self.closed:
            try:
                self.poll()
            except Exception:
                with self.lock:
                    if self.settings and self.settings.journal_enabled and not self.closed:
                        self.failed = True
                        self.reader = None
                        self.notify((self.generation, 'Ошибка дневника: запись остановлена. Проверь доступ к папке и настройки.'))
            self.wake.wait(2)
            self.wake.clear()

    def clear(self):
        with self.lock:
            self.store.clear()
            # Keep the last hash, so unchanged text isn't immediately re-added.

    def close(self):
        with self.lock:
            self.closed = True
            self.generation += 1
            self.reader = None
            self.last = None
        self.wake.set()
