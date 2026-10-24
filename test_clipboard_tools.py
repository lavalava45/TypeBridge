"""Clipboard tests use fake APIs; never touch the real clipboard or input."""
from dataclasses import replace
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from clipboard_tools import TextHistory, NativeClipboard, ClipboardTools, clipboard_bindings, MAX_TEXT
from direct_hotkeys import compile_bindings
from typebridge_app import Settings, load_settings, save_settings


class HistoryTests(unittest.TestCase):
    def test_exact_text_dedup_and_recent_order(self):
        history = TextHistory(2)
        for text in ('one', 'שלום\nпривет\tHello', 'one'):
            history.add(text)
        self.assertEqual(history.items, ['one', 'שלום\nпривет\tHello'])
        history.add('three')
        self.assertEqual(history.items, ['three', 'one'])
        history.clear()
        self.assertEqual(history.items, [])

    def test_no_empty_nul_oversized_or_nontext_entries(self):
        history = TextHistory()
        for text in (None, b'abc', '', ' \r\n', 'a\0b', 'a' * (MAX_TEXT + 1)):
            self.assertFalse(history.add(text))
        for index in range(30):
            history.add(str(index) + 'a' * (MAX_TEXT - 2))
        self.assertLessEqual(sum(map(len, history.items)), 1_000_000)


class ClipboardSettingsTests(unittest.TestCase):
    def test_disabled_defaults_legacy_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text('{}')
            self.assertFalse(load_settings(path).clipboard_history)
            self.assertFalse(load_settings(path).plain_paste)
            expected = replace(Settings(), clipboard_history=True, plain_paste=True,
                               clipboard_history_key='Ctrl+Alt+F6', plain_paste_key='Ctrl+Alt+F7')
            save_settings(expected, path)
            self.assertEqual(load_settings(path), expected)

    def test_conflicts_and_invalid_settings_rejected(self):
        for settings in (
            replace(Settings(), clipboard_history=True, clipboard_history_key='Ctrl+Shift+F1'),
            replace(Settings(), clipboard_history=True, plain_paste=True, clipboard_history_key='Ctrl+Shift+V'),
            replace(Settings(), plain_paste_key='F6'),
            replace(Settings(), plain_paste_key='Ctrl+F12'),
            replace(Settings(), plain_paste_key=[]),
            replace(Settings(), plain_paste=True, plain_paste_key='Ctrl+Alt+Pause'),
        ):
            with self.assertRaises(ValueError):
                compile_bindings(settings)
        self.assertEqual(clipboard_bindings(Settings()), {})
        self.assertEqual(len(clipboard_bindings(replace(Settings(), clipboard_history=True, plain_paste=True))), 2)


class NativeClipboardTests(unittest.TestCase):
    def fixture(self):
        native = NativeClipboard.__new__(NativeClipboard)
        native.cb = Mock()
        native.private_formats = [100, 101]
        native.history_format = 102
        native.cb.GetClipboardOwner.return_value = 321
        self.enterContext(patch('win32process.GetWindowThreadProcessId', return_value=(1, 42)))
        native.cb.GetClipboardSequenceNumber.return_value = 7
        native.cb.IsClipboardFormatAvailable.side_effect = lambda fmt: fmt == 13
        native.cb.GetClipboardData.return_value = 'שלום\r\nпривет'
        return native

    def test_read_only_unicode_and_release_lock(self):
        native = self.fixture()
        self.assertEqual(native.read(lambda pid: True, history=True), (7, 'שלום\r\nпривет'))
        native.cb.GetClipboardData.assert_called_once_with(13)
        native.cb.CloseClipboard.assert_called_once()
        native.cb.EmptyClipboard.assert_not_called()

    def test_private_and_excluded_data_not_read(self):
        for private in (True, False):
            native = self.fixture()
            native.cb.IsClipboardFormatAvailable.side_effect = lambda fmt: fmt in (100, 13)
            self.assertIsNone(native.read(lambda pid: private, history=True)[1])
            native.cb.GetClipboardData.assert_not_called()

    def test_ownerless_history_does_not_read_text_even_with_permissive_policy(self):
        native = self.fixture()
        native.cb.GetClipboardOwner.return_value = 0
        allowed = Mock(return_value=True)
        self.assertEqual(native.read(allowed, history=True), (7, None))
        allowed.assert_not_called()
        native.cb.GetClipboardData.assert_not_called()
        native.cb.CloseClipboard.assert_called_once()

    def test_unresolved_owner_pid_does_not_enter_history(self):
        native = self.fixture()
        with patch('win32process.GetWindowThreadProcessId', return_value=(1, 0)):
            self.assertEqual(native.read(lambda pid: True, history=True), (7, None))
        native.cb.GetClipboardData.assert_not_called()

    def test_explicit_plain_read_keeps_ownerless_clipboard_supported(self):
        native = self.fixture()
        native.cb.GetClipboardOwner.return_value = 0
        tools = ClipboardTools(Mock(), Mock(), native=native)
        tools.settings = Settings()
        self.assertEqual(native.read(tools.allowed_pid), (7, 'שלום\r\nпривет'))
        native.cb.GetClipboardData.assert_called_once_with(13)

    def test_known_owner_resolution_failure_or_exclusion_blocks_read(self):
        for name in ('keepass.exe', None):
            with self.subTest(name=name):
                native = self.fixture()
                tools = ClipboardTools(Mock(), Mock(), native=native)
                tools.settings = Settings()
                with patch('psutil.Process') as process:
                    if name is None:
                        process.side_effect = OSError('process unavailable')
                    else:
                        process.return_value.name.return_value = name
                    for history in (False, True):
                        self.assertEqual(native.read(tools.allowed_pid, history=history), (7, None))
                native.cb.GetClipboardData.assert_not_called()

    def test_history_optout_and_image_only_are_ignored(self):
        for formats in ((102, 13), (2,)):
            native = self.fixture()
            native.cb.IsClipboardFormatAvailable.side_effect = lambda fmt: fmt in formats
            native.cb.GetClipboardData.return_value = b'\0\0\0\0'
            self.assertIsNone(native.read(lambda pid: True, history=True)[1])
            self.assertNotIn(unittest.mock.call(13), native.cb.GetClipboardData.call_args_list)

    def test_plain_write_strips_rich_formats_and_checks_sequence(self):
        native = self.fixture()
        native.write_plain('hello', 123, 7)
        native.cb.EmptyClipboard.assert_called_once()
        native.cb.SetClipboardData.assert_called_once_with(13, 'hello')
        native.cb.reset_mock()
        with self.assertRaises(OSError):
            native.write_plain('hello', 123, 8)
        native.cb.EmptyClipboard.assert_not_called()
        native.cb.CloseClipboard.assert_called_once()


class ServiceTests(unittest.TestCase):
    def fixture(self):
        tools = ClipboardTools(Mock(), Mock(), native=Mock())
        tools.native.sequence.return_value = 20
        tools.configure(Settings())
        return tools

    def test_disabled_does_not_start_reader_and_disable_clears(self):
        tools = self.fixture()
        self.assertIsNone(tools.worker)
        tools.native.read.assert_not_called()
        tools.history.add('old')
        tools.configure(Settings())
        self.assertEqual(tools.history.items, [])
        tools.close()

    def test_stale_capture_and_pending_paste_discarded_on_disable(self):
        tools = self.fixture()
        with patch.object(tools, '_start_reader'):
            tools.configure(replace(Settings(), clipboard_history=True, plain_paste=True))
        previous = tools.generation
        tools.configure(Settings())
        tools.receive(('captured', previous, 21, 'secret'))
        with patch.object(tools, '_paste_when_released') as paste:
            tools.receive(('prepared', previous, object(), time.monotonic(), 21, 'text'))
            paste.assert_not_called()
        self.assertFalse(tools.history.items)
        tools.close()

    def test_clear_rejects_already_queued_capture(self):
        tools = self.fixture()
        with patch.object(tools, '_start_reader'):
            tools.configure(replace(Settings(), clipboard_history=True))
        previous = tools.generation
        tools.history.add('old')
        tools.clear_history()
        tools.receive(('captured', previous, 21, 'old'))
        self.assertEqual(tools.history.items, [])
        tools.close()

    def test_paste_cancels_before_write_if_target_changes(self):
        tools = self.fixture()
        layouts = Mock()
        layouts.valid.return_value = True
        layouts.capture.return_value = None
        with patch('tray_layouts.WindowsLayouts', return_value=layouts):
            tools._paste_when_released(object(), 'test', time.monotonic(), 20, tools.generation)
        tools.native.write_plain.assert_not_called()
        self.assertEqual(tools.notify.call_args.args[0][0], 'paste-error')
        tools.close()

    def test_paste_waits_for_modifiers_then_sends_one_ctrl_v_batch(self):
        tools = self.fixture()
        target = Mock(pid=12345, focus=7)
        layouts = Mock()
        layouts.valid.return_value = True
        layouts.capture.return_value = target
        tools.native.write_plain.return_value = 21
        tools.native.sequence.return_value = 21
        api = Mock()
        api.SendInput.return_value = 4
        with (patch('tray_layouts.WindowsLayouts', return_value=layouts),
              patch('clipboard_tools.target_allowed', return_value=True),
              patch('win32gui.GetWindowLong', return_value=0),
              patch('win32api.GetAsyncKeyState', return_value=0x8000)):
            tools._paste_when_released(target, 'שלום\r\nhello', time.monotonic(), 20, tools.generation)
            tools.native.write_plain.assert_not_called()
            tools.root.after.assert_called_once()
            with (patch('win32api.GetAsyncKeyState', return_value=0),
                  patch('ctypes.WinDLL', return_value=api)):
                tools.root.after.call_args.args[1]()
        tools.native.write_plain.assert_called_once()
        api.SendInput.assert_called_once()
        count, events, size = api.SendInput.call_args.args
        self.assertEqual((count, size), (4, 40))
        self.assertEqual([(x.u.ki.wVk, x.u.ki.dwFlags) for x in events],
                         [(0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)])
        tools.close()

    def test_partial_paste_does_not_repeat(self):
        tools = self.fixture()
        target = Mock(pid=12345, focus=7)
        layouts = Mock()
        layouts.valid.return_value = True
        layouts.capture.return_value = target
        tools.native.write_plain.return_value = 21
        tools.native.sequence.return_value = 21
        api = Mock()
        api.SendInput.return_value = 2
        with (patch('tray_layouts.WindowsLayouts', return_value=layouts),
              patch('clipboard_tools.target_allowed', return_value=True),
              patch('win32gui.GetWindowLong', return_value=0),
              patch('win32api.GetAsyncKeyState', return_value=0),
              patch('ctypes.WinDLL', return_value=api)):
            tools._paste_when_released(target, 'test', time.monotonic(), 20, tools.generation)
        self.assertEqual([call.args[0] for call in api.SendInput.call_args_list], [4, 1])
        self.assertEqual(tools.notify.call_args.args[0][0], 'paste-error')
        tools.close()


class HiddenGuiTests(unittest.TestCase):
    def test_settings_and_picker_fit_without_mapping_or_native_access(self):
        import tkinter as tk
        from tkinter import ttk
        from typebridge_app import TypeBridgeGui
        from clipboard_gui import ClipboardWindow
        from user_lexicon import UserLexicon
        root = tk.Tk()
        root.withdraw()
        service = Mock()
        service.settings = Settings()
        service.history = TextHistory()
        picker = None
        try:
            with (tempfile.TemporaryDirectory() as folder,
                  patch('clipboard_tools.ClipboardTools', return_value=service),
                  patch('typebridge_app.load_settings', return_value=Settings()),
                  patch('typebridge_app.load_user_lexicon', return_value=UserLexicon()),
                  patch('typebridge_app.USER_DIR', Path(folder))):
                gui = TypeBridgeGui(root)
                root.update_idletasks()
                gui.clipboard_history.set(True)
                gui.plain_paste.set(True)
                self.assertTrue(gui.current_settings().clipboard_history)
                self.assertTrue(gui.current_settings().plain_paste)
                notebook = next(child for child in root.winfo_children() if isinstance(child, ttk.Notebook))
                self.assertIn('Буфер обмена', [notebook.tab(tab, 'text') for tab in notebook.tabs()])
                picker = ClipboardWindow(root, service)
                picker.window.withdraw()
                root.update_idletasks()
                self.assertFalse(root.winfo_ismapped())
                self.assertFalse(picker.window.winfo_ismapped())
                self.assertLessEqual(picker.status.winfo_reqwidth(), 710)
                service.native.write_plain.assert_not_called()
        finally:
            root.destroy()


class TrayHotkeyTests(unittest.TestCase):
    def test_router_independent_of_correction_state_and_updates_on_disable(self):
        from tray_win32 import TrayController
        tray = TrayController(Mock())
        tray.hwnd = 123
        enabled = replace(Settings(), clipboard_history=True, plain_paste=True)
        with patch('clipboard_hotkeys.ClipboardHotkeys') as factory:
            tray.clipboard_settings = enabled
            tray._register_clipboard_hotkeys()
            factory.return_value.configure.assert_called_once_with(enabled)
            self.assertEqual(tray.run_state[0], 'stopped')
            tray.clipboard_settings = Settings()
            tray._register_clipboard_hotkeys()
            factory.assert_called_once()
            factory.return_value.configure.assert_called_with(Settings())

    def test_router_failure_is_reported(self):
        from tray_win32 import TrayController
        tray = TrayController(Mock())
        tray.hwnd = 123
        tray.clipboard_settings = replace(Settings(), plain_paste=True)
        with patch('clipboard_hotkeys.ClipboardHotkeys') as factory:
            factory.return_value.configure.side_effect = OSError('unavailable')
            tray._register_clipboard_hotkeys()
        self.assertEqual(tray.notify.call_args.args[0][0], 'tray-error')


if __name__ == '__main__':
    unittest.main()
