import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from word_recovery import recover_prefix
import test_word_cycle as cycle_tests
from test_app_controls import key
from password_fields import PasswordFieldGuard


class PrefixRecoveryTests(unittest.TestCase):
    """Old prefix reader stays isolated; live hook must never call it."""
    def setUp(self):
        self.driver = cycle_tests.WordCycleTests()
        self.driver.setUpClass()
        self.driver.setUp()

    def test_skipped_start_is_not_silently_recovered_from_document(self):
        hook = self.driver.hook
        hook.password_guard = guard = PasswordFieldGuard()
        original = hook.context
        def context():
            hook.protection_scope = (90, 100)
            return original() if guard.permit_revision(90, 100) is not None else None
        hook.context = context
        self.driver.type('gh')
        self.assertEqual(hook.keys, [])  # No password/unknown key prefix is retained.
        guard.ingest('90|100|safe')
        with patch('typebridge_app.recover_prefix', return_value=[(71, False), (72, False)]) as recover:
            self.driver.type('bdtn ')
        recover.assert_not_called()
        self.assertEqual(self.driver.text, 'ghbdtn ')

    def test_password_never_triggers_document_recovery(self):
        hook = self.driver.hook
        hook.password_guard = guard = PasswordFieldGuard()
        hook.protection_scope = (90, 100)
        hook.context = lambda: None
        guard.ingest('90|100|password')
        with patch('typebridge_app.recover_prefix') as recover:
            self.driver.type('ghbdtn ')
        recover.assert_not_called()
        self.assertEqual(self.driver.text, 'ghbdtn ')

    def test_shortcut_during_unknown_cannot_turn_into_a_recoverable_prefix(self):
        hook = self.driver.hook
        hook.context = lambda: None
        hook.protection_scope = (90, 100)
        hook.pressed = lambda vk: vk == 0x11
        key(hook, 65)
        hook.pressed = lambda vk: False
        key(hook, 8)
        key(hook, 66)
        self.assertFalse(hasattr(hook, 'skipped_prefix_length'))
        self.assertEqual(hook.keys, [])

    def test_skipped_first_word_is_not_recovered_before_space(self):
        from incremental_recognition import prepare_early
        hook = self.driver.hook
        hook.settings = replace(hook.settings, early_correction=True, early_native=True)
        prepare_early(hook.engine)
        hook.password_guard = guard = PasswordFieldGuard()
        original = hook.context
        def context():
            hook.protection_scope = (90, 100)
            return original() if guard.permit_revision(90, 100) is not None else None
        hook.context = context
        self.driver.type('gh')
        guard.ingest('90|100|safe')
        with patch('typebridge_app.recover_prefix', return_value=[(71, False), (72, False)]) as recover:
            self.driver.type('bdtn')
        recover.assert_not_called()
        self.assertEqual(self.driver.text, 'ghbdtn')

    def snapshot_case(self, snapshots, length=2, valid=lambda: True):
        def class_name(_hwnd, value, _size): value.value = 'RichEdit50W'
        api = Mock(); api.GetClassNameW.side_effect = class_name
        bridge = Mock(); bridge.snapshot.side_effect = snapshots
        adapter = Mock(); adapter.__enter__ = Mock(return_value=bridge); adapter.__exit__ = Mock(return_value=False)
        context = SimpleNamespace(focus=100, language='en', process_name='editor.exe')
        with patch('word_recovery.ctypes.WinDLL', return_value=api), \
                patch('word_recovery.NativeEditBridge', return_value=adapter):
            return recover_prefix(context, self.driver.engine.mapper, length, valid)

    def test_only_exact_collapsed_verified_prefix_is_accepted(self):
        snapshot = (b'hello gh', 8)
        self.assertEqual(self.snapshot_case([snapshot, snapshot]), [(71, False), (72, False)])
        self.assertIsNone(self.snapshot_case([snapshot], length=1))
        self.assertIsNone(self.snapshot_case([(b'ghx', 2)]))
        self.assertIsNone(self.snapshot_case([snapshot, (b'hello g', 7)]))
        self.assertIsNone(self.snapshot_case([], valid=lambda: False))


if __name__ == '__main__': unittest.main()
