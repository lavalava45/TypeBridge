"""No actual keyboard hooks: exercise a mocked independent Win32 hook."""
import ctypes
from unittest.mock import Mock, patch
import unittest

from keyboard_control_probe import ControlKeyboardProbe, WH_KEYBOARD_LL
from hook_watchdog import HookWatchdog


class ControlProbeTests(unittest.TestCase):
    def test_hook_registers_on_separate_thread_and_only_counts_callbacks(self):
        probe = ControlKeyboardProbe()
        u, k = Mock(), Mock()
        k.GetCurrentThreadId.return_value = 811
        k.GetModuleHandleW.return_value = 123
        u.SetWindowsHookExW.return_value = 456
        u.GetMessageW.return_value = 0
        observed = {}
        def on_register(hook_type, callback, module, thread):
            observed.update(hook_type=hook_type, module=module, thread=thread)
            self.assertEqual(callback(0, 0x100, 991), 55)
            self.assertEqual(callback(0, 0x101, 991), 55)
            return 456
        u.SetWindowsHookExW.side_effect = on_register
        u.CallNextHookEx.return_value = 55
        with patch('keyboard_control_probe.ctypes.WinDLL',
                   side_effect=lambda name, **kwargs: u if name == 'user32' else k):
            probe._run()
        self.assertEqual((probe.callbacks, probe.keydowns), (2, 1))
        self.assertEqual(observed, {'hook_type': WH_KEYBOARD_LL, 'module': 123, 'thread': 0})
        u.CallNextHookEx.assert_called_with(None, 0, 0x101, 991)
        u.UnhookWindowsHookEx.assert_called_once_with(456)
        self.assertEqual(probe.status, 'stopped')

    def test_hook_install_error_is_visible(self):
        probe = ControlKeyboardProbe()
        u, k = Mock(), Mock()
        k.GetCurrentThreadId.return_value = 811
        k.GetModuleHandleW.return_value = 123
        u.SetWindowsHookExW.return_value = 0
        with patch('keyboard_control_probe.ctypes.WinDLL',
                   side_effect=lambda name, **kwargs: u if name == 'user32' else k), \
             patch('keyboard_control_probe.ctypes.get_last_error', return_value=5):
            probe._run()
        self.assertEqual(probe.status, 'hook-error')
        self.assertEqual(probe.error, 5)

    def test_control_comparison_uses_counts_and_ping_not_keys(self):
        probe = ControlKeyboardProbe()
        probe.status = 'registered'
        probe.callbacks = 7
        probe.keydowns = 3
        got = []
        with patch.object(probe, 'ping', return_value=True):
            monitor = HookWatchdog(lambda _kind, msg: got.append(msg),
                                   input_tick=lambda: None, control_probe=probe)
            monitor.poll(None, None)
            probe.callbacks += 5
            monitor.poll(None, None)
        self.assertIn('control_callbacks=12:control_delta=5:control_keydowns=3', got[1])
        self.assertIn('control_ping=posted', got[1])
        self.assertNotIn('991', got[1])


if __name__ == '__main__':
    unittest.main()
