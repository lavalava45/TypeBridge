"""Real service worker lifecycle with mocked clipboard/Win32 (no user I/O)."""
import threading
import time
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clipboard_tools import ClipboardTools, NativeClipboard


class PlainPasteWorkerTests(unittest.TestCase):
    def fixture(self):
        native = NativeClipboard.__new__(NativeClipboard)
        native.prepare_plain_rollback = Mock()
        native.sequence = Mock(return_value=20)
        native.write_plain = Mock(return_value=21)
        owner, notify = Mock(), Mock()
        owner.winfo_id.return_value = 987
        service = ClipboardTools(owner, notify, native=native)
        service.generation = 5
        service.pending = True
        target = SimpleNamespace(top=10, focus=20, pid=30)
        return service, native, target

    def test_blocked_native_backup_never_blocks_gui_and_close_prevents_write(self):
        service, native, target = self.fixture()
        entered, release = threading.Event(), threading.Event()
        backup = Mock(sequence=20)

        def blocked_backup(*_):
            entered.set()
            release.wait(2)
            return backup

        native.prepare_plain_rollback.side_effect = blocked_backup
        layouts = Mock()
        layouts.valid.return_value = True
        layouts.capture.return_value = target
        try:
            with (patch('tray_layouts.WindowsLayouts', return_value=layouts),
                  patch('clipboard_tools.target_allowed', return_value=True),
                  patch('win32gui.GetWindowLong', return_value=0),
                  patch('win32api.GetAsyncKeyState', return_value=0)):
                started = time.monotonic()
                service._paste_when_released(target, 'example', started, 20, 5)
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(entered.wait(1))
                service.close()
                release.set()
                service._paste_worker.join(2)
        finally:
            release.set()
        self.assertFalse(service._paste_worker.is_alive())
        native.write_plain.assert_not_called()
        backup.close.assert_called_once_with()

    def test_one_outstanding_paste_worker_no_thread_fanout(self):
        service, native, target = self.fixture()
        entered, release = threading.Event(), threading.Event()
        native.prepare_plain_rollback.side_effect = lambda *_: (entered.set(), release.wait(2), Mock(sequence=20))[2]
        layouts = Mock()
        layouts.valid.return_value = True
        layouts.capture.return_value = target
        try:
            with (patch('tray_layouts.WindowsLayouts', return_value=layouts),
                  patch('clipboard_tools.target_allowed', return_value=True),
                  patch('win32gui.GetWindowLong', return_value=0),
                  patch('win32api.GetAsyncKeyState', return_value=0)):
                now = time.monotonic()
                service._paste_when_released(target, 'example', now, 20, 5)
                self.assertTrue(entered.wait(1))
                first = service._paste_worker
                service._paste_when_released(target, 'duplicate', now, 20, 5)
                self.assertIs(service._paste_worker, first)
                self.assertEqual(native.prepare_plain_rollback.call_count, 1)
                service.close()
                release.set()
                first.join(2)
        finally:
            release.set()
        self.assertFalse(first.is_alive())
        native.write_plain.assert_not_called()

    def test_previous_worker_finishing_cannot_orphan_new_paste_pending(self):
        # Old thread may have set pending=False before its finally/close()
        # completes. A new request must not enter the queue and then be
        # silently discarded when its worker sees the previous one alive.
        service, native, target = self.fixture()
        service.pending = False
        service.settings = SimpleNamespace(plain_paste=True)
        service._paste_worker = Mock()
        service._paste_worker.is_alive.return_value = True
        with self.assertRaisesRegex(OSError, 'ещё завершается'):
            service.request_plain(target)
        self.assertFalse(service.pending)
        self.assertTrue(service.requests.empty())

    def test_stale_cancel_and_error_cannot_cancel_newer_paste(self):
        service, native, target = self.fixture()
        service.settings = SimpleNamespace(plain_paste=True)
        service._pending_paste_token = 7
        service._paste_serial = 7
        # An old request can report after configure() or after the next
        # explicit paste has been queued, including the same generation.
        service.receive(('cancelled', 4, 6))
        service.receive(('paste-error', 'old failure', 5, 6))
        service.receive(('prepared', 5, target, time.monotonic(), 20, 'old', 6))
        self.assertTrue(service.pending)
        self.assertEqual(service._pending_paste_token, 7)
        service.notify.assert_not_called()
        native.write_plain.assert_not_called()
        with self.assertRaisesRegex(OSError, 'current failure'):
            service.receive(('paste-error', 'current failure', 5, 7))
        self.assertFalse(service.pending)
        self.assertIsNone(service._pending_paste_token)

    def test_old_cancel_from_prior_configuration_does_not_clear_new_request(self):
        service, native, target = self.fixture()
        service.settings = SimpleNamespace(plain_paste=True)
        service._pending_paste_token = 11
        service.pending = True
        service.generation = 8
        service.receive(('cancelled', 7, 10))
        self.assertTrue(service.pending)
        self.assertEqual(service._pending_paste_token, 11)

    def test_quick_search_copy_refuses_during_paste_cleanup(self):
        service, native, target = self.fixture()
        service.pending = False  # SendInput finished; rollback.close still active.
        service._paste_worker = Mock()
        service._paste_worker.is_alive.return_value = True
        with self.assertRaisesRegex(Exception, 'занят'):
            with service.selection_copy():
                self.fail('Clipboard transactions must not overlap')
        self.assertFalse(service.history_paused)

    def test_actual_reader_to_gui_dispatch_keeps_request_identity(self):
        # Exercise the real queue and _reader() through receive(). Native
        # clipboard operations and text mutation remain fully mocked.
        messages = queue.Queue()
        native = Mock()
        native.sequence.return_value = 17
        native.read.return_value = (17, 'synthetic')
        service = ClipboardTools(Mock(), messages.put, native=native)
        service.configure(SimpleNamespace(plain_paste=True, clipboard_history=False))
        target = SimpleNamespace(top=10, focus=20, pid=30)
        with patch.object(service, '_paste_when_released') as send:
            try:
                service.request_plain(target)
                message = messages.get(timeout=1)
                self.assertEqual(message[0], 'prepared')
                self.assertEqual(message[6], service._pending_paste_token)
                service.receive(message)
                send.assert_called_once()
                self.assertEqual(send.call_args.kwargs['_request_token'],
                                 message[6])
                # Simulate successful completion of the first operation.
                service.pending = False
                service._pending_paste_token = None
                service.request_plain(target)
                newest = messages.get(timeout=1)
                self.assertNotEqual(message[6], newest[6])
                # A late error and a late prepared message from request 1
                # must not alter request 2 or start another write.
                service.receive(('paste-error', 'old error',
                                 service.generation, message[6]))
                service.receive(message)
                self.assertTrue(service.pending)
                self.assertEqual(service._pending_paste_token, newest[6])
                send.assert_called_once()
                service.receive(newest)
                self.assertEqual(send.call_count, 2)
            finally:
                service.close()

    def test_stuck_clipboard_read_expires_without_unbounded_queue_or_gui_shutdown(self):
        entered, release = threading.Event(), threading.Event()
        messages = queue.Queue()
        native = Mock()
        native.sequence.return_value = 17
        def stalled_read(*args, **kwargs):
            entered.set()
            release.wait(2)
            return 17, 'synthetic text'
        native.read.side_effect = stalled_read
        service = ClipboardTools(Mock(), messages.put, native=native)
        service.configure(SimpleNamespace(plain_paste=True, clipboard_history=False))
        target = SimpleNamespace(top=10, focus=20, pid=30)
        try:
            service.request_plain(target)
            self.assertTrue(entered.wait(1))
            service._pending_started -= 3  # Deterministic virtual deadline.
            self.assertTrue(service.expire_pending())
            self.assertFalse(service.expire_pending())  # No repeated UI errors.
            timeout = messages.get(timeout=.5)
            self.assertEqual(timeout[0], 'paste-error')
            with self.assertRaisesRegex(OSError, 'время ожидания'):
                service.receive(timeout)
            self.assertFalse(service.pending)
            service.request_plain(target)  # One bounded queued request.
            self.assertEqual(service.requests.qsize(), 1)
            service._pending_started -= 3
            self.assertTrue(service.expire_pending())
            with self.assertRaises(OSError):
                service.receive(messages.get(timeout=.5))
            with self.assertRaisesRegex(OSError, 'занят предыдущей операцией'):
                service.request_plain(target)  # Refuses unbounded fanout.
            started = time.monotonic()
            service.close()  # Must not block on a full queue/native API.
            self.assertLess(time.monotonic() - started, .3)
        finally:
            release.set()
            if service.worker is not None:
                service.worker.join(2)
        native.write_plain.assert_not_called()
        self.assertFalse(service.pending)


if __name__ == '__main__':
    unittest.main()
