"""DPAPI uses synthetic data only; clipboard APIs are always mocked."""
import ctypes
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

import clipboard_recovery as recovery
from clipboard_selection import NativeCopy, SelectionUnavailable, BitmapBackup, copy_transaction


class ClipboardRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.rows = [(13, 'SYNTHETIC private text\0'.encode('utf-16-le')),
                     (49443, b'{\\rtf1 SYNTHETIC rich text}')]
        self.names = lambda fmt: 'Rich Text Format'
        self.patch_folder = patch('clipboard_recovery.recovery_folder', return_value=self.folder)
        self.patch_folder.start()
        self.addCleanup(self.patch_folder.stop)

    def native(self):
        native = NativeCopy.__new__(NativeCopy)
        native.cb, native.gui = Mock(), Mock()
        native.cb.GetClipboardFormatName.side_effect = self.names
        native.hwnd, native.bitmaps = 100, []
        native.sequence = Mock(return_value=11)
        native.owned_by = Mock(return_value=True)
        kernel, api, buffers = Mock(), Mock(), []
        def allocate(flags, size):
            buffer = ctypes.create_string_buffer(size)
            buffers.append(buffer)
            return ctypes.addressof(buffer)
        kernel.GlobalAlloc.side_effect = allocate
        kernel.GlobalLock.side_effect = lambda handle: handle
        api.SetClipboardData.side_effect = lambda fmt, handle: handle
        return native, kernel, api, buffers

    def dll(self, kernel, api):
        return patch('ctypes.WinDLL', side_effect=lambda name, **kw: api if name == 'user32' else kernel)

    def test_real_dpapi_roundtrip_never_writes_plaintext(self):
        checkpoint = recovery.Checkpoint.create(self.rows, self.names)
        ciphertext = checkpoint.path.read_bytes()
        self.assertNotIn(b'SYNTHETIC', ciphertext)
        self.assertNotIn(self.rows[0][1], ciphertext)
        loaded = recovery.Checkpoint.load(checkpoint.path)
        self.assertEqual(loaded.payload, checkpoint.payload)
        self.assertEqual([(fmt, raw) for fmt, name, meta, raw in recovery.decode(loaded.payload)], self.rows)

    def test_tampered_or_truncated_file_is_not_accepted(self):
        checkpoint = recovery.Checkpoint.create(self.rows, self.names)
        raw = checkpoint.path.read_bytes()
        for corrupted in (raw[:12], raw[:-8], raw[:-16]+bytes([raw[-16] ^ 0xFF])+raw[-15:]):
            with self.subTest(length=len(corrupted)):
                checkpoint.path.write_bytes(corrupted)
                with self.assertRaises(Exception):
                    recovery.Checkpoint.load(checkpoint.path)

    def test_encryption_failure_does_not_clear_clipboard(self):
        native, kernel, api, _ = self.native()
        with patch('clipboard_recovery.protect', side_effect=OSError), self.dll(kernel, api):
            with self.assertRaises(SelectionUnavailable):
                native.restore(self.rows, 11, object())
        native.cb.EmptyClipboard.assert_not_called()
        api.SetClipboardData.assert_not_called()

    def test_failed_disk_write_or_verification_never_starts_copy(self):
        for failure in ('write', 'verify'):
            native, _, _, _ = self.native()
            native.snapshot = Mock(return_value=(self.rows, 11))
            native.keys_released = Mock(return_value=True)
            native.copy = Mock()
            context = (patch.object(Path, 'open', side_effect=OSError) if failure == 'write'
                       else patch.object(recovery.Checkpoint, 'load', side_effect=OSError))
            with self.subTest(failure=failure), context, self.assertRaises(SelectionUnavailable):
                copy_transaction(native, object(), lambda: True)
            native.copy.assert_not_called()

    def test_second_format_failure_retains_full_original_after_close(self):
        native, kernel, api, _ = self.native()
        api.SetClipboardData.side_effect = [1, 0]
        with self.dll(kernel, api), self.assertRaises(SelectionUnavailable):
            native.restore(self.rows, 11, object())
        native.cb.EmptyClipboard.assert_called_once()
        path = native._checkpoint.path
        native.close()
        restored = recovery.Checkpoint.load(path)
        self.assertTrue(restored.matches(self.rows, self.names))
        self.assertEqual(len(recovery.decode(restored.payload)), 2)
        # First handle belongs to Windows, only the rejected handle is freed.
        self.assertEqual(kernel.GlobalFree.call_count, 1)

    def test_first_format_failure_also_preserves_original(self):
        native, kernel, api, _ = self.native()
        api.SetClipboardData.return_value = 0
        api.SetClipboardData.side_effect = None
        with self.dll(kernel, api), self.assertRaises(SelectionUnavailable):
            native.restore(self.rows, 11, object())
        self.assertTrue(native._checkpoint.path.exists())
        self.assertEqual(kernel.GlobalFree.call_count, 2)

    def test_success_removes_only_its_checkpoint_after_closeclipboard(self):
        native, kernel, api, _ = self.native()
        native.protect_snapshot(self.rows)
        path = native._checkpoint.path
        other = recovery.Checkpoint.create(self.rows, self.names)
        native.cb.CloseClipboard.side_effect = lambda: self.assertTrue(path.exists())
        with self.dll(kernel, api):
            self.assertTrue(native.restore(self.rows, 11, object()))
        self.assertFalse(path.exists())
        self.assertTrue(other.path.exists())
        kernel.GlobalFree.assert_not_called()

    def test_clipboard_changed_by_another_app_is_not_overwritten(self):
        native, kernel, api, _ = self.native()
        native._mutation_possible = True  # Already copied in an earlier phase.
        native.sequence.return_value = 12
        with self.dll(kernel, api):
            self.assertFalse(native.restore(self.rows, 11, object()))
        native.cb.EmptyClipboard.assert_not_called()
        path = native._checkpoint.path
        native.close()
        self.assertTrue(path.exists())

    def test_registered_format_ids_are_remapped_after_restart(self):
        checkpoint = recovery.Checkpoint.create(self.rows, self.names)
        native, _, _, _ = self.native()
        native.cb.RegisterClipboardFormat.return_value = 50111
        rows = checkpoint.materialize(native)
        self.assertEqual(rows[1], (50111, self.rows[1][1]))
        self.assertTrue(checkpoint.matches(rows, self.names))

    def test_explicit_recovery_failure_keeps_original_file(self):
        checkpoint = recovery.Checkpoint.create(self.rows, self.names)
        native, kernel, api, _ = self.native()
        native.cb.RegisterClipboardFormat.return_value = 49443
        api.SetClipboardData.side_effect = [1, 0]
        with (patch('clipboard_selection.NativeCopy', return_value=native), self.dll(kernel, api),
              self.assertRaises(SelectionUnavailable)):
            recovery.restore_file(checkpoint.path)
        self.assertTrue(checkpoint.path.exists())
        self.assertTrue(recovery.Checkpoint.load(checkpoint.path).matches(self.rows, self.names))

    def test_explicit_recovery_succeeds_with_existing_checkpoint_only(self):
        checkpoint = recovery.Checkpoint.create(self.rows, self.names)
        native, kernel, api, _ = self.native()
        native.cb.RegisterClipboardFormat.return_value = 49443
        with (patch('clipboard_selection.NativeCopy', return_value=native), self.dll(kernel, api),
              patch.object(recovery.Checkpoint, 'create', side_effect=AssertionError('No new backup'))):
            recovery.restore_file(checkpoint.path)
        self.assertFalse(checkpoint.path.exists())
        self.assertEqual(api.SetClipboardData.call_count, 2)

    def test_cancelled_recovery_leaves_current_clipboard_and_file(self):
        checkpoint = recovery.Checkpoint.create(self.rows, self.names)
        native, kernel, api, _ = self.native()
        native.cb.RegisterClipboardFormat.return_value = 49443
        with (patch('clipboard_selection.NativeCopy', return_value=native), self.dll(kernel, api),
              self.assertRaises(OSError)):
            recovery.restore_file(checkpoint.path, still_valid=lambda: False)
        native.cb.EmptyClipboard.assert_not_called()
        self.assertTrue(checkpoint.path.exists())

    def test_storage_limit_refuses_new_backup_without_removing_old_files(self):
        for index in range(32):
            (self.folder / f'{index}.tbclip').write_bytes(b'kept')
        with self.assertRaises(OSError):
            recovery.Checkpoint.create(self.rows, self.names)
        self.assertEqual(len(list(self.folder.glob('*.tbclip'))), 32)

    def test_bitmap_pixels_survive_native_serialize_recreate_without_clipboard(self):
        gdi = recovery.bitmap_api()
        data = ctypes.create_string_buffer(bytes(range(16)))
        handle = gdi.CreateBitmap(2, 2, 1, 32, data)
        self.assertTrue(handle)
        copy = None
        try:
            rows = [(2, BitmapBackup(handle, 16))]
            checkpoint = recovery.Checkpoint.create(rows, self.names)
            native = SimpleNamespace(cb=Mock(), bitmaps=[])
            materialized = checkpoint.materialize(native)
            copy = materialized[0][1].handle
            self.assertNotEqual(copy, handle)
            self.assertTrue(checkpoint.matches(materialized, self.names))
        finally:
            if copy:
                gdi.DeleteObject(copy)
            gdi.DeleteObject(handle)

    def test_direct_copy_cannot_bypass_verified_backup(self):
        import time
        native, kernel, api, _ = self.native()
        native.target = object()
        with (patch('clipboard_selection.ensure_copy_target_safe'), self.dll(kernel, api),
              self.assertRaises(SelectionUnavailable)):
            native.copy(still_valid=lambda: True, deadline=time.monotonic()+1)
        api.SendInput.assert_not_called()

    def test_privacy_check_runs_before_reading_original_clipboard(self):
        native, _, _, _ = self.native()
        native._privacy_check = Mock(side_effect=SelectionUnavailable('private'))
        with self.assertRaises(SelectionUnavailable):
            native.snapshot()
        native.cb.EnumClipboardFormats.assert_not_called()
        self.assertEqual(list(self.folder.glob('*.tbclip')), [])

    def test_tray_recovery_menu_is_available_in_all_languages(self):
        from tray_win32 import menu_items, MENU_RESTORE_CLIPBOARD
        from ui_i18n import set_language, get_language
        previous = get_language()
        try:
            for language in ('ru', 'en', 'he'):
                set_language(language)
                row = next(row for row in menu_items('stopped') if row[0] == MENU_RESTORE_CLIPBOARD)
                self.assertTrue(row[2])
                if language != 'ru':
                    self.assertNotIn('Восстановить', row[1])
        finally:
            set_language(previous)

    def gui(self):
        from typebridge_app import TypeBridgeGui
        recovery.Checkpoint.create(self.rows, self.names)
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.root, gui.emit, gui.clipboard_tools = Mock(), Mock(), Mock()
        gui.closing = False
        gui.clipboard_tools.selection_copy.side_effect = nullcontext
        return gui

    def test_recovery_dialog_cancellation_never_launches_worker(self):
        gui = self.gui()
        with (patch('typebridge_app.filedialog.askopenfilename', return_value='chosen.tbclip'),
              patch('typebridge_app.messagebox.askyesno', return_value=False),
              patch('typebridge_app.threading.Thread') as thread):
            gui.restore_clipboard_backup()
        thread.assert_not_called()

    def test_recovery_action_dispatches_off_gui_and_reports_only_boolean(self):
        gui = self.gui()
        with (patch('typebridge_app.filedialog.askopenfilename', return_value='chosen.tbclip'),
              patch('typebridge_app.messagebox.askyesno', return_value=True),
              patch('typebridge_app.threading.Thread') as thread,
              patch('clipboard_recovery.restore_file') as restore):
            gui.restore_clipboard_backup()
            restore.assert_not_called()
            work = thread.call_args.kwargs['target']
            work()
        restore.assert_called_once()
        self.assertEqual(restore.call_args.args, ('chosen.tbclip',))
        gui.emit.assert_called_once_with('clipboard-recovery-result', True)

    def test_recovery_thread_start_failure_releases_busy_state(self):
        gui = self.gui()
        with (patch('typebridge_app.filedialog.askopenfilename', return_value='chosen.tbclip'),
              patch('typebridge_app.messagebox.askyesno', return_value=True),
              patch('typebridge_app.threading.Thread') as thread):
            thread.return_value.start.side_effect = RuntimeError
            gui.restore_clipboard_backup()
        self.assertFalse(gui._clipboard_recovery_busy)
        self.assertTrue(any(self.folder.glob('*.tbclip')))


if __name__ == '__main__':
    unittest.main()
