"""R17 cache/latency models; fake Win32 only, no global hook or user input."""
import ctypes
from ctypes import wintypes as w
from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock, patch

from clipboard_hotkeys import PreparedContext, KeyboardEvent, monotonic
import test_clipboard_hotkeys as worker_tests
from tray_layouts import WindowsLayouts, LayoutTarget, GuiInfo
from typebridge_app import Settings


class ContextTests(unittest.TestCase):
    def fixture(self):
        router, api, _ = worker_tests.WorkerTests().fixture()
        router.settings = replace(Settings(), plain_paste=True, plain_paste_key='Ctrl+V')
        from clipboard_tools import clipboard_bindings
        router.bindings = clipboard_bindings(router.settings)
        router.accepting.set()
        return router

    def send(self, router):
        event = KeyboardEvent(vkCode=0x56)
        return router.handle(0, 0x100, ctypes.addressof(event))

    def test_callback_uses_prepared_target_without_capture_or_process_lookup(self):
        router = self.fixture()
        target = router.layouts.capture.return_value
        router.context_cache = PreparedContext(target, router.settings, 0, monotonic())
        with patch('clipboard_hotkeys.target_allowed', side_effect=AssertionError('process lookup in callback')) as allowed:
            self.assertEqual(self.send(router), 1)
        allowed.assert_not_called()
        router.layouts.capture.assert_not_called()
        router.notify.assert_called_once_with(('plain-paste', target))

    def test_expired_settings_generation_and_focus_changes_forward_original(self):
        for mode in ('expired', 'settings', 'generation', 'focus', 'missing'):
            with self.subTest(mode=mode):
                router = self.fixture()
                cache = PreparedContext(router.layouts.capture.return_value, router.settings, 0, monotonic())
                if mode == 'expired': cache = replace(cache, started=cache.started-1)
                if mode == 'settings': cache = replace(cache, settings=Settings())
                if mode == 'generation': cache = replace(cache, generation=99)
                if mode == 'focus': router.layouts.matches_foreground.return_value = False
                router.context_cache = None if mode == 'missing' else cache
                self.assertEqual(self.send(router), 0)
                router.notify.assert_not_called()
                self.assertFalse(router.suppressed)

    def test_blocked_metadata_reader_never_blocks_callback_or_keeps_old_cache(self):
        router = self.fixture()
        entered, release = threading.Event(), threading.Event()
        def capture():
            entered.set()
            release.wait(2)
            return object()
        router.layouts.capture.side_effect = capture
        router.context_cache = PreparedContext(object(), router.settings, 0, monotonic())
        worker = threading.Thread(target=router._refresh_context, args=(router.context_stop,))
        with patch('clipboard_hotkeys.target_allowed', return_value=True):
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.send(router), 0)
                self.assertTrue(worker.is_alive())
                router.layouts.capture.assert_called_once()
                self.assertIsNone(router.context_cache)
                router.context_stop.set()
            finally:
                release.set()
                worker.join(1)
        self.assertIsNone(router.context_cache)  # Late result cannot publish.

    def test_slow_or_denied_metadata_cannot_publish_fresh_authority(self):
        for mode in ('slow', 'denied', 'error', 'changed-settings'):
            with self.subTest(mode=mode):
                router = self.fixture()
                now = [0]
                def allowed(target, settings):
                    if mode == 'slow': now[0] = 1
                    if mode == 'error': raise OSError
                    if mode == 'changed-settings': router.context_generation += 1
                    return mode != 'denied'
                with (patch('clipboard_hotkeys.monotonic', side_effect=lambda: now[0]),
                      patch('clipboard_hotkeys.target_allowed', side_effect=allowed)):
                    router._refresh_context(router.context_stop)
                self.assertIsNone(router.context_cache)

    def test_hung_metadata_reader_cannot_be_duplicated_on_restart(self):
        router = self.fixture()
        entered, release = threading.Event(), threading.Event()
        def capture():
            entered.set()
            release.wait(3)
            return None
        router.layouts.capture.side_effect = capture
        try:
            router._start_context_reader()
            self.assertTrue(entered.wait(1))
            owner = router.context_worker
            router.context_stop.set()
            with self.assertRaises(OSError): router._start_context_reader()
            self.assertIs(router.context_worker, owner)
        finally:
            release.set()
            router.context_worker.join(1)

    def test_password_manager_and_user_exclusion_never_become_cached_permission(self):
        for name in ('keepass.exe', 'private-editor.exe'):
            router = self.fixture()
            router.settings = replace(router.settings, excluded_processes=('private-editor.exe',),
                                      excluded_categories=())
            router.layouts.capture.return_value = LayoutTarget(10, 11, 12, 13)
            with patch('psutil.Process') as process:
                process.return_value.name.return_value = name
                router._refresh_context(router.context_stop)
            self.assertIsNone(router.context_cache)
            self.assertEqual(self.send(router), 0)

    def test_context_that_expires_during_live_identity_check_is_not_used(self):
        router = self.fixture()
        router.context_cache = PreparedContext(object(), router.settings, 0, 0)
        now = [0]
        def slow_identity(target):
            now[0] = 1
            return True
        router.layouts.matches_foreground.side_effect = slow_identity
        with patch('clipboard_hotkeys.monotonic', side_effect=lambda: now[0]):
            self.assertEqual(self.send(router), 0)
        router.notify.assert_not_called()


class LiveIdentityTests(unittest.TestCase):
    def fixture(self):
        target = LayoutTarget(10, 11, 12, 13, 14)
        service = WindowsLayouts.__new__(WindowsLayouts)
        api = service.u = Mock()
        api.GetForegroundWindow.return_value = 10
        def identity(hwnd, ptr):
            ctypes.cast(ptr, ctypes.POINTER(w.DWORD)).contents.value = 14 if hwnd == 10 else 13
            return 15 if hwnd == 10 else 12
        api.GetWindowThreadProcessId.side_effect = identity
        def info(tid, ptr):
            ctypes.cast(ptr, ctypes.POINTER(GuiInfo)).contents.hwndFocus = 11
            return 1
        api.GetGUIThreadInfo.side_effect = info
        return service, target

    def test_exact_field_identity_matches_without_class_hkl_or_process_calls(self):
        service, target = self.fixture()
        self.assertTrue(service.matches_foreground(target))
        service.u.GetClassNameW.assert_not_called()
        service.u.GetKeyboardLayout.assert_not_called()
        service.u.SendMessageTimeoutW.assert_not_called()

    def test_changed_field_pid_or_foreground_is_refused(self):
        for mode in ('field', 'pid', 'foreground', 'unknown'):
            with self.subTest(mode=mode):
                service, target = self.fixture()
                if mode == 'field': target = replace(target, focus=20)
                if mode == 'pid': target = replace(target, pid=99)
                if mode == 'foreground': service.u.GetForegroundWindow.return_value = 30
                if mode == 'unknown': service.u.GetGUIThreadInfo.return_value = 0; service.u.GetGUIThreadInfo.side_effect = None
                self.assertFalse(service.matches_foreground(target))


if __name__ == '__main__':
    unittest.main()
