"""Journal responsiveness and completion ordering, without GUI or editor I/O."""
from dataclasses import replace
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from input_journal import JournalService
from journal_io import JournalIo
from journal_gui import JournalWindow
from typebridge_app import Settings


class JournalIoTests(unittest.TestCase):
    def fixture(self):
        store = Mock()
        store.exists.return_value = False
        service = JournalService(store, Mock())
        service.worker = Mock()  # No editor capture thread in these tests.
        service.configure(replace(Settings(), journal_enabled=True))
        messages = queue.Queue()
        io = JournalIo(service, messages.put)
        self.addCleanup(lambda: (io.close(), io.finished.wait(2)))
        return io, service, messages

    def test_disable_returns_without_waiting_for_commit_but_ack_waits(self):
        io, service, messages = self.fixture()
        entered, release = threading.Event(), threading.Event()
        def commit():
            with service._commit_guard(service.generation) as allowed:
                self.assertTrue(allowed)
                entered.set()
                release.wait(3)
        thread = threading.Thread(target=commit)
        thread.start()
        self.addCleanup(lambda: (release.set(), thread.join(2)))
        self.assertTrue(entered.wait(2))
        done = Mock()
        identifier = io.submit('configure', Settings(), done)
        self.assertEqual(io.pending_config, identifier)
        self.assertFalse(service.enabled)
        self.assertTrue(messages.empty())
        done.assert_not_called()
        release.set()
        message = messages.get(timeout=2)
        done.assert_not_called()  # Worker does not invoke GUI callbacks.
        io.receive(message)
        done.assert_called_once()
        self.assertFalse(done.call_args.args[0]['enabled'])
        self.assertIsNone(io.pending_config)

    def test_stale_config_and_cancelled_text_do_not_reach_gui(self):
        io, service, messages = self.fixture()
        first, last = Mock(), Mock()
        with service.lock:
            io.submit('configure', Settings(), first)
            io.submit('configure', replace(Settings(), journal_enabled=True), last)
        while not last.called:
            io.receive(messages.get(timeout=2))
        first.assert_not_called()
        self.assertTrue(service.enabled)
        text = Mock()
        identifier = io.submit('text', 123, text)
        io.cancel(identifier)
        io.receive((identifier, 'text', 'synthetic secret', None))
        text.assert_not_called()

    def test_queue_is_bounded_and_close_never_waits_for_store(self):
        io, service, messages = self.fixture()
        entered, release = threading.Event(), threading.Event()
        service.store.text.side_effect = lambda _: (entered.set(), release.wait(3), '')[-1]
        self.addCleanup(release.set)
        io.jobs = queue.Queue(maxsize=1)
        io.submit('text', 1, Mock())
        self.assertTrue(entered.wait(2))
        io.submit('list', (200, 0), Mock())
        with self.assertRaises(OSError):
            io.submit('clear', None, Mock())
        with self.assertRaises(OSError):
            io.submit('configure', Settings(), Mock())
        self.assertFalse(service.enabled)
        io.close()
        self.assertTrue(service.stop_requested.is_set())
        self.assertFalse(io.finished.is_set())
        release.set()
        self.assertTrue(io.finished.wait(2))

    def test_refresh_invalidates_old_decrypted_selection(self):
        window = JournalWindow.__new__(JournalWindow)
        io = Mock()
        callbacks = []
        io.submit.side_effect = lambda kind, args, callback: callbacks.append(callback) or len(callbacks)
        window.app = SimpleNamespace(journal_io=io)
        window.window = Mock()
        window.list = Mock()
        window.list.selection.return_value = ('row',)
        window.list.get_children.return_value = ('row',)
        window.rows = {'row': 1}
        window.view_serial = window.text_serial = 0
        window.list_request = window.text_request = None
        window.operation_status = Mock()
        window.base_status = 'ready'
        window._display_text = Mock()
        window.select()
        old_reply = callbacks[0]
        window.refresh()
        window._display_text.reset_mock()
        old_reply('old secret', None)
        window._display_text.assert_not_called()
        io.cancel.assert_any_call(1)


if __name__ == '__main__':
    unittest.main()
