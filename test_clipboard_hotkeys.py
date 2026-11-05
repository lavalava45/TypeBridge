"""Real Python threads with fake Win32 queues; never installs a native hook."""
import ctypes
from dataclasses import replace
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from clipboard_hotkeys import ClipboardHotkeys, KeyboardEvent, PreparedContext, monotonic
from typebridge_app import Settings


class WorkerTests(unittest.TestCase):
    def fixture(self):
        messages = queue.Queue()
        api, kernel = Mock(), Mock()
        owners = []
        api.PeekMessageW.return_value = 0
        api.GetMessageW.side_effect = lambda *args: messages.get(timeout=2)
        api.PostThreadMessageW.side_effect = lambda *args: (messages.put(1) or 1)
        api.SetWindowsHookExW.side_effect = lambda *args: (owners.append(('install', threading.get_ident())) or 42)
        api.UnhookWindowsHookEx.side_effect = lambda *args: (owners.append(('remove', threading.get_ident())) or 1)
        api.CallNextHookEx.return_value = 0
        api.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key == 0x11 else 0
        kernel.GetCurrentThreadId.side_effect = threading.get_ident
        with patch('ctypes.WinDLL', side_effect=lambda name, **kw: api if name == 'user32' else kernel):
            router = ClipboardHotkeys(Mock(), Mock())
        return router, api, owners

    def test_install_reconfigure_remove_use_one_noncaller_thread(self):
        router, api, owners = self.fixture()
        try:
            router.configure(replace(Settings(), plain_paste=True))
            owner = router.worker.ident
            router.configure(replace(Settings(), quick_search=True))
            self.assertEqual(router.worker.ident, owner)
            router.configure(Settings())
            self.assertIsNone(router.hook)
        finally:
            router.close()
        self.assertEqual(owners, [('install', owner), ('remove', owner)])
        self.assertNotEqual(owner, threading.get_ident())
        self.assertFalse(router.worker.is_alive())

    def test_disabled_settings_create_no_thread(self):
        router, api, _ = self.fixture()
        router.configure(Settings())
        self.assertIsNone(router.worker)
        api.SetWindowsHookExW.assert_not_called()
        router.close()

    def test_disable_drains_consumed_keyup_and_blocks_new_actions(self):
        router, api, _ = self.fixture()
        settings = replace(Settings(), plain_paste=True, plain_paste_key='Ctrl+V')
        event = KeyboardEvent(vkCode=0x56)
        try:
            router.configure(settings)
            router.context_stop.set()
            router.context_worker.join(1)
            router.context_cache = PreparedContext(router.layouts.capture.return_value,
                                                   router.settings, router.context_generation, monotonic())
            self.assertEqual(router.handle(0, 0x100, ctypes.addressof(event)), 1)
            api.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key in (0x11, 0x56) else 0
            router.configure(Settings())
            self.assertEqual(router.hook, 42)  # Held consumed key still owned.
            self.assertEqual(router.handle(0, 0x100, ctypes.addressof(event)), 1)
            self.assertEqual(router.handle(0, 0x101, ctypes.addressof(event)), 1)
            router.configure(Settings())  # Barrier after scheduled cleanup.
            self.assertIsNone(router.hook)
            self.assertEqual(router.notify.call_count, 1)
        finally:
            router.close()

    def test_failed_install_fails_open_and_close_is_repeatable(self):
        router, api, _ = self.fixture()
        api.SetWindowsHookExW.return_value = 0
        api.SetWindowsHookExW.side_effect = None
        try:
            with self.assertRaises(OSError):
                router.configure(replace(Settings(), quick_search=True))
            self.assertFalse(router.accepting.is_set())
            self.assertFalse(router.bindings)
        finally:
            router.close()
            router.close()

    def test_thread_start_failure_never_installs_hook(self):
        router, api, _ = self.fixture()
        with patch('clipboard_hotkeys.threading.Thread', side_effect=RuntimeError):
            with self.assertRaises(OSError):
                router.configure(replace(Settings(), quick_search=True))
        api.SetWindowsHookExW.assert_not_called()

    def test_failed_thread_start_can_be_closed_without_joining_unstarted_thread(self):
        router, api, _ = self.fixture()
        with patch('clipboard_hotkeys.threading.Thread') as factory:
            factory.return_value.start.side_effect = RuntimeError
            factory.return_value.is_alive.return_value = False
            with self.assertRaises(OSError):
                router.configure(replace(Settings(), quick_search=True))
            router.close()
        self.assertIsNone(router.worker)

    def test_reconfigure_reconciles_a_missing_keyup_without_synthetic_input(self):
        router, api, _ = self.fixture()
        settings = replace(Settings(), plain_paste=True)
        try:
            router.configure(settings)
            router.suppressed.add(0x56)
            router.configure(settings)  # Physical V is up, but KEYUP was missed.
            self.assertFalse(router.suppressed)
        finally:
            router.close()

    def test_injected_and_negative_events_always_forward(self):
        router, api, _ = self.fixture()
        event = KeyboardEvent(vkCode=0x56, flags=0x10)
        self.assertEqual(router.handle(-1, 0x100, ctypes.addressof(event)), 0)
        self.assertEqual(router.handle(0, 0x100, ctypes.addressof(event)), 0)
        router.notify.assert_not_called()

    def test_slow_install_times_out_without_late_interception_or_second_worker(self):
        router, api, _ = self.fixture()
        release = threading.Event()
        api.SetWindowsHookExW.side_effect = lambda *args: (release.wait(3) and 42)
        try:
            with self.assertRaises(OSError):
                router.configure(replace(Settings(), quick_search=True))
            owner = router.worker
            self.assertFalse(router.accepting.is_set())
            with self.assertRaises(OSError):
                router.configure(replace(Settings(), quick_search=True))
            self.assertIs(router.worker, owner)
        finally:
            release.set()
            router.close()
        self.assertFalse(router.accepting.is_set())
        self.assertIsNone(router.hook)

    def test_tray_shutdown_continues_when_hook_worker_cannot_join(self):
        from tray_win32 import TrayController
        tray = TrayController(Mock())
        tray.clipboard_hotkeys = Mock()
        tray.clipboard_hotkeys.close.side_effect = OSError
        tray._close_clipboard_hotkeys()
        self.assertEqual(tray.notify.call_args.args[0][0], 'tray-error')


if __name__ == '__main__':
    unittest.main()
