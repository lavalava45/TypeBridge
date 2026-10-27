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

    def test_undo_end_still_attempted_after_an_earlier_message_timeout(self):
        bridge = ScintillaEarlyBridge.__new__(ScintillaEarlyBridge)
        bridge.started = -1_000_000.0
        bridge.write_started = True
        bridge.budget_exceeded = False
        bridge.timeout = True
        bridge.context = SimpleNamespace(focus=100)
        bridge.u = Mock()
        bridge.u.SendMessageTimeoutW.return_value = 1
        self.assertEqual(bridge.msg(2079), 0)
        self.assertTrue(bridge.timeout)  # Never certify the earlier edit.
        self.assertEqual(bridge.u.SendMessageTimeoutW.call_args.args[5], 15)


class UndoTransactionTests(unittest.TestCase):
    def fixture(self):
        bridge = ScintillaEarlyBridge.__new__(ScintillaEarlyBridge)
        bridge.write_started = False
        bridge.timeout = False
        bridge.target_addr = 321
        bridge.msg = Mock(side_effect=lambda command, *args: {
            2191: 1, 2193: 4, 2194: 2}.get(command, 0))
        return bridge

    def test_success_has_exactly_one_begin_and_end(self):
        bridge = self.fixture()
        bridge.replace(1, 4, b'ab')
        messages = [c.args[0] for c in bridge.msg.call_args_list]
        self.assertEqual(messages, [2190, 2192, 2191, 2193, 2078, 2194, 2160, 2079])
        self.assertTrue(bridge.write_started)

    def test_replace_target_timeout_still_sends_one_bounded_end(self):
        bridge = self.fixture()
        def msg(command, *args):
            if command == 2191: return 1
            if command == 2193: return 4
            if command == 2194:
                bridge.timeout = True
                raise TimeoutError('editor-message')
            return 0
        bridge.msg.side_effect = msg
        with self.assertRaises(TimeoutError):
            bridge.replace(1, 4, b'ab')
        messages = [c.args[0] for c in bridge.msg.call_args_list]
        self.assertEqual(messages, [2190, 2192, 2191, 2193, 2078, 2194, 2079])
        self.assertTrue(bridge.timeout)

    def test_caret_timeout_still_closes_confirmed_undo_group(self):
        bridge = self.fixture()
        def msg(command, *args):
            if command == 2191: return 1
            if command == 2193: return 4
            if command == 2194: return 2
            if command == 2160:
                bridge.timeout = True
                raise TimeoutError('editor-message')
            return 0
        bridge.msg.side_effect = msg
        with self.assertRaises(TimeoutError):
            bridge.replace(1, 4, b'ab')
        self.assertEqual([c.args[0] for c in bridge.msg.call_args_list][-2:], [2160, 2079])

    def test_failed_begin_does_not_send_unmatched_end(self):
        bridge = self.fixture()
        def msg(command, *args):
            if command == 2191: return 1
            if command == 2193: return 4
            if command == 2078:
                bridge.timeout = True
                raise TimeoutError('begin-unconfirmed')
            return 0
        bridge.msg.side_effect = msg
        with self.assertRaises(TimeoutError):
            bridge.replace(1, 4, b'ab')
        self.assertEqual([c.args[0] for c in bridge.msg.call_args_list][-1], 2078)
        self.assertFalse(bridge.write_started)

    def test_failed_end_is_reported_as_uncertain_not_retried(self):
        bridge = self.fixture()
        def msg(command, *args):
            if command == 2191: return 1
            if command == 2193: return 4
            if command == 2194: return 2
            if command == 2079:
                bridge.timeout = True
                raise TimeoutError('end-unconfirmed')
            return 0
        bridge.msg.side_effect = msg
        with self.assertRaises(TimeoutError):
            bridge.replace(1, 4, b'ab')
        self.assertEqual(sum(c.args[0] == 2079 for c in bridge.msg.call_args_list), 1)
        self.assertTrue(bridge.timeout)
