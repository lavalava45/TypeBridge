import ctypes
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from early_native_edit import NativeEditBridge
from comparison_diagnostics import context_report


class ComparisonDiagnosticsTests(unittest.TestCase):
    def fixture(self):
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.trace = []
        bridge.phase = 'audit-test'
        bridge.write_started = bridge.budget_exceeded = bridge.timeout = False
        bridge.check_deadline = Mock()
        bridge.context = SimpleNamespace(focus=123)
        bridge.u = Mock()
        return bridge

    def test_success_records_message_timing_without_message_arguments(self):
        bridge = self.fixture()
        bridge.u.SendMessageTimeoutW.return_value = 1
        bridge.msg(0xB1, 12345, 67890)
        self.assertIn('msg=b1', bridge.trace[0])
        self.assertIn(':ok=1:', bridge.trace[0])
        self.assertNotIn('12345', bridge.trace[0])
        self.assertNotIn('67890', bridge.trace[0])

    def test_timeout_keeps_exact_stage_and_error(self):
        bridge = self.fixture()
        def fail(*args):
            ctypes.set_last_error(1460)
            return 0
        bridge.u.SendMessageTimeoutW.side_effect = fail
        with self.assertRaises(TimeoutError): bridge.msg(0xB1)
        self.assertIn(':error=1460:', bridge.trace[0])
        self.assertTrue(bridge.timeout)

    def test_real_own_process_metadata_is_available_without_focus_or_input(self):
        info = json.loads(context_report({'pid': os.getpid(), 'host_pid': os.getpid()}).split(':', 1)[1])
        self.assertIn('integrity_rid', info['typebridge_rights'])
        self.assertIn('version', info['field_process'])
        self.assertIn('rich_edit', info['field_process'])


if __name__ == '__main__': unittest.main()
