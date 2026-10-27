"""Undo cleanup is allowed after elapsed budget, never an edit replay."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from early_scintilla import ScintillaEarlyBridge


class UndoBudgetTests(unittest.TestCase):
    def test_end_undo_is_bounded_but_bypasses_total_elapsed_budget(self):
        bridge = ScintillaEarlyBridge.__new__(ScintillaEarlyBridge)
        bridge.started = -1_000_000.0
        bridge.write_started = True
        bridge.budget_exceeded = bridge.timeout = False
        bridge.context = SimpleNamespace(focus=100)
        bridge.u = Mock()
        bridge.u.SendMessageTimeoutW.return_value = 1
        self.assertEqual(bridge.msg(2079), 0)
        self.assertEqual(bridge.u.SendMessageTimeoutW.call_count, 1)
        self.assertEqual(bridge.u.SendMessageTimeoutW.call_args.args[1], 2079)
        self.assertEqual(bridge.u.SendMessageTimeoutW.call_args.args[5], 15)

    def test_regular_scintilla_message_still_refused_after_budget(self):
        bridge = ScintillaEarlyBridge.__new__(ScintillaEarlyBridge)
        bridge.started = -1_000_000.0
        bridge.write_started = True
        bridge.budget_exceeded = bridge.timeout = False
        bridge.context = SimpleNamespace(focus=100)
        bridge.u = Mock()
        with self.assertRaisesRegex(TimeoutError, 'editor-total-budget'):
            bridge.msg(2194, 5, 12345)
        bridge.u.SendMessageTimeoutW.assert_not_called()
