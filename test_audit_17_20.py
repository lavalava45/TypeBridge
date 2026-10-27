"""Audit №17–20: modeled races and failures, no global hook or real typing."""
import ctypes
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clipboard_tools import plain_paste_release_inputs, ClipboardTools
from typebridge_app import TypeBridgeGui, Settings, Input, KeyInput, OWN_INPUT_MARKER


class StartupSettingsAuditTests(unittest.TestCase):
    def test_save_during_dictionary_load_wins_before_hook_creation(self):
        from contextlib import nullcontext
        from dataclasses import replace
        old = Settings(conservative=True)
        new = replace(old, conservative=False, early_correction=True)
        entered, release = threading.Event(), threading.Event()
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.closing = False
        gui.run_state = 'stopped'
        gui.corrections_enabled = Mock()
        gui.worker = None
        gui.hook = None
        gui.lexicon = object()
        gui.snippet_config = object()
        gui.cancel_start = threading.Event()
        gui._runtime_config_lock = threading.RLock()
        gui._desired_runtime_config = None
        gui.current_settings = Mock(side_effect=[old, new])
        gui.clipboard_tools = Mock()
        gui.quick_search = Mock()
        gui.tray = None
        gui._configure_journal = Mock()
        gui._apply_caret_indicator = Mock()
        gui.emit = Mock()
        gui.status = Mock()
        gui._sync_tray_state = Mock()

        def slow_load(*a, **kw):
            entered.set()
            if not release.wait(4):
                raise TimeoutError('test did not release dictionary load')
            return SimpleNamespace(conservative=kw['conservative'],
                                   user_lexicon=kw['user_lexicon'],
                                   mapper=SimpleNamespace(layouts={}))
        hook = Mock()
        with patch('typebridge_app.save_settings'), \
             patch('typebridge_app.read_registration', return_value=None), \
             patch('typebridge_app.write_registration'), \
             patch('typebridge_app.require_no_original_switcher'), \
             patch('typebridge_app.exclusive_live_hook', return_value=nullcontext()), \
             patch('typebridge_app.source_data_dir', return_value=Path('not-a-real-folder')), \
             patch('typebridge_app.LanguageEngine', side_effect=slow_load), \
             patch('typebridge_app.dictionary_report', return_value=[]), \
             patch('typebridge_app.LiveHook', return_value=hook) as constructor:
            gui.start()
            self.assertTrue(entered.wait(3))
            self.assertIsNone(gui.hook)
            self.assertEqual(TypeBridgeGui.save(gui), new)
            release.set()
            gui.worker.join(4)
            self.assertFalse(gui.worker.is_alive())
        instance_engine, instance_settings = constructor.call_args.args[:2]
        self.assertIs(instance_settings, new)
        self.assertFalse(instance_engine.conservative)
        self.assertIs(instance_engine.user_lexicon, gui.lexicon)
        self.assertIs(gui._desired_runtime_config[0], new)


class PlainPastePartialAuditTests(unittest.TestCase):
    def test_each_accepted_prefix_releases_only_held_keys(self):
        original = (Input * 4)()
        for item, (vk, flags) in zip(original, ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2))):
            item.type = 1
            item.u.ki = KeyInput(vk, 0, flags, 0, OWN_INPUT_MARKER)
        expected = ([], [0x11], [0x56, 0x11], [0x11], [])
        for sent, release_keys in enumerate(expected):
            releases = plain_paste_release_inputs(original, sent)
            self.assertEqual([event.u.ki.wVk for event in releases], release_keys)
            self.assertTrue(all(event.u.ki.dwFlags & 2 for event in releases))
            self.assertTrue(all(event.u.ki.dwExtraInfo == OWN_INPUT_MARKER for event in releases))

    def test_partial_actual_paste_never_retries_ctrl_v(self):
        # No clipboard mutation: mock the native clipboard, Windows layout,
        # key states and SendInput; check exactly two API batches.
        target = SimpleNamespace(focus=123)
        gui = ClipboardTools.__new__(ClipboardTools)
        gui.closed = False
        gui.generation = 9
        gui.pending = True
        gui.settings = SimpleNamespace(plain_paste=True)
        gui.root = Mock()
        gui.root.winfo_id.return_value = 101
        gui.native = Mock()
        gui.native.write_plain.return_value = 42
        gui.native.sequence.return_value = 42
        gui.notify = Mock()
        layouts = Mock()
        layouts.valid.return_value = True
        layouts.capture.return_value = target
        api = Mock()
        calls = []
        def send(n, events, size):
            calls.append([events[i].u.ki.wVk for i in range(n)])
            return 2 if len(calls) == 1 else n
        api.SendInput.side_effect = send
        with patch('tray_layouts.WindowsLayouts', return_value=layouts), \
             patch('clipboard_tools.target_allowed', return_value=True), \
             patch('win32gui.GetWindowLong', return_value=0), \
             patch('win32api.GetAsyncKeyState', return_value=0), \
             patch('ctypes.WinDLL', return_value=api), \
             patch('clipboard_tools.time.monotonic', return_value=10):
            gui._paste_when_released(target, 'test', 9, None, 9)
        self.assertEqual(calls, [[0x11, 0x56, 0x56, 0x11], [0x56, 0x11]],
                         str(gui.notify.call_args_list))
        self.assertFalse(gui.pending)
        self.assertEqual(gui.notify.call_args.args[0][0], 'paste-error')


class TrayFailureAuditTests(unittest.TestCase):
    def test_corrupted_existing_ico_is_regenerated(self):
        from PIL import Image
        from tray_win32 import _icon_files
        with tempfile.TemporaryDirectory() as directory:
            icons = Path(directory) / 'icons'
            icons.mkdir()
            bad = icons / 'ru.ico'
            bad.write_bytes(b'NOT AN ICON')
            with patch('app_paths.USER_DIR', Path(directory)):
                produced = _icon_files()
                original_bytes = bad.read_bytes()
                _icon_files()
            self.assertNotEqual(original_bytes, b'NOT AN ICON')
            self.assertEqual(bad.read_bytes(), original_bytes)
            for icon in produced.values():
                with Image.open(icon) as image:
                    self.assertEqual(image.format, 'ICO')
                    image.verify()

    def test_fatal_worker_failure_backoff_prevents_two_second_restart_loop(self):
        from tray_win32 import TrayController
        tray = TrayController.__new__(TrayController)
        tray.notify = Mock()
        tray.icons = {}
        tray.hwnd = None
        tray.closing = False
        tray.ready = threading.Event()
        tray.clipboard_hotkeys = None
        tray.tray_start_failures = 0
        tray.last_fatal_error = None
        tray.next_recovery = 0
        tray.worker = Mock()
        tray.worker.is_alive.return_value = False
        tray.start = Mock()
        # Simulate a fatal failure before icon creation, with no real HWND.
        with patch('tray_win32._icon_files', side_effect=OSError('bad ICO')), \
             patch('tray_win32.win32gui.RegisterWindowMessage', return_value=3), \
             patch('tray_win32.win32gui.RegisterClass', return_value=1), \
             patch('tray_win32.win32gui.CreateWindow', return_value=0), \
             patch('tray_win32.win32gui.IsWindow', return_value=False), \
             patch('tray_win32.win32gui.UnregisterClass'), \
             patch('tray_win32.monotonic', return_value=10):
            tray._message_loop()
            tray._message_loop()
        self.assertEqual(tray.tray_start_failures, 2)
        self.assertEqual(tray.notify.call_count, 1)
        self.assertGreaterEqual(tray.next_recovery, 18)
        with patch('tray_win32.monotonic', return_value=12):
            tray.maintain()
        tray.start.assert_not_called()
        with patch('tray_win32.monotonic', return_value=20):
            tray.maintain()
        tray.start.assert_called_once()

    def test_repeated_tray_errors_do_not_steal_foreground(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.events = __import__('queue').Queue()
        gui.events.put(('tray', ('tray-error', 'bad-icon')))
        gui.events.put(('tray', ('tray-error', 'bad-icon')))
        gui.root = Mock()
        gui.root.state.return_value = 'normal'
        gui.status = Mock()
        gui.log = Mock()
        gui.log.index.return_value = '2.0'
        gui.worker = Mock()
        gui.worker.is_alive.return_value = True
        gui.hook = None
        gui.tray = None
        gui.closing = False
        gui.run_state = 'running'
        gui.last_start_error = ''
        gui.native_quarantine_message = ''
        gui.corrections_enabled = Mock()
        gui.corrections_check = Mock()
        gui.emit = Mock()
        gui.show = Mock()
        gui._sync_tray_state = Mock()
        gui._drain()
        gui.show.assert_not_called()
        gui.root.deiconify.assert_not_called()
        gui.root.iconify.assert_not_called()
        self.assertEqual(gui.log.insert.call_count, 1)


if __name__ == '__main__':
    unittest.main()
