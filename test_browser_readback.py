"""Browser readback isolation and cancellation; no desktop input or UIA reads."""
import queue
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from browser_readback import BrowserReadback, CARET_SCRIPT, ReadbackFailure
from test_app_controls import make_hook
from typebridge_app import TypeBridgeGui
from word_trace import safe_word_event
import json


class BrowserReadbackTests(unittest.TestCase):
    def fixture(self):
        hook = make_hook()
        hook.debug_words = True
        hook._browser_field_revision = 0
        ctx = replace(hook.context(), process_name='chrome.exe')
        hook.context = lambda: ctx
        notify = Mock()
        service = BrowserReadback(notify, clock=lambda: 10)
        service.reader = Mock()
        service.reader.read.return_value = {'state': 'sampled', 'observed': 'яблокко'}
        job = (hook, ctx, {'source': 'z,kjrj', 'replacement': 'яблоко', 'phase': 'early'}, 0, 9.8)
        return hook, service, notify, job

    def test_sample_is_bounded_and_records_delayed_read_not_verified_edit(self):
        hook, service, notify, job = self.fixture()
        service.reader.read.return_value['observed'] = 'a' * 80 + 'яблокко'
        service._read_job(job)
        result = notify.call_args.args[0][2]
        self.assertEqual(result['state'], 'sampled')
        self.assertTrue(result['observed'].endswith('яблокко'))
        self.assertEqual(len(result['observed']), 60)
        self.assertEqual(result['replacement'], 'яблоко')
        self.assertIn('delay_ms', result)
        self.assertNotIn('verified', result['state'])
        self.assertEqual(json.loads(safe_word_event({'event': 'browser-readback', **result}))['observed'], result['observed'])
        hook.user32.SendInput.assert_not_called()

    def test_changed_focus_never_reads_new_target(self):
        hook, service, notify, job = self.fixture()
        hook.context = lambda: replace(job[1], focus=999)
        service._read_job(job)
        service.reader.read.assert_not_called()
        self.assertEqual(notify.call_args.args[0][2]['state'], 'skipped')

    def test_mouse_revision_cancels_even_with_same_browser_hwnd(self):
        hook, service, notify, job = self.fixture()
        hook._browser_field_revision = 1
        service._read_job(job)
        service.reader.read.assert_not_called()

    def test_focus_changed_during_read_discards_returned_text(self):
        hook, service, notify, job = self.fixture()
        def read(_context):
            hook._browser_field_revision += 1
            return {'state': 'sampled', 'observed': 'must not log this'}
        service.reader.read.side_effect = read
        service._read_job(job)
        result = notify.call_args.args[0][2]
        self.assertNotIn('observed', result)
        self.assertEqual(result['reason'], 'context-changed-during-read')

    def test_timeout_closes_only_helper_and_allows_recovery(self):
        hook, service, notify, job = self.fixture()
        reader = service.reader
        reader.read.side_effect = ReadbackFailure('helper-timeout')
        service._read_job(job)
        reader.close.assert_called_once()
        self.assertIsNone(service.reader)
        self.assertEqual(notify.call_args.args[0][2]['reason'], 'helper-timeout')
        self.assertFalse(getattr(hook, 'native_edit_disabled', False))
        service.clock = lambda: 13
        service.reader_factory = Mock(return_value=Mock())
        service.reader_factory.return_value.read.return_value = {'state': 'sampled', 'observed': 'яблоко'}
        fresh = (*job[:4], 12.8)
        service._read_job(fresh)
        self.assertEqual(notify.call_args.args[0][2]['observed'], 'яблоко')

    def test_stale_jobs_do_not_read_current_text(self):
        _, service, notify, job = self.fixture()
        service._read_job((*job[:4], 5))
        service.reader.read.assert_not_called()
        self.assertEqual(notify.call_args.args[0][2]['reason'], 'readback-request-stale')

    def test_unsupported_or_password_field_records_status_without_text(self):
        for reason in ('password-field', 'text-pattern-unavailable', 'selection-not-collapsed'):
            _, service, notify, job = self.fixture()
            service.reader.read.return_value = {'state': 'skipped', 'reason': reason}
            service._read_job(job)
            self.assertNotIn('observed', notify.call_args.args[0][2])

    def test_full_queue_does_not_block_input_thread(self):
        _, service, notify, job = self.fixture()
        for _ in range(5):
            service.request(*job)
        self.assertEqual(service.jobs.qsize(), 4)
        self.assertEqual(notify.call_args.args[0][2]['reason'], 'readback-queue-full')

    def test_hook_only_queues_job_and_disabled_flag_is_silent(self):
        hook, _, _, job = self.fixture()
        hook._queue_browser_readback(job[1], 'z,kjrj', 'яблоко', 'early')
        self.assertEqual(hook.emit.call_args.args[0], 'browser-readback-request')
        hook.debug_words = False
        hook.emit.reset_mock()
        hook._queue_browser_readback(job[1], 'z,kjrj', 'яблоко', 'early')
        hook.emit.assert_not_called()

    def test_gui_drain_schedules_read_without_running_provider(self):
        hook, service, _, job = self.fixture()
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.hook, gui.closing, gui.worker, gui.root = hook, False, None, Mock()
        gui._browser_readback = SimpleNamespace(request=Mock())
        gui.events = queue.Queue()
        gui.events.put(('browser-readback-request', job))
        with patch('typebridge_app.DEBUG_WORDS_ENABLED', True):
            gui._drain()
        gui._browser_readback.request.assert_called_once_with(*job)
        service.reader.read.assert_not_called()

    def test_helper_script_only_reads_caret_range(self):
        self.assertIn('GetText(60)', CARET_SCRIPT)
        self.assertIn('GetSelection()', CARET_SCRIPT)
        self.assertIn('IsPassword', CARET_SCRIPT)
        self.assertIn('Check-Window', CARET_SCRIPT)
        for forbidden in ('.Select(', 'ValuePattern', 'Clipboard', 'SetFocus', 'SendInput', 'DocumentRange'):
            self.assertNotIn(forbidden, CARET_SCRIPT)

    def test_worker_reads_and_cancels_helper_on_shutdown(self):
        _, service, _, job = self.fixture()
        reader = service.reader
        service.reader_factory = lambda: reader
        done = threading.Event()
        results = []
        service.notify = lambda value: (results.append(value), done.set())
        try:
            service.start()
            service.request(*job)
            self.assertTrue(done.wait(1))
            self.assertEqual(results[0][2]['observed'], 'яблокко')
        finally:
            service.close()
            service.worker.join(1)
        self.assertFalse(service.worker.is_alive())
        reader.cancel.assert_called_once()
        reader.close.assert_called_once()

    def test_gui_logs_returned_sample_only_when_debug_flag_is_enabled(self):
        hook, _, _, job = self.fixture()
        for enabled in (False, True):
            gui = TypeBridgeGui.__new__(TypeBridgeGui)
            gui.hook, gui.closing, gui.worker, gui.root = hook, False, None, Mock()
            gui.events = queue.Queue()
            details = {'state': 'sampled', 'observed': 'яблокко'}
            gui.events.put(('browser-readback-result', (hook, job[1], details)))
            hook.emit.reset_mock()
            with patch('typebridge_app.DEBUG_WORDS_ENABLED', enabled):
                gui._drain()
            if enabled:
                self.assertEqual(hook.emit.call_args.args[1]['observed'], 'яблокко')
            else:
                hook.emit.assert_not_called()

    def test_real_mouse_callback_invalidates_same_hwnd_sample(self):
        hook, service, notify, job = self.fixture()
        hook.mouse_hook = None
        hook.handle_mouse(0, 0x0201, 0)
        service._read_job(job)
        service.reader.read.assert_not_called()
        self.assertEqual(notify.call_args.args[0][2]['reason'], 'context-changed-before-read')


if __name__ == '__main__':
    unittest.main()
