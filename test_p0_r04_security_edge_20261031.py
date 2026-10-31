"""R04 metadata boundary regressions; no hooks, UIA calls or foreground input.

These model tests do not prove that a browser provider reports every DOM focus
transition before a keyboard callback. The production GUI still opts out.
"""

import unittest
from unittest.mock import patch

from password_fields import PasswordFieldGuard


EPOCH_TICKS = 621355968000000000


def metadata(state, element, *, started=100.0, top=10, focus=20):
    ticks = EPOCH_TICKS + round(started * 10_000_000)
    return f'{top}|{focus}|{state}|{ticks}|checked|{element}'


class R04SecurityEdgeTests(unittest.TestCase):
    def setUp(self):
        self.now = 8.0
        self.guard = PasswordFieldGuard(
            clock=lambda: self.now, require_element_id=True)

    def ingest(self, state, element, *, started=100.0):
        with patch('password_fields.time.time', return_value=100.1):
            self.guard.ingest(metadata(state, element, started=started))

    def test_legacy_safe_and_invalid_element_identifiers_fail_closed(self):
        with patch('password_fields.time.time', return_value=100.1):
            samples = ('10|20|safe',
                       f'10|20|safe|{EPOCH_TICKS + 1000000000}|checked',
                       metadata('safe', ''), metadata('safe', 'uia:'),
                       metadata('safe', 'uia:1,a'),
                       metadata('safe', 'win32:21'),
                       metadata('safe', 'uia:' + '1' * 260))
            for line in samples:
                with self.subTest(line=line[-45:]):
                    self.guard.ingest(line)
                    self.assertIsNone(self.guard.permit_revision(10, 20))

    def test_same_hwnd_new_dom_element_changes_revision_and_password_denies(self):
        self.ingest('safe', 'uia:1,100')
        old_revision = self.guard.permit_revision(10, 20)
        self.assertIsNotNone(old_revision)
        self.ingest('safe', 'uia:1,100')
        self.assertEqual(self.guard.permit_revision(10, 20), old_revision)
        self.ingest('password', 'uia:1,101')
        self.assertIsNone(self.guard.permit_revision(10, 20))
        self.ingest('safe', 'uia:1,102')
        self.assertNotEqual(self.guard.permit_revision(10, 20), old_revision)

    def test_helper_response_started_before_click_is_discarded(self):
        self.ingest('safe', 'uia:1,100')
        with patch('password_fields.time.time', return_value=100.2):
            self.guard.invalidate()
        self.assertIsNone(self.guard.permit_revision(10, 20))
        with patch('password_fields.time.time', return_value=100.3):
            self.guard.ingest(metadata('safe', 'uia:1,101', started=100.1))
        self.assertIsNone(self.guard.permit_revision(10, 20))
        with patch('password_fields.time.time', return_value=100.4):
            self.guard.ingest(metadata('safe', 'uia:1,101', started=100.3))
        self.assertIsNotNone(self.guard.permit_revision(10, 20))

    def test_metadata_age_counts_from_query_start_not_delivery(self):
        self.ingest('safe', 'uia:1,100', started=99.7)
        self.assertIsNotNone(self.guard.permit_revision(10, 20))
        self.now += 0.11  # 0.4 s query age + 0.11 elapsed exceeds 0.5 s TTL
        self.assertIsNone(self.guard.permit_revision(10, 20))
        self.assertEqual(self.guard.snapshot.state, 'unknown')

    def test_delayed_and_future_queries_cannot_reauthorize(self):
        for started in (99.0, 100.3):
            with self.subTest(started=started):
                self.ingest('safe', 'uia:1,100', started=started)
                self.assertIsNone(self.guard.permit_revision(10, 20))
                self.assertEqual(self.guard.snapshot.reason, 'stale-helper-response')


if __name__ == '__main__':
    unittest.main()
