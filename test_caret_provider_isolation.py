"""Caret geometry stubs: a paused MSAA fallback must not disable native carets."""
import ctypes
from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch

from caret_indicator import CaretReader
from tray_layouts import GuiInfo


class ProviderIsolationTests(unittest.TestCase):
    def fixture(self, native):
        reader = CaretReader.__new__(CaretReader)
        reader.languages = {'en': 0x409}
        reader.u = Mock()
        reader.u.GetForegroundWindow.return_value = 10
        reader.u.GetWindowThreadProcessId.return_value = 7
        reader.u.GetKeyboardLayout.return_value = 0x409
        reader.u.GetDpiForWindow.return_value = 96
        def info(thread, output):
            value = ctypes.cast(output, ctypes.POINTER(GuiInfo)).contents
            value.hwndFocus = 11
            value.hwndCaret = 11 if native else 0
            value.rect.left, value.rect.top = 20, 20
            value.rect.right, value.rect.bottom = 21, 40
            return 1
        reader.u.GetGUIThreadInfo.side_effect = info
        reader._accessible_caret = Mock(return_value=(20, 20, 21, 40))
        return reader

    def environment(self):
        stack = ExitStack()
        gui = stack.enter_context(patch('caret_indicator.win32gui'))
        gui.IsWindowVisible.return_value = True
        gui.IsChild.return_value = True
        gui.GetClassName.return_value = 'Edit'
        gui.GetClientRect.return_value = (0, 0, 200, 200)
        gui.ClientToScreen.side_effect = lambda hwnd, point: point
        api = stack.enter_context(patch('caret_indicator.win32api'))
        api.MonitorFromPoint.return_value = 99
        api.GetMonitorInfo.return_value = {'Work': (0, 0, 500, 500)}
        stack.enter_context(patch('caret_indicator.layout_owner', return_value=(11, 7)))
        return stack

    def test_native_caret_remains_available_when_accessibility_is_paused(self):
        reader = self.fixture(native=True)
        with self.environment():
            sample = reader.snapshot(allow_accessibility=False)
        self.assertEqual(sample.rect, (20, 20, 21, 40))
        reader._accessible_caret.assert_not_called()

    def test_missing_native_caret_hides_without_entering_paused_provider(self):
        reader = self.fixture(native=False)
        with self.environment():
            self.assertIsNone(reader.snapshot(allow_accessibility=False))
        reader._accessible_caret.assert_not_called()

    def test_normal_window_can_still_use_accessibility(self):
        reader = self.fixture(native=False)
        with self.environment():
            self.assertIsNotNone(reader.snapshot())
        reader._accessible_caret.assert_called_once()

    def test_native_budget_exhaustion_does_not_start_unbounded_fallback(self):
        reader = self.fixture(native=False)
        with self.environment(), patch('caret_indicator.monotonic', side_effect=[0, .12]):
            self.assertIsNone(reader.snapshot())
        reader._accessible_caret.assert_not_called()


if __name__ == '__main__':
    unittest.main()
