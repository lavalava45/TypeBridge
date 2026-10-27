"""Journal cancellation/recovery regressions: fake editors and temporary SQLite only."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import ctypes
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from input_journal import JournalService, JournalStore, NativeJournalReader, Snapshot
from typebridge_app import Settings


class ReaderRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.reader = NativeJournalReader.__new__(NativeJournalReader)
        self.reader.layouts = Mock()
        self.reader.clock = lambda: self.now
        self.reader.blocked = {}
        self.target = SimpleNamespace(pid=1, host_pid=1, top=2, focus=3)
        self.reader.layouts.capture.return_value = self.target
        self.reader.layouts.valid.return_value = True
        self.enterContext(patch('app_exclusions.target_allowed', return_value=True))
        process = self.enterContext(patch('psutil.Process')).return_value
        process.name.return_value = 'notepad.exe'
        process.create_time.return_value = 123
        self.process = process
        self.cls = self.enterContext(patch('win32gui.GetClassName', return_value='Edit'))
        self.enterContext(patch('win32gui.GetWindowLong', return_value=0))
        self.api = self.reader.layouts.u.SendMessageTimeoutW

    @staticmethod
    def healthy(hwnd, command, wp, lp, flags, timeout, result):
        result._obj.value = 5
        if command == 0x000D:
            source = ctypes.create_unicode_buffer('hello')
            ctypes.memmove(lp, source, ctypes.sizeof(source))
        return 1

    def test_timeout_is_visible_bounded_and_resumes_only_after_probe(self):
        self.api.return_value = 0
        self.assertIsNone(self.reader.read(Settings()))
        self.assertEqual(self.reader.status, 'timeout')
        self.assertIn('30', JournalService._reader_status(self.reader))
        self.now = 29
        self.reader.read(Settings())
        self.assertEqual(self.api.call_count, 1)
        self.now = 30
        self.api.side_effect = self.healthy
        snapshot = self.reader.read(Settings())
        self.assertEqual(snapshot.text, 'hello')
        self.assertEqual([c.args[1] for c in self.api.call_args_list],
                         [0x000E, 0, 0x000E, 0x000D, 0x000E])

    def test_hung_editor_gets_no_text_read_and_only_three_attempts(self):
        self.api.return_value = 0
        for self.now in (0, 30, 90, 100000):
            self.assertIsNone(self.reader.read(Settings()))
        self.assertEqual(self.reader.status, 'timeout-exhausted')
        self.assertEqual([c.args[1] for c in self.api.call_args_list], [0x000E, 0, 0])
        self.assertIn('3 таймаутов', JournalService._reader_status(self.reader))

    def test_scintilla_timeout_does_not_reallocate_before_response_probe(self):
        self.cls.return_value = 'Scintilla'
        self.process.name.return_value = 'notepad++.exe'
        with patch('early_scintilla.ScintillaEarlyBridge') as factory:
            bridge = factory.return_value.__enter__.return_value
            bridge.msg.return_value = 5
            bridge.snapshot.side_effect = TimeoutError()
            self.reader.read(Settings())
            self.now = 30
            self.api.return_value = 0
            self.reader.read(Settings())
            self.assertEqual(factory.call_count, 1)
            self.now = 90
            self.api.side_effect = self.healthy
            bridge.snapshot.side_effect = None
            bridge.snapshot.return_value = (b'hello', 5)
            self.assertEqual(self.reader.read(Settings()).text, 'hello')
            self.assertEqual(factory.call_count, 2)
            bridge.replace.assert_not_called()

    def test_native_and_scintilla_size_refusals_have_explicit_status(self):
        def too_big(*args):
            args[-1]._obj.value = 70000
            return 1
        self.api.side_effect = too_big
        self.assertIsNone(self.reader.read(Settings()))
        self.assertEqual(self.reader.status, 'too-large')
        self.assertEqual(self.api.call_count, 1)
        self.cls.return_value = 'Scintilla'
        self.process.name.return_value = 'notepad++.exe'
        with patch('early_scintilla.ScintillaEarlyBridge') as factory:
            bridge = factory.return_value.__enter__.return_value
            bridge.msg.return_value = 70000
            self.reader.read(Settings())
            self.assertEqual(self.reader.status, 'too-large')
            bridge.snapshot.assert_not_called()

    def test_reused_pid_does_not_inherit_previous_process_quarantine(self):
        self.api.return_value = 0
        self.reader.read(Settings())
        self.process.create_time.return_value = 456
        self.api.side_effect = self.healthy
        self.assertEqual(self.reader.read(Settings()).text, 'hello')


class StorageConcurrencyTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(folder).resolve() / 'journal.sqlite3'
        self.now = 0
        self.store = JournalStore(self.path, encrypt=lambda raw: raw[::-1],
                                  decrypt=lambda raw: raw[::-1], clock=lambda: self.now)
        self.reader = Mock()
        self.reader.read.return_value = Snapshot((1, 2, 3), 'notepad.exe', 'pending')
        self.service = JournalService(self.store, Mock(), reader_factory=lambda: self.reader,
                                      clock=lambda: self.now)
        self.service.worker = Mock()  # Only explicitly invoked background poll.
        self.addCleanup(self.service.close)

    def test_existing_disabled_database_is_pruned_without_editor_reads(self):
        self.store.append('notepad.exe', 'old', 365)
        self.now = 10 * 86400
        self.service.configure(Settings())
        self.service.poll()
        self.reader.read.assert_not_called()
        self.assertEqual(self.store.list(), [])

    def test_fresh_disabled_install_creates_no_database_reader_or_worker(self):
        self.service.worker = None
        with patch('input_journal.threading.Thread') as factory:
            self.service.configure(Settings())
            self.service.poll()
            factory.assert_not_called()
        self.reader.read.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_cancellation_rolls_back_an_uncommitted_snapshot(self):
        @contextmanager
        def refuse():
            yield False
        self.assertFalse(self.store.append('notepad.exe', 'must not persist', 7, commit_guard=refuse))
        self.assertEqual(self.store.list(), [])

    def test_configure_does_not_wait_for_encryption_and_cancels_commit(self):
        entered, release = threading.Event(), threading.Event()
        def encrypt(raw):
            entered.set()
            if not release.wait(2):
                raise AssertionError('test timed out')
            return raw[::-1]
        self.store.encrypt = encrypt
        self.service.configure(replace(Settings(), journal_enabled=True))
        errors = []
        def poll():
            try:
                self.service.poll()
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=poll)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            changed = threading.Event()
            settings_thread = threading.Thread(target=lambda: (self.service.configure(Settings()), changed.set()))
            settings_thread.start()
            self.assertTrue(changed.wait(0.5), 'settings waited for encryption')
            settings_thread.join(1)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.assertEqual(self.store.list(), [])

    def test_sqlite_writer_wait_does_not_block_configuration(self):
        self.store.append('notepad.exe', 'existing', 365)
        locked = sqlite3.connect(self.path)
        self.addCleanup(locked.close)
        locked.execute('BEGIN IMMEDIATE')
        entered = threading.Event()
        self.store.encrypt = lambda raw: (entered.set(), raw[::-1])[1]
        self.service.configure(replace(Settings(), journal_enabled=True))
        # Maintenance is not part of this writer-wait scenario.
        self.service.last_cleanup = self.now
        errors = []
        def poll():
            try:
                self.service.poll()
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=poll)
        worker.start()
        changed = threading.Event()
        try:
            self.assertTrue(entered.wait(1))
            config = threading.Thread(target=lambda: (self.service.configure(Settings()), changed.set()))
            config.start()
            self.assertTrue(changed.wait(0.5), 'settings waited for SQLite writer')
            config.join(1)
        finally:
            locked.rollback()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        rows = self.store.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.store.text(rows[0][0]), 'existing')

    def test_cleanup_failure_has_backoff_even_when_recording_disabled(self):
        self.service.store = Mock()
        self.service.configure(Settings())
        self.service.store.cleanup.side_effect = OSError('busy')
        self.service.poll()
        self.assertIn('автоочистки', self.service.notify.call_args.args[0][1])
        self.service.poll()
        self.assertEqual(self.service.store.cleanup.call_count, 1)

    def test_busy_commit_rolls_back_immediately_and_recording_can_retry(self):
        self.store.append('notepad.exe', 'existing', 365)
        reader = sqlite3.connect(self.path)
        self.addCleanup(reader.close)
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM snapshots').fetchall()
        self.service.configure(replace(Settings(), journal_enabled=True))
        self.service.last_cleanup = self.now
        try:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                self.service.poll()
            self.assertEqual(caught.exception.sqlite_errorcode & 0xff, sqlite3.SQLITE_BUSY)
        finally:
            reader.rollback()
        self.service.poll()
        self.assertEqual(len(self.store.list()), 2)

    def test_transient_sqlite_error_does_not_permanently_disable_service(self):
        self.service.configure(replace(Settings(), journal_enabled=True))
        busy = sqlite3.OperationalError('database is locked')
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        self.service.poll = Mock(side_effect=busy)
        self.service.wake = Mock()
        self.service.wake.wait.side_effect = lambda seconds: setattr(self.service, 'closed', True)
        self.service._run()
        self.assertFalse(self.service.failed)
        self.assertIn('база занята', self.service.notify.call_args.args[0][1])


class SharedInputTests(unittest.TestCase):
    def test_clipboard_runtime_does_not_import_main_application(self):
        import subprocess, sys
        result = subprocess.run([sys.executable, '-c',
            "import sys,ctypes; from win32_input import Input,KeyInput; "
            "from clipboard_tools import plain_paste_release_inputs; "
            "e=(Input*2)(); e[0].type=e[1].type=1; "
            "e[0].u.ki=KeyInput(17,0,0,0,0); e[1].u.ki=KeyInput(86,0,0,0,0); "
            "r=plain_paste_release_inputs(e,2); "
            "assert ctypes.sizeof(Input)==40; "
            "assert [x.u.ki.wVk for x in r]==[86,17]; "
            "assert 'typebridge_app' not in sys.modules"], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main()
