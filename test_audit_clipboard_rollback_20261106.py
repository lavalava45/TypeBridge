"""Plain-paste rollback model; no real clipboard, focus, hooks, or SendInput."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clipboard_tools import ClipboardTools, ClipboardWriteUncertain
from clipboard_selection import NativeCopy, SelectionUnavailable


class PlainPasteRollbackTests(unittest.TestCase):
    def fixture(self, captures):
        target = SimpleNamespace(top=90, focus=100, pid=123, host_pid=123)
        foreign = SimpleNamespace(top=91, focus=101, pid=123, host_pid=123)
        captures = [target if item == 'a' else foreign for item in captures]
        native = Mock()
        native.sequence.return_value = 21
        native.write_plain.return_value = 21
        backup = Mock(sequence=20)
        backup.restore.return_value = True
        native.prepare_plain_rollback.return_value = backup
        root, notify, layouts = Mock(), Mock(), Mock()
        root.winfo_id.return_value = 987
        layouts.valid.return_value = True
        layouts.capture.side_effect = captures
        service = ClipboardTools(root, notify, native=native)
        service.pending = True
        return target, service, native, backup, layouts

    def invoke(self, target, service, layouts):
        with (patch('tray_layouts.WindowsLayouts', return_value=layouts),
              patch('clipboard_tools.target_allowed', return_value=True),
              patch('win32gui.GetWindowLong', return_value=0),
              patch('win32api.GetAsyncKeyState', return_value=0),
              patch('ctypes.WinDLL') as dll):
            service._paste_when_released(target, 'plain', time.monotonic(), 20, 0)
            return dll

    def test_focus_switch_after_clipboard_write_rolls_back_original(self):
        target, service, native, backup, layouts = self.fixture(('a', 'a', 'b'))
        dll = self.invoke(target, service, layouts)
        native.write_plain.assert_called_once_with('plain', 987, 20)
        backup.restore.assert_called_once_with(21, 987)
        backup.close.assert_called_once()
        dll.assert_not_called()
        self.assertEqual(service.notify.call_args.args[0][0], 'paste-error')
        self.assertFalse(service.pending)

    def test_foreign_clipboard_owner_prevents_destructive_rollback(self):
        target, service, native, backup, layouts = self.fixture(('a', 'a', 'b'))
        backup.restore.return_value = False  # Other app now owns clipboard.
        self.invoke(target, service, layouts)
        self.assertIn('не удалось восстановить',
                      service.notify.call_args.args[0][1])
        backup.restore.assert_called_once_with(21, 987)

    def test_failed_clipboard_write_after_empty_attempts_safe_restore(self):
        target, service, native, backup, layouts = self.fixture(('a', 'a'))
        native.write_plain.side_effect = ClipboardWriteUncertain(21)
        self.invoke(target, service, layouts)
        backup.restore.assert_called_once_with(21, 987)
        backup.close.assert_called_once()

    def test_unidentified_mutation_does_not_overwrite_other_same_owner_copy(self):
        target, service, native, backup, layouts = self.fixture(('a', 'a'))
        native.write_plain.side_effect = OSError('unknown third-party update')
        self.invoke(target, service, layouts)
        backup.restore.assert_not_called()
        self.assertIn('не удалось восстановить', service.notify.call_args.args[0][1])

    def test_before_write_failure_preserves_original_without_restore(self):
        target, service, native, backup, layouts = self.fixture(('a', 'b'))
        native.sequence.return_value = 20
        self.invoke(target, service, layouts)
        native.write_plain.assert_not_called()
        backup.restore.assert_not_called()
        backup.close.assert_called_once()

    def test_accepted_prefix_does_not_restore_pending_paste(self):
        target, service, native, backup, layouts = self.fixture(('a', 'a', 'a', 'a'))
        api = Mock()
        api.SendInput.side_effect = [2, 2]
        with (patch('tray_layouts.WindowsLayouts', return_value=layouts),
              patch('clipboard_tools.target_allowed', return_value=True),
              patch('win32gui.GetWindowLong', return_value=0),
              patch('win32api.GetAsyncKeyState', return_value=0),
              patch('ctypes.WinDLL', return_value=api)):
            service._paste_when_released(target, 'plain', time.monotonic(), 20, 0)
        self.assertEqual([call.args[0] for call in api.SendInput.call_args_list], [4, 2])
        backup.restore.assert_not_called()
        backup.close.assert_called_once()

    def test_strict_clipboard_snapshot_refuses_dropping_ole_bookkeeping(self):
        native = NativeCopy.__new__(NativeCopy)
        native.hwnd = 20
        native.cb = Mock()
        native.cb.EnumClipboardFormats.side_effect = [49161, 13, 0]
        native.cb.GetClipboardFormatName.return_value = 'DataObject'
        native.global_bytes = Mock(return_value=b'text bytes')
        with self.assertRaises(SelectionUnavailable):
            native.snapshot(strict=True)
        native.cb.EmptyClipboard.assert_not_called()

    def test_privacy_is_checked_under_lock_before_any_snapshot_read(self):
        native = NativeCopy.__new__(NativeCopy)
        native.hwnd = 20
        native.cb = Mock()
        native.cb.EnumClipboardFormats.side_effect = AssertionError(
            'No clipboard formats may be enumerated after privacy refusal')
        denied = Mock(side_effect=OSError('private clipboard'))
        with self.assertRaisesRegex(OSError, 'private clipboard'):
            native.snapshot(strict=True, preflight=denied)
        denied.assert_called_once_with()
        native.cb.OpenClipboard.assert_called_once_with(20)
        native.cb.CloseClipboard.assert_called_once_with()
        native.cb.EnumClipboardFormats.assert_not_called()


if __name__ == '__main__':
    unittest.main()
