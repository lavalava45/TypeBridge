"""Raw Input diagnostic tests without registering devices on the real desktop."""
import ctypes
from ctypes import wintypes as w
from unittest.mock import Mock, patch
import unittest

from raw_input_probe import (RawKeyboardProbe, RawInputDevice, RawInputHeader,
    RIDEV_INPUTSINK, RIDEV_REMOVE, RIM_TYPEKEYBOARD, WM_INPUT)
from hook_watchdog import HookWatchdog


class RawInputTests(unittest.TestCase):
    def test_structures_are_x64_win32_compatible(self):
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(RawInputDevice), 16)
            self.assertEqual(ctypes.sizeof(RawInputHeader), 24)

    def test_reads_header_only_and_counts_keyboard_type_without_key_fields(self):
        probe = RawKeyboardProbe()
        api = Mock()
        calls = []
        def get_data(handle, action, data, size, header_size):
            calls.append((handle, action, header_size))
            header = ctypes.cast(data, ctypes.POINTER(RawInputHeader)).contents
            header.type = RIM_TYPEKEYBOARD if len(calls) == 1 else 0
            header.size = ctypes.sizeof(RawInputHeader)
            return ctypes.sizeof(RawInputHeader)
        api.GetRawInputData.side_effect = get_data
        probe._count_input(api, 123)
        probe._count_input(api, 456)
        self.assertEqual(probe.keyboard_events, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], 0x10000005)  # RID_HEADER, NOT RID_INPUT
        self.assertEqual(probe.message_errors, 0)

    def test_read_error_does_not_count_a_keyboard_event(self):
        probe = RawKeyboardProbe()
        api = Mock()
        api.GetRawInputData.return_value = 0xFFFFFFFF
        probe._count_input(api, 123)
        self.assertEqual(probe.keyboard_events, 0)
        self.assertEqual(probe.message_errors, 1)

    def test_registration_uses_hidden_window_input_sink_no_legacy_block(self):
        probe = RawKeyboardProbe()
        user32, kernel32 = Mock(), Mock()
        registrations = []
        user32.RegisterClassW.return_value = 7
        user32.CreateWindowExW.return_value = 1234
        user32.GetMessageW.return_value = 0
        kernel32.GetModuleHandleW.return_value = 777
        kernel32.GetCurrentThreadId.return_value = 789
        def register(pointer, number, size):
            dev = ctypes.cast(pointer, ctypes.POINTER(RawInputDevice)).contents
            registrations.append((dev.usage_page, dev.usage, dev.flags,
                                  dev.target, number, size))
            return 1
        user32.RegisterRawInputDevices.side_effect = register
        def get_registration(buffer, count_ptr, size):
            ctypes.cast(count_ptr, ctypes.POINTER(w.UINT)).contents.value = 1
            if not buffer:
                ctypes.set_last_error(122)
                return 0xFFFFFFFF
            entry = ctypes.cast(buffer, ctypes.POINTER(RawInputDevice)).contents
            entry.usage_page = 1
            entry.usage = 6
            entry.flags = RIDEV_INPUTSINK
            entry.target = 1234
            return 1
        user32.GetRegisteredRawInputDevices.side_effect = get_registration
        with patch('raw_input_probe.ctypes.WinDLL',
                   side_effect=lambda dll, **kwargs: user32 if dll == 'user32' else kernel32):
            probe._run()
        self.assertEqual(probe.status, 'stopped')
        self.assertEqual(registrations[0][:3], (1, 6, RIDEV_INPUTSINK))
        self.assertEqual(registrations[0][3], 1234)
        self.assertEqual(registrations[-1][:4], (1, 6, RIDEV_REMOVE, None))
        self.assertNotEqual(registrations[0][2], 0x30)  # RIDEV_NOLEGACY

    def test_registration_replaced_by_other_window_is_reported_without_removing_it(self):
        probe = RawKeyboardProbe()
        probe.status = 'registered'
        probe._window = 1234
        user32 = Mock()
        target = [1234]
        def registered(buffer, count_ptr, size):
            ctypes.cast(count_ptr, ctypes.POINTER(w.UINT)).contents.value = 1
            if not buffer:
                ctypes.set_last_error(122)
                return 0xFFFFFFFF
            entry = ctypes.cast(buffer, ctypes.POINTER(RawInputDevice)).contents
            entry.usage_page = 1
            entry.usage = 6
            entry.flags = RIDEV_INPUTSINK
            entry.target = target[0]
            return 1
        user32.GetRegisteredRawInputDevices.side_effect = registered
        with patch('raw_input_probe.ctypes.WinDLL', return_value=user32):
            probe.verify_registration()
            self.assertEqual(probe.reg_state, 'ours')
            target[0] = 5555
            probe.verify_registration()
        self.assertEqual(probe.reg_state, 'other-target')
        self.assertEqual(probe.snapshot()['reg'], 'other-target')

    def test_wm_input_is_counted_before_header_parse_failure(self):
        """A WM_INPUT arrival must be visible even if GetRawInputData fails."""
        probe = RawKeyboardProbe()
        user32, kernel32 = Mock(), Mock()
        user32.RegisterClassW.return_value = 1
        user32.CreateWindowExW.return_value = 1234
        user32.DefWindowProcW.return_value = 0
        kernel32.GetModuleHandleW.return_value = 777
        kernel32.GetCurrentThreadId.return_value = 789
        count = [0]
        callback = [None]
        def register_class(pointer):
            # The native WndProc would be called by DispatchMessageW.
            from raw_input_probe import WindowClass
            callback[0] = ctypes.cast(pointer, ctypes.POINTER(WindowClass)).contents.proc
            return 1
        user32.RegisterClassW.side_effect = register_class
        def get_message(pointer, hwnd, low, high):
            count[0] += 1
            if count[0] == 1:
                ctypes.cast(pointer, ctypes.POINTER(w.MSG)).contents.message = WM_INPUT
                ctypes.cast(pointer, ctypes.POINTER(w.MSG)).contents.lParam = 900
                return 1
            return 0
        user32.GetMessageW.side_effect = get_message
        user32.DispatchMessageW.side_effect = lambda p: callback[0](1234, WM_INPUT, 0, 900)
        user32.GetRawInputData.return_value = 0xFFFFFFFF
        def registration(buffer, count_ptr, size):
            ctypes.cast(count_ptr, ctypes.POINTER(w.UINT)).contents.value = 1
            if not buffer:
                ctypes.set_last_error(122)
                return 0xFFFFFFFF
            dev = ctypes.cast(buffer, ctypes.POINTER(RawInputDevice)).contents
            dev.usage_page, dev.usage = 1, 6
            dev.flags, dev.target = RIDEV_INPUTSINK, 1234
            return 1
        user32.GetRegisteredRawInputDevices.side_effect = registration
        user32.RegisterRawInputDevices.return_value = 1
        with patch('raw_input_probe.ctypes.WinDLL',
                   side_effect=lambda name, **kw: user32 if name == 'user32' else kernel32):
            probe._run()
        self.assertEqual(probe.wm_input_messages, 1)
        self.assertEqual(probe.pump_input_messages, 1)
        self.assertEqual(probe.pump_messages, 1)
        self.assertEqual(probe.message_errors, 1)
        self.assertEqual(probe.keyboard_events, 0)

    def test_registration_failure_is_visible_to_watchdog(self):
        probe = RawKeyboardProbe()
        user32, kernel32 = Mock(), Mock()
        user32.RegisterClassW.return_value = 1
        user32.CreateWindowExW.return_value = 1234
        user32.RegisterRawInputDevices.return_value = 0
        kernel32.GetModuleHandleW.return_value = 777
        kernel32.GetCurrentThreadId.return_value = 789
        with patch('raw_input_probe.ctypes.WinDLL',
                   side_effect=lambda dll, **kwargs: user32 if dll == 'user32' else kernel32), \
             patch('raw_input_probe.ctypes.get_last_error', return_value=87):
            probe._run()
        self.assertEqual(probe.status, 'register-error')
        self.assertEqual(probe.error, 87)
        self.assertEqual(probe.keyboard_events, 0)
        got = []
        watchdog = HookWatchdog(lambda k, v: got.append(v),
                                input_tick=lambda: 1, raw_probe=probe)
        watchdog.poll(None, None)
        self.assertIn('raw=register-error:raw_events=0:raw_errors=0:raw_error=87', got[0])

    def test_4_second_deltas_distinguish_raw_from_low_level_hook(self):
        probe = RawKeyboardProbe()
        probe.status = 'registered'
        got = []
        watchdog = HookWatchdog(lambda k, v: got.append(v),
                                input_tick=lambda: 1, raw_probe=probe)
        hook = Mock(thread_id=1, keyboard_hook=10, mouse_hook=20,
                    debug_keyboard_callbacks=0, debug_keydowns=0,
                    debug_mouse_callbacks=0, debug_probe_pongs=0,
                    debug_callback_errors=0, debug_keyboard_slow=0,
                    debug_keyboard_max_ms=0, debug_mouse_max_us=0,
                    debug_mouse_slow=0)
        hook.request_probe_ping.return_value = True
        watchdog.poll(hook, None)
        probe.keyboard_events = 18
        watchdog.poll(hook, None)
        self.assertIn('raw_delta=18', got[1])
        self.assertIn('ll_delta=0', got[1])
        hook.debug_keyboard_callbacks = 4
        probe.keyboard_events = 22
        watchdog.poll(hook, None)
        self.assertIn('raw_delta=4', got[2])
        self.assertIn('ll_delta=4', got[2])


if __name__ == '__main__':
    unittest.main()
