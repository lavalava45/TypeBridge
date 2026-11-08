"""Console metadata and blocked-worker models, no clipboard or input APIs."""
from dataclasses import replace
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from clipboard_selection import ensure_copy_target_safe, NativeCopy, SelectionUnavailable
from quick_search import QuickSearch, read_selection
from tray_layouts import LayoutTarget
from typebridge_app import Settings


class ConsoleFallbackTests(unittest.TestCase):
    target = LayoutTarget(10, 11, 12, 13)

    def test_console_class_or_process_is_denied_independently(self):
        for cls, name in [('ConsoleWindowClass', 'other.exe'),
                          ('CASCADIA_HOSTING_WINDOW_CLASS', 'other.exe'),
                          ('unknown', 'pwsh.exe'), ('unknown', 'WindowsTerminal.exe')]:
            with self.subTest(cls=cls, name=name), self.assertRaises(SelectionUnavailable):
                ensure_copy_target_safe(self.target, window_class=lambda hwnd: cls,
                                        process_name=lambda pid: name)

    def test_unknown_identity_and_console_host_pid_are_denied(self):
        with self.assertRaises(SelectionUnavailable):
            ensure_copy_target_safe(self.target, window_class=Mock(side_effect=OSError),
                                    process_name=lambda pid: 'notepad.exe')
        target = replace(self.target, host_pid=99)
        with self.assertRaises(SelectionUnavailable):
            ensure_copy_target_safe(target, window_class=lambda hwnd: 'Edit',
                                    process_name=lambda pid: 'conhost.exe' if pid == 99 else 'other.exe')

    def test_regular_editor_is_allowed(self):
        ensure_copy_target_safe(self.target, window_class=lambda hwnd: 'Edit',
                                process_name=lambda pid: 'notepad.exe')

    def test_native_copy_rechecks_before_any_injection(self):
        native = NativeCopy.__new__(NativeCopy)
        native.target = self.target
        with (patch('clipboard_selection.ensure_copy_target_safe', side_effect=SelectionUnavailable('denied')),
              patch('ctypes.WinDLL') as api):
            with self.assertRaises(SelectionUnavailable):
                native.copy()
        api.assert_not_called()

    def test_uia_selection_in_console_does_not_use_fallback(self):
        # Native UIA replaced the legacy PowerShell subprocess. A validated
        # UIA selection must never trigger generic Ctrl+C fallback.
        with (patch('uia_native_worker.get_uia_service') as native,
              patch('quick_search.copy_selection') as fallback):
            native.return_value.request.return_value = {'state': 'ok', 'text': 'selected'}
            self.assertEqual(read_selection(self.target), 'selected')
        native.return_value.request.assert_called_once()
        fallback.assert_not_called()

    def test_delayed_metadata_check_cannot_authorize_late_copy(self):
        native = NativeCopy.__new__(NativeCopy)
        native.target = self.target
        now = [0]
        def slow_check(target):
            now[0] = 30
        with (patch('clipboard_selection.ensure_copy_target_safe', side_effect=slow_check),
              patch('clipboard_selection.time.monotonic', side_effect=lambda: now[0]),
              patch('ctypes.WinDLL') as dll):
            with self.assertRaises(SelectionUnavailable):
                native.copy(still_valid=lambda: True, deadline=8)
        dll.return_value.SendInput.assert_not_called()

    def test_blocked_reader_cannot_spawn_duplicates_or_open_late_result(self):
        release, entered = threading.Event(), threading.Event()
        results = queue.Queue()
        def reader(target):
            entered.set()
            release.wait(2)
            return 'synthetic selection'
        service = QuickSearch(results.put, reader=reader, opener=Mock())
        settings = replace(Settings(), quick_search=True)
        layouts = Mock()
        layouts.capture.return_value = self.target
        layouts.valid.return_value = True
        now = [0]
        try:
            with (patch('quick_search.time.monotonic', side_effect=lambda: now[0]),
                  patch('app_exclusions.target_allowed', return_value=True)):
                service.request(self.target, settings, layouts)
                self.assertTrue(entered.wait(1))
                now[0] = 30
                service.request(self.target, settings, layouts)
                self.assertTrue(service.busy)  # No unbounded replacement threads.
                with self.assertRaises(SelectionUnavailable):
                    with service.guarded_copy(service.generation):
                        self.fail('expired request permitted Copy')
                release.set()
                result = results.get(timeout=1)
                service.receive(result, settings, layouts)
                self.assertFalse(service.busy)
                service.opener.assert_not_called()
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
