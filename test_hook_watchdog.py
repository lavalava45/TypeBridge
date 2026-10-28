"""Watchdog and exception counters without installing Windows hooks."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dictionary_diagnostics import record_debug_event
from hook_watchdog import HookWatchdog
from typebridge_app import LiveHook, TypeBridgeGui


class HookWatchdogTests(unittest.TestCase):
    def test_mouse_timing_counts_slow_calls_without_per_move_logging(self):
        hook = LiveHook.__new__(LiveHook)
        hook.debug_mouse_callbacks = 0
        hook.handle_mouse = Mock(return_value=0)
        with patch('typebridge_app.perf_counter', side_effect=[0, .006, 1, 1.001]), \
             patch('typebridge_app.emit_pipeline') as emit:
            hook._mouse_probe_callback(0, 0x200, 0)
            hook._mouse_probe_callback(0, 0x200, 0)
        self.assertEqual(hook.debug_mouse_callbacks, 2)
        self.assertEqual(hook.debug_mouse_max_us, 6000)
        self.assertEqual(hook.debug_mouse_slow, 1)
        emit.assert_not_called()

    def test_watchdog_exports_numeric_mouse_timing(self):
        hook = Mock()
        hook.debug_mouse_max_us, hook.debug_mouse_slow = 7200, 3
        # Supply the existing numeric counters without installing any hook.
        for name in ('debug_keyboard_callbacks','debug_keydowns','debug_mouse_callbacks',
                     'debug_probe_pongs','debug_callback_errors','debug_keyboard_slow',
                     'debug_keyboard_max_ms'):
            setattr(hook, name, 0)
        watchdog = HookWatchdog(Mock(), input_tick=lambda: 1)
        with patch('hook_watchdog.emit_pipeline') as emit:
            watchdog.poll(hook, Mock())
        self.assertEqual(emit.call_args.kwargs['mouse_max_us'], 7200)
        self.assertEqual(emit.call_args.kwargs['mouse_slow'], 3)

    def test_slow_successful_callback_does_not_quarantine_future_native_edits(self):
        hook = LiveHook.__new__(LiveHook)
        hook.emit = Mock()
        hook.user32 = Mock()
        hook.native_edit_disabled = False
        hook.native_early_disabled = False
        hook.native_edit_attempts = 0
        hook.pipeline_debug = False
        hook.debug_keyboard_callbacks = 0
        hook.debug_keydowns = 0
        hook.debug_callback_errors = 0
        hook.debug_keyboard_max_ms = 0
        hook.debug_keyboard_slow = 0
        hook.handle_keyboard = Mock(side_effect=lambda *a: setattr(hook, 'native_edit_attempts', 1) or 0)
        with patch('typebridge_app.perf_counter', side_effect=[1.0, 1.241]):
            self.assertEqual(hook._keyboard_probe_callback(0, 0x100, 0), 0)
        self.assertFalse(hook.native_edit_disabled)
        self.assertFalse(hook.native_early_disabled)
        self.assertEqual(hook.debug_keyboard_slow, 1)
        hook.emit.assert_not_called()

    def test_slow_callback_without_native_operation_does_not_quarantine(self):
        hook = LiveHook.__new__(LiveHook)
        hook.emit = Mock()
        hook.user32 = Mock()
        hook.native_edit_disabled = False
        hook.native_edit_attempts = 0
        hook.pipeline_debug = False
        hook.debug_keyboard_callbacks = 0
        hook.debug_keydowns = 0
        hook.debug_callback_errors = 0
        hook.debug_keyboard_max_ms = 0
        hook.debug_keyboard_slow = 0
        hook.handle_keyboard = Mock(return_value=0)
        with patch('typebridge_app.perf_counter', side_effect=[1.0, 1.241]):
            self.assertEqual(hook._keyboard_probe_callback(0, 0x100, 0), 0)
        self.assertFalse(hook.native_edit_disabled)
        hook.emit.assert_not_called()

    def test_declined_native_admission_does_not_trigger_slow_callback_quarantine(self):
        hook = LiveHook.__new__(LiveHook)
        hook.native_edit_attempts = 0
        hook.native_edit_disabled = False
        hook.native_early_disabled = False
        hook.emit = Mock()
        editor = SimpleNamespace(write_started=False)
        hook.handle_keyboard = Mock(side_effect=lambda *a: hook._note_native_mutation(editor) or 0)
        hook.debug_keyboard_callbacks = hook.debug_keydowns = 0
        hook.debug_callback_errors = hook.debug_keyboard_max_ms = hook.debug_keyboard_slow = 0
        hook.pipeline_debug = False
        with patch('typebridge_app.perf_counter', side_effect=[1, 1.245]):
            hook._keyboard_probe_callback(0, 0x100, 0)
        self.assertFalse(hook.native_edit_disabled)
        hook.emit.assert_not_called()

    def test_gui_poll_sees_windows_activity_without_hook_callbacks(self):
        ticks = iter((100, 120, 120))
        with tempfile.TemporaryDirectory() as folder:
            watchdog = HookWatchdog(
                lambda kind, event: record_debug_event(folder, kind, event),
                input_tick=lambda: next(ticks))
            hook = SimpleNamespace(thread_id=123, keyboard_hook=1234, mouse_hook=1235,
                debug_keyboard_callbacks=0, debug_keydowns=0,
                debug_mouse_callbacks=0, debug_probe_pongs=0,
                debug_callback_errors=0, request_probe_ping=Mock(return_value=True))
            worker = SimpleNamespace(is_alive=lambda: True)
            watchdog.poll(hook, worker)
            watchdog.poll(hook, worker)
            watchdog.poll(hook, worker)
            text = (Path(folder) / 'debug.log').read_text(encoding='utf-8')
            lines = [line for line in text.splitlines() if 'pipeline:watchdog:' in line]
            self.assertEqual(len(lines), 3)
            self.assertIn('alive=yes:thread=yes:keyboard=yes:mouse=yes', lines[0])
            self.assertIn('callbacks=0:keydowns=0:mice=0:pongs=0:errors=0', lines[1])
            self.assertIn('ping=posted:wininput=yes', lines[1])
            self.assertIn('ping=posted:wininput=no', lines[2])
            self.assertEqual(hook.request_probe_ping.call_count, 3)

    def test_ping_disabled_when_no_hook(self):
        got = []
        watchdog = HookWatchdog(lambda kind, message: got.append(message), input_tick=lambda: None)
        watchdog.poll(None, SimpleNamespace(is_alive=lambda: False))
        self.assertIn('thread=no:keyboard=no:mouse=no', got[0])
        self.assertIn('ping=not-sent:wininput=unknown', got[0])

    def test_callback_exception_records_type_and_forwards_keyboard_event(self):
        got = []
        hook = LiveHook.__new__(LiveHook)
        hook.pipeline_debug = True
        hook.emit = lambda kind, message: got.append((kind, message))
        hook.debug_keyboard_callbacks = 0
        hook.debug_keydowns = 0
        hook.debug_callback_errors = 0
        hook.debug_keyboard_slow = 0
        hook.debug_keyboard_max_ms = 0
        hook.keyboard_hook = 123
        hook.user32 = Mock()
        hook.user32.CallNextHookEx.return_value = 17
        hook.handle_keyboard = Mock(side_effect=RuntimeError('secret document contents'))
        self.assertEqual(hook._keyboard_probe_callback(0, 0x100, 987), 17)
        self.assertEqual(hook.debug_keyboard_callbacks, 1)
        self.assertEqual(hook.debug_keydowns, 1)
        self.assertEqual(hook.debug_callback_errors, 1)
        self.assertEqual(got, [('probe',
            'pipeline:callback-error:method=keyboard:reason=RuntimeError')])

    def test_ui_watchdog_uses_independent_file_path_even_if_event_queue_idle(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.closing = False
        gui.hook = None
        gui.worker = None
        gui.run_state = 'starting'
        gui._watchdog_idle_logged = False
        gui._hook_watchdog = Mock()
        gui.root = Mock()
        TypeBridgeGui._poll_hook_watchdog(gui)
        gui._hook_watchdog.poll.assert_called_once_with(None, None)
        gui.root.after.assert_called_once_with(4000, gui._poll_hook_watchdog)


if __name__ == '__main__':
    unittest.main()
