"""Synthetic journal tests: temporary databases, fake editors, no active input."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from input_journal import JournalStore, JournalService, NativeJournalReader, Snapshot, validate_journal
from typebridge_app import Settings, load_settings, save_settings, TypeBridgeGui


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve() / 'journal' / 'journal.sqlite3'
        self.now = 0
        self.store = JournalStore(self.path, clock=lambda: self.now,
            encrypt=lambda raw: bytes(x ^ 0x55 for x in raw),
            decrypt=lambda raw: bytes(x ^ 0x55 for x in raw))

    def test_no_database_created_by_disabled_or_empty_views(self):
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.text(1), '')
        self.store.cleanup(7)
        self.store.clear()
        self.assertFalse(self.path.parent.exists())

    def test_retention_prunes_old_entries_and_clear(self):
        self.store.append('notepad.exe', 'old', 365)
        self.now = 10 * 86400
        self.store.append('notepad.exe', 'new', 365)
        self.store.cleanup(7)
        rows = self.store.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.store.text(rows[0][0]), 'new')
        self.store.clear()
        self.assertEqual(self.store.list(), [])

    def test_record_limit_and_failed_encryption_do_not_keep_plaintext(self):
        with patch('input_journal.MAX_RECORDS', 2):
            for word in ('first', 'second', 'third'):
                self.store.append('notepad.exe', word, 7)
        self.assertEqual(len(self.store.list()), 2)
        self.assertEqual(self.store.text(self.store.list()[0][0]), 'third')
        original = self.path.read_bytes()
        self.store.encrypt = Mock(side_effect=OSError('crypto unavailable'))
        with self.assertRaises(OSError):
            self.store.append('notepad.exe', 'SENSITIVE-SYNTHETIC-MARKER', 7)
        self.assertEqual(self.path.read_bytes(), original)

    def test_real_windows_dpapi_roundtrip_with_synthetic_data_only(self):
        store = JournalStore(self.path)
        marker = 'SYNTHETIC-JOURNAL-MARKER привет שלום 😀'
        self.assertTrue(store.append('notepad.exe', marker, 7))
        self.assertNotIn(marker.encode('utf-8'), self.path.read_bytes())
        self.assertEqual(store.text(store.list()[0][0]), marker)

    def test_byte_limit_evicts_oldest_snapshot(self):
        with patch('input_journal.MAX_PAYLOAD_BYTES', 8):
            self.store.append('notepad.exe', 'firstxx', 7)
            self.store.append('notepad.exe', 'secondx', 7)
        rows = self.store.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.store.text(rows[0][0]), 'secondx')


class ServiceTests(unittest.TestCase):
    def fixture(self):
        store = Mock()
        store.exists.return_value = False
        reader = Mock()
        reader.read.return_value = Snapshot((1, 2, 3), 'notepad.exe', 'synthetic')
        service = JournalService(store, Mock(), reader_factory=lambda: reader)
        # A sentinel prevents Thread creation. Tests explicitly invoke poll().
        service.worker = Mock()
        service.configure(Settings())
        return service, store, reader

    def test_disabled_maintains_retention_without_reading_or_appending(self):
        service, store, reader = self.fixture()
        service.poll()
        reader.read.assert_not_called()
        store.append.assert_not_called()
        store.cleanup.assert_called_once_with(7)

    def test_only_changes_saved_and_manual_clear_does_not_readd_unchanged_text(self):
        service, store, reader = self.fixture()
        service.configure(replace(Settings(), journal_enabled=True))
        service.poll()
        service.poll()
        store.append.assert_called_once()
        store.cleanup.assert_called_once()
        service.clear()
        service.poll()
        self.assertEqual(store.append.call_count, 1)
        reader.read.return_value = Snapshot((1, 2, 3), 'notepad.exe', 'changed')
        service.poll()
        self.assertEqual(store.append.call_count, 2)

    def test_disabling_during_native_read_discards_result(self):
        service, store, reader = self.fixture()
        service.configure(replace(Settings(), journal_enabled=True))
        def read(settings):
            service.configure(Settings())
            return Snapshot((1, 2, 3), 'notepad.exe', 'must not persist')
        reader.read.side_effect = read
        service.poll()
        store.append.assert_not_called()
        store.cleanup.assert_not_called()

    def test_disk_failure_stops_recording_and_error_contains_no_text(self):
        service, store, reader = self.fixture()
        service.configure(replace(Settings(), journal_enabled=True))
        store.append.side_effect = OSError('disk full')
        service.wake = Mock()
        service.wake.wait.side_effect = lambda seconds: service.close()
        service._run()
        self.assertFalse(service.enabled)
        message = service.notify.call_args.args[0][1]
        self.assertNotIn('synthetic', message)
        self.assertIn('Ошибка дневника', message)


class NativeReaderTests(unittest.TestCase):
    def fixture(self):
        reader = NativeJournalReader.__new__(NativeJournalReader)
        reader.blocked = {}
        reader.clock = lambda: 0
        reader.status = "waiting"
        reader.retry_after = 0
        reader.layouts = Mock()
        target = SimpleNamespace(pid=1, host_pid=1, top=2, focus=3)
        reader.layouts.capture.return_value = target
        reader.layouts.valid.return_value = True
        return reader

    def test_scintilla_read_uses_only_snapshot_not_mutating_bridge_methods(self):
        reader = self.fixture()
        with (patch('app_exclusions.target_allowed', return_value=True),
              patch('psutil.Process') as process,
              patch('win32gui.GetClassName', return_value='Scintilla'),
              patch('win32gui.GetWindowLong', return_value=0),
              patch('early_scintilla.ScintillaEarlyBridge') as factory):
            process.return_value.name.return_value = 'notepad++.exe'
            bridge = factory.return_value.__enter__.return_value
            bridge.msg.return_value = 0
            bridge.snapshot.return_value = ('привет שלום'.encode(), 0)
            snapshot = reader.read(Settings())
            self.assertEqual(snapshot.text, 'привет שלום')
            bridge.prepare.assert_not_called()
            bridge.replace.assert_not_called()

    def test_password_unknown_controls_and_excluded_processes_are_never_read(self):
        for allowed, cls, style in ((False, 'Edit', 0), (True, 'Edit', 0x20),
                                    (True, 'Chrome_RenderWidgetHostHWND', 0)):
            reader = self.fixture()
            with (patch('app_exclusions.target_allowed', return_value=allowed),
                  patch('psutil.Process') as process,
                  patch('win32gui.GetClassName', return_value=cls),
                  patch('win32gui.GetWindowLong', return_value=style),
                  patch('early_scintilla.ScintillaEarlyBridge') as bridge):
                process.return_value.name.return_value = 'notepad.exe'
                self.assertIsNone(reader.read(Settings()))
                reader.layouts.u.SendMessageTimeoutW.assert_not_called()
                bridge.assert_not_called()

    def test_not_in_allowlist_and_closing_editor_fail_closed(self):
        reader = self.fixture()
        with (patch('app_exclusions.target_allowed', return_value=True), patch('psutil.Process') as process):
            process.return_value.name.return_value = 'chrome.exe'
            self.assertIsNone(reader.read(Settings()))
            process.side_effect = OSError('window exited')
            self.assertIsNone(reader.read(Settings()))

    def test_edit_snapshot_uses_only_gettext_messages(self):
        import ctypes
        reader = self.fixture()
        commands = []
        def message(hwnd, command, wp, lp, flags, timeout, result):
            commands.append(command)
            result._obj.value = 5
            if command == 0x000D:
                source = ctypes.create_unicode_buffer('hello')
                ctypes.memmove(lp, source, ctypes.sizeof(source))
            return 1
        reader.layouts.u.SendMessageTimeoutW.side_effect = message
        with (patch('app_exclusions.target_allowed', return_value=True),
              patch('psutil.Process') as process,
              patch('win32gui.GetClassName', return_value='Edit'),
              patch('win32gui.GetWindowLong', return_value=0)):
            process.return_value.name.return_value = 'notepad.exe'
            self.assertEqual(reader.read(Settings()).text, 'hello')
        self.assertEqual(commands, [0x000E, 0x000D, 0x000E])


class SettingsAndGuiTests(unittest.TestCase):
    def test_stale_enabled_status_does_not_override_disabled_indicator(self):
        import queue
        app = TypeBridgeGui.__new__(TypeBridgeGui)
        app.events = queue.Queue()
        app.events.put(('journal', (1, 'Дневник включён: старое сообщение')))
        app.journal_service = SimpleNamespace(generation=2, enabled=False)
        app.journal_status = Mock()
        app.tray = Mock()
        app.closing = False
        app.root = Mock()
        app._drain()
        app.journal_status.set.assert_not_called()
        app.tray.set_journal_enabled.assert_not_called()

    def test_defaults_bounds_types_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            path.write_text('{}')
            self.assertFalse(load_settings(path).journal_enabled)
            expected = replace(Settings(), journal_enabled=True, journal_days=3)
            save_settings(expected, path)
            self.assertEqual(load_settings(path), expected)
            for days in (0, -1, 366, True, '7'):
                with self.assertRaises(ValueError):
                    validate_journal(days, ['notepad.exe'])

    def test_setting_save_failure_does_not_enable_or_start_service(self):
        app = TypeBridgeGui.__new__(TypeBridgeGui)
        app.journal_service = Mock()
        app.journal_enabled = Mock()
        with (patch('typebridge_app.load_settings', return_value=Settings()),
              patch('typebridge_app.save_settings', side_effect=OSError('disk full'))):
            with self.assertRaises(OSError):
                app.apply_journal_settings(True, 7, ['notepad.exe'])
        app.journal_service.configure.assert_not_called()
        app.journal_enabled.set.assert_not_called()

    def test_hidden_viewer_and_tray_indicator(self):
        import tkinter as tk
        from journal_gui import JournalWindow
        from tray_win32 import menu_items, MENU_JOURNAL
        root = tk.Tk()
        root.withdraw()
        service = Mock()
        service.store.list.return_value = []
        app = SimpleNamespace(journal_status=tk.StringVar(master=root, value='Дневник выключен'),
            journal_enabled=tk.BooleanVar(master=root, value=False),
            journal_days=tk.StringVar(master=root, value='7'),
            journal_apps=tk.StringVar(master=root, value='notepad.exe, notepad++.exe'),
            journal_service=service, journal_io=Mock(), apply_journal_settings=Mock())
        try:
            viewer = JournalWindow(root, app)
            viewer.window.withdraw()
            root.update_idletasks()
            self.assertFalse(viewer.window.winfo_ismapped())
            self.assertLessEqual(viewer.window.winfo_reqheight(), 760)
            viewer.save()
            app.apply_journal_settings.assert_called_once_with(False, 7, ('notepad.exe', 'notepad++.exe'))
            menu = menu_items('stopped', journal_enabled=True)
            self.assertTrue(any(row[0] == MENU_JOURNAL for row in menu))
            self.assertTrue(any('Дневник включён' in row[1] for row in menu))
            service.store.append.assert_not_called()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
