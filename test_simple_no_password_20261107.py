"""Simple mode: metadata protection intentionally inactive, no GUI/COM calls."""
import unittest
from unittest.mock import Mock, patch

from password_fields import PasswordFieldGuard, Snapshot


class DisabledPasswordGuardTests(unittest.TestCase):
    def test_disabled_guard_never_starts_thread_or_native_service(self):
        guard = PasswordFieldGuard(disabled=True, require_element_id=True,
                                   explicit_password_only=True)
        with patch('threading.Thread') as thread, \
             patch('uia_native_worker.get_uia_service') as com:
            guard.start()
            guard._watch_once()
        thread.assert_not_called()
        com.assert_not_called()
        self.assertIsNone(guard.worker)
        self.assertEqual(guard.snapshot, Snapshot())

    def test_disabled_guard_keeps_constant_revision_for_any_field(self):
        changed = Mock()
        guard = PasswordFieldGuard(changed, disabled=True)
        for top, focus in ((10, 11), (10, 12), (0, 0), (99, 100)):
            self.assertEqual(guard.permit_revision(top, focus), 0)
            self.assertTrue(guard.revalidate(top, focus, 0))
            self.assertFalse(guard.revalidate(top, focus, 1))
            guard.invalidate()
            guard.ingest('10|11|password|0|checked|uia:1,2|0|10')
        self.assertEqual(guard.snapshot, Snapshot())
        changed.assert_not_called()

    def test_disabled_guard_does_not_touch_existing_snapshot(self):
        guard = PasswordFieldGuard(disabled=True)
        snap = Snapshot()
        self.assertEqual(guard.snapshot, snap)
        guard.close()
        self.assertTrue(guard.stopped.is_set())
        self.assertEqual(guard.snapshot, snap)

    def test_strict_guard_unchanged_by_disabled_opt_in(self):
        strict = PasswordFieldGuard(require_element_id=True)
        self.assertFalse(strict.disabled)
        self.assertIsNone(strict.permit_revision(10, 11))
        strict._update(10, 11, 'password', element_id='uia:1,2', addressable=False)
        self.assertIsNone(strict.permit_revision(10, 11))
        strict.close()

    def test_explicit_password_legacy_guard_still_rejects_known_password(self):
        legacy = PasswordFieldGuard(explicit_password_only=True)
        self.assertFalse(legacy.disabled)
        legacy._update(10, 11, 'password')
        self.assertIsNone(legacy.permit_revision(10, 11))
        legacy.close()


if __name__ == '__main__':
    unittest.main()
