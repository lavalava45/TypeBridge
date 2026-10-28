"""Tray health snapshots without Explorer calls, windows or focus changes."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tray_win32 import TrayController, IconIdentifier, icon_registered, _icon_rect_query
from typebridge_app import TypeBridgeGui


class TrayHealthTests(unittest.TestCase):
    def fixture(self):
        tray = TrayController.__new__(TrayController)
        tray.closing = False
        tray.ready = threading.Event()
        tray.ready.set()
        tray.worker = Mock()
        tray.worker.is_alive.return_value = True
        tray.hwnd = 1
        tray.last_icon_check = 10
        tray.last_icon_status = None
        tray.notify = Mock()
        return tray

    def test_fresh_health_never_queries_explorer_from_gui(self):
        tray = self.fixture()
        with patch('tray_win32.monotonic', return_value=11), \
             patch('tray_win32.win32gui.IsWindow', return_value=True), \
             patch('tray_win32.icon_registered', side_effect=AssertionError('GUI queried Explorer')):
            self.assertTrue(tray.available())
            tray.worker.is_alive.return_value = False
            self.assertFalse(tray.available())

    def test_health_expires_or_clears_on_shell_failure(self):
        tray = self.fixture()
        with patch('tray_win32.monotonic', return_value=16):
            self.assertFalse(tray.available())
        with patch('tray_win32.icon_registered', side_effect=OSError('shell failure')):
            tray._ensure_icon()
        self.assertFalse(tray.ready.is_set())
        with patch('tray_win32.monotonic', return_value=11):
            self.assertFalse(tray.available())

    def test_hung_background_query_cannot_block_minimizing_or_cached_health(self):
        tray = self.fixture()
        entered, release = threading.Event(), threading.Event()
        def query(_):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test query not released')
            return True
        worker = threading.Thread(target=tray._ensure_icon)
        tray.worker = worker
        with patch('tray_win32.icon_registered', side_effect=query) as shell, \
             patch('tray_win32.monotonic', return_value=16):
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(tray.available())
                gui = SimpleNamespace(tray=tray, root=Mock(), emit=Mock())
                TypeBridgeGui.hide(gui)
                gui.root.iconify.assert_called_once()
                gui.root.withdraw.assert_not_called()
                self.assertFalse(release.is_set())
                self.assertEqual(shell.call_count, 1)
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_identifier_type_and_native_signature_are_initialized_once(self):
        _icon_rect_query.cache_clear()
        self.addCleanup(_icon_rect_query.cache_clear)
        query = Mock(return_value=0)
        with patch('tray_win32.ctypes.WinDLL', return_value=SimpleNamespace(Shell_NotifyIconGetRect=query)) as dll:
            self.assertTrue(icon_registered(1))
            self.assertTrue(icon_registered(2))
        dll.assert_called_once_with('shell32')
        self.assertIs(query.argtypes[0]._type_, IconIdentifier)


if __name__ == '__main__':
    unittest.main()
