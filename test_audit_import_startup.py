"""Audit 1–4: temporary data and mocked GUI/Win32, no real input or windows."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from app_paths import ensure_writable_profile, ProfileWriteError, migrate_user_data
from engine import DICTIONARY_FILES, LanguageEngine
from user_lexicon import UserLexicon, LexiconLoadError, recover_lexicon
from typebridge_app import load_startup_lexicon, StartupCancelled
from tray_win32 import TrayController, WM_TRAY, win32con


class RecoveryTests(unittest.TestCase):
    def fixture(self, payload=None):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        lex = UserLexicon()
        lex.add('words', 'en', 'hello')
        lex.save(folder)
        path = folder / 'user_lexicon.json'
        if payload is None:
            doc = json.loads(path.read_bytes())
            doc['entries']['words']['en']['insensitive'] = ['PERSONALWORD']
            payload = json.dumps(doc).encode()
        path.write_bytes(payload)
        try:
            UserLexicon.load(folder)
        except LexiconLoadError as error:
            return folder, path, payload, error
        self.fail('Malformed file was accepted')

    def test_error_has_path_and_record_without_personal_word(self):
        folder, path, raw, error = self.fixture()
        self.assertIn(str(path), str(error))
        self.assertIn('entries.words.en.insensitive[1]', str(error))
        self.assertNotIn('PERSONALWORD', str(error))
        self.assertEqual(path.read_bytes(), raw)

    def test_invalid_json_has_line_and_column_without_payload(self):
        _, _, _, error = self.fixture(b'{\n "secret": }')
        self.assertIn('JSON: 2:', str(error))
        self.assertNotIn('secret', str(error))

    def test_oversized_file_never_becomes_a_partial_recovery_backup(self):
        folder, path, raw, error = self.fixture(b'x' * 2_000_001)
        self.assertIsNone(error.digest)
        with self.assertRaises(ValueError):
            recover_lexicon(error)
        with patch('typebridge_app.load_user_lexicon', side_effect=error), \
             patch('typebridge_app.messagebox.askyesno') as ask:
            with self.assertRaises(LexiconLoadError):
                load_startup_lexicon(Mock())
            ask.assert_not_called()
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(list(folder.glob('*.bad')))

    def test_recovery_preserves_verified_bytes_before_creating_empty_snapshot(self):
        folder, path, raw, error = self.fixture()
        lexicon, backup = recover_lexicon(error)
        self.assertEqual(backup.parent, folder)
        self.assertEqual(backup.suffix, '.bad')
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), error.digest)
        self.assertEqual(backup.read_bytes(), raw)
        self.assertEqual(lexicon.data, UserLexicon.load(folder).data)
        self.assertFalse(lexicon.contains('words', 'en', 'hello'))

    def test_changed_original_is_never_replaced(self):
        folder, path, raw, error = self.fixture()
        path.write_bytes(b'newer version')
        with self.assertRaisesRegex(ValueError, 'изменился'):
            recover_lexicon(error)
        self.assertEqual(path.read_bytes(), b'newer version')
        self.assertFalse(list(folder.glob('*.bad')))

    def test_failed_backup_verification_keeps_original(self):
        _, path, raw, error = self.fixture()
        read_bytes = Path.read_bytes
        def corrupt_backup(p):
            return b'bad disk read' if p.suffix == '.bad' else read_bytes(p)
        with patch.object(Path, 'read_bytes', corrupt_backup), patch.object(UserLexicon, 'save') as save:
            with self.assertRaises(OSError):
                recover_lexicon(error)
            save.assert_not_called()
        self.assertEqual(path.read_bytes(), raw)

    def test_failed_replacement_keeps_original_and_complete_backup(self):
        folder, path, raw, error = self.fixture()
        with patch('user_lexicon.os.replace', side_effect=PermissionError()):
            with self.assertRaises(PermissionError):
                recover_lexicon(error)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual([p.read_bytes() for p in folder.glob('*.bad')], [raw])

    def test_declined_recovery_never_writes_or_displays_second_dialog(self):
        _, path, raw, error = self.fixture()
        with patch('typebridge_app.load_user_lexicon', side_effect=error), \
             patch('typebridge_app.messagebox.askyesno', return_value=False), \
             patch('typebridge_app.recover_lexicon') as recover:
            with self.assertRaises(StartupCancelled):
                load_startup_lexicon(Mock())
            recover.assert_not_called()
        self.assertEqual(path.read_bytes(), raw)

    def test_accepted_recovery_starts_with_verified_empty_lexicon(self):
        _, _, raw, error = self.fixture()
        with patch('typebridge_app.load_user_lexicon', side_effect=error), \
             patch('typebridge_app.messagebox.askyesno', return_value=True), \
             patch('typebridge_app.messagebox.showinfo') as info:
            result = load_startup_lexicon(Mock())
        self.assertEqual(result.data, UserLexicon().data)
        backup = next(error.path.parent.glob('*.bad'))
        self.assertEqual(backup.read_bytes(), raw)
        self.assertIn(str(backup), info.call_args.args[1])

    def test_declined_startup_destroys_root_without_second_dialog(self):
        from typebridge_app import main
        with patch('typebridge_app.tk.Tk') as root, \
             patch('typebridge_app.migrate_user_data'), \
             patch('typebridge_app.TypeBridgeGui', side_effect=StartupCancelled), \
             patch('typebridge_app.messagebox.showerror') as show:
            self.assertEqual(main([]), 1)
        root.return_value.destroy.assert_called_once()
        root.return_value.mainloop.assert_not_called()
        show.assert_not_called()


class StartupFilesTests(unittest.TestCase):
    def test_profile_probe_leaves_no_files(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'profile'
        ensure_writable_profile(folder)
        self.assertEqual(list(folder.iterdir()), [])

    def test_readonly_profile_has_hint_and_does_not_start_migration(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        old = folder / 'settings.json'
        old.write_bytes(b'keep exactly')
        with patch('app_paths.tempfile.NamedTemporaryFile', side_effect=PermissionError('secret OS text')), \
             patch('app_paths._copy_missing') as copy:
            with self.assertRaises(ProfileWriteError) as failure:
                migrate_user_data(folder)
            copy.assert_not_called()
        self.assertIn(str(folder), str(failure.exception))
        self.assertIn('правом записи', str(failure.exception))
        self.assertNotIn('secret OS text', str(failure.exception))
        self.assertEqual(old.read_bytes(), b'keep exactly')

    def test_missing_overlay_has_clear_hint_before_layout_mapper_or_hook(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in DICTIONARY_FILES:
            (folder / name).write_text('word', encoding='utf-8')
        (folder / 'he_custom.txt').unlink()
        for cache in (None, folder / 'cache'):
            with patch('engine.LayoutMapper') as mapper:
                with self.assertRaises(ValueError) as failure:
                    LanguageEngine(folder, cache_dir=cache)
                mapper.assert_not_called()
            self.assertIn('he_custom.txt', str(failure.exception))
            self.assertIn('data рядом с EXE', str(failure.exception))


class TrayCallbackTests(unittest.TestCase):
    def fixture(self):
        tray = TrayController.__new__(TrayController)
        tray.notify = Mock()
        tray._show_menu = Mock(side_effect=RuntimeError('PRIVATE INPUT SHOULD NOT BE LOGGED'))
        return tray

    def test_menu_error_is_metadata_only_rate_limited_and_never_escapes(self):
        tray = self.fixture()
        with patch('tray_win32.monotonic', return_value=10):
            for _ in range(3):
                self.assertEqual(tray._wndproc(1, WM_TRAY, 0, win32con.WM_RBUTTONUP), 0)
        tray.notify.assert_called_once()
        kind, record = tray.notify.call_args.args[0]
        self.assertEqual(kind, 'tray-callback-error')
        self.assertIn('RuntimeError', record)
        self.assertIn('tray_win32.py:', record)
        self.assertNotIn('PRIVATE INPUT', record)
        with patch('tray_win32.monotonic', return_value=16):
            tray._wndproc(1, WM_TRAY, 0, win32con.WM_RBUTTONUP)
        self.assertEqual(tray.notify.call_count, 2)

    def test_broken_notification_sink_cannot_escape_into_win32(self):
        tray = self.fixture()
        tray.notify.side_effect = OSError('broken queue')
        self.assertEqual(tray._wndproc(1, WM_TRAY, 0, win32con.WM_RBUTTONUP), 0)

    def test_failed_error_formatter_cannot_escape_into_win32(self):
        tray = self.fixture()
        with patch('ui_diagnostics.tray_callback_code', side_effect=RuntimeError()):
            self.assertEqual(tray._wndproc(1, WM_TRAY, 0, win32con.WM_RBUTTONUP), 0)

    def test_hotkey_close_error_still_removes_icon_and_posts_quit(self):
        tray = self.fixture()
        tray.ready = threading.Event()
        tray.clipboard_hotkeys = Mock()
        tray.clipboard_hotkeys.close.side_effect = RuntimeError('close failed')
        tray._icon_data = Mock(return_value=())
        with patch('tray_win32.win32gui.Shell_NotifyIcon') as shell, \
             patch('tray_win32.win32gui.PostQuitMessage') as quit:
            self.assertEqual(tray._wndproc(1, win32con.WM_DESTROY, 0, 0), 0)
        shell.assert_called_once()
        quit.assert_called_once_with(0)

    def test_gui_queues_runtime_log_without_activating_window(self):
        import queue
        from typebridge_app import TypeBridgeGui
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.events = queue.Queue()
        code = 'tray-callback-error:message=1:type=RuntimeError:test.py:1:fn'
        gui.events.put(('tray', ('tray-callback-error', code)))
        gui.root = Mock()
        gui.status = Mock()
        gui.closing = False
        gui._queue_diagnostic = Mock()
        gui._drain()
        gui._queue_diagnostic.assert_called_once_with('runtime', code)
        gui.root.deiconify.assert_not_called()
        gui.root.iconify.assert_not_called()
        gui.root.lift.assert_not_called()


if __name__ == '__main__':
    unittest.main()
