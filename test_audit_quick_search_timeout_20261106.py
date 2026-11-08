"""Quick Search timeout/ownership tests; only synthetic readers and targets."""
from dataclasses import replace
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from clipboard_selection import SelectionUnavailable
from quick_search import QuickSearch
from typebridge_app import Settings
from tray_layouts import LayoutTarget


class QuickSearchTimeoutTests(unittest.TestCase):
    def fixture(self, reader):
        target = LayoutTarget(10, 11, 12, 13)
        settings = replace(Settings(), quick_search=True)
        layouts, opener, reports = Mock(), Mock(return_value=True), queue.Queue()
        layouts.valid.return_value = True
        layouts.capture.return_value = target
        return target, settings, layouts, opener, reports, QuickSearch(
            reports.put, reader=reader, opener=opener)

    def test_hung_reader_expires_and_new_read_can_finish_without_stale_reply(self):
        entered, release = threading.Event(), threading.Event()
        calls = [0]
        def reader(_):
            calls[0] += 1
            if calls[0] == 1:
                entered.set()
                release.wait(2)
            return 'test'
        target, settings, layouts, opener, reports, service = self.fixture(reader)
        with patch('app_exclusions.target_allowed', return_value=True), \
             patch('quick_search.REQUEST_TIMEOUT', 0.07):
            service.request(target, settings, layouts)
            self.assertTrue(entered.wait(.5))
            time.sleep(.09)
            self.assertTrue(service.expire_pending())
            timeout = reports.get(timeout=.5)
            self.assertEqual(len(timeout), 6)
            self.assertIn('истёк срок', service.receive(timeout, settings, layouts))
            self.assertFalse(service.busy)
            service.request(target, settings, layouts)
            second = reports.get(timeout=.5)
            self.assertEqual(service.receive(second, settings, layouts), 'Поиск открыт в браузере')
            self.assertEqual(calls[0], 2)
            release.set()
            late = reports.get(timeout=.5)
            self.assertIsNone(service.receive(late, settings, layouts))
            self.assertEqual(opener.call_count, 1)
            self.assertFalse(service.busy)

    def test_configure_invalidates_old_worker_without_reusing_token(self):
        entered, release = threading.Event(), threading.Event()
        def reader(_):
            entered.set()
            release.wait(1)
            return 'test'
        target, settings, layouts, opener, reports, service = self.fixture(reader)
        with patch('app_exclusions.target_allowed', return_value=True):
            service.request(target, settings, layouts)
            self.assertTrue(entered.wait(.5))
            service.configure()
            self.assertFalse(service.busy)
            self.assertIsNone(service.request_deadline)
            release.set()
            old = reports.get(timeout=.5)
            self.assertIsNone(service.receive(old, settings, layouts))
            opener.assert_not_called()

    def test_previous_copy_owner_cannot_overlap_new_copy(self):
        target, settings, layouts, opener, reports, service = self.fixture(lambda _: 'unused')
        with patch('app_exclusions.target_allowed', return_value=True):
            with patch('quick_search.REQUEST_TIMEOUT', .04):
                service.request(target, settings, layouts)
                # Consume a completed synthetic read; no browser needed.
                result = reports.get(timeout=.5)
                service.receive(result, settings, layouts)
                service._copy_lock.acquire()
                try:
                    service.request(target, settings, layouts)
                    token = service._active_token
                    with self.assertRaises(SelectionUnavailable):
                        with service.guarded_copy(service.generation, token):
                            self.fail('Overlapping clipboard access')
                finally:
                    service._copy_lock.release()
                result = reports.get(timeout=.5)
                service.receive(result, settings, layouts)

    def test_worker_capacity_prevents_unbounded_abandoned_threads(self):
        entered = threading.Event()
        release = threading.Event()
        def reader(_):
            entered.set()
            release.wait(2)
            return 'test'
        target, settings, layouts, opener, reports, service = self.fixture(reader)
        with patch('app_exclusions.target_allowed', return_value=True), \
             patch('quick_search.REQUEST_TIMEOUT', .03):
            for _ in range(2):
                service.request(target, settings, layouts)
                self.assertTrue(entered.wait(.5))
                time.sleep(.04)
                self.assertTrue(service.expire_pending())
                service.receive(reports.get(timeout=.5), settings, layouts)
            self.assertEqual(len(service._workers), 2)
            service.request(target, settings, layouts)
            result = reports.get(timeout=.5)
            self.assertIn('предыдущие запросы', service.receive(result, settings, layouts))
            self.assertEqual(len(service._workers), 2)
            release.set()


if __name__ == '__main__':
    unittest.main()
