"""PC metadata comparison runs in Tk poll, never the low-level keyboard hook."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from typebridge_app import TypeBridgeGui


class ComparisonProducerTests(unittest.TestCase):
    def test_debug_capture_queued_once_on_notepad_focus_off_hook(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.closing = False
        gui._hook_watchdog = None
        gui._comparison_captured = False
        gui.root = Mock()
        gui.emit = Mock()
        gui.hook = Mock()
        gui.hook.context.return_value = SimpleNamespace(process_name='notepad.exe')
        with patch('typebridge_app.DEBUG_ENABLED', True), \
                patch('comparison_diagnostics.capture_context', return_value={'pid':123}) as capture:
            gui._poll_hook_watchdog()
            gui._poll_hook_watchdog()
        capture.assert_called_once()
        gui.emit.assert_called_once_with('comparison', {'pid':123})

    def test_other_app_not_captured(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.closing = False
        gui._hook_watchdog = None
        gui._comparison_captured = False
        gui.root, gui.emit, gui.hook = Mock(), Mock(), Mock()
        gui.hook.context.return_value = SimpleNamespace(process_name='keepass.exe')
        with patch('typebridge_app.DEBUG_ENABLED', True), \
                patch('comparison_diagnostics.capture_context') as capture:
            gui._poll_hook_watchdog()
        capture.assert_not_called()
        gui.emit.assert_not_called()
