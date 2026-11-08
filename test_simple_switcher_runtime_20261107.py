"""Switcher-only mode tests: no global hook, live fields or real keystrokes."""

import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from test_app_controls import make_hook
import test_field_states_20261106 as field_fixtures
import test_hook_cycle_offhook as cycle_fixtures
import test_offhook_native as native_fixtures
import test_browser_selected_pause_20261107 as browser_fixtures
from native_transaction_queue import PhysicalKey
from typebridge_app import (SIMPLE_SWITCHER_MODE, Settings,
                            staged_password_guard, VK_SPACE, WM_KEYDOWN)


class SimpleSwitcherRuntimeTests(unittest.TestCase):
    def test_runtime_mode_is_active_while_legacy_guard_remains_available(self):
        self.assertTrue(SIMPLE_SWITCHER_MODE)
        legacy = staged_password_guard(Settings())
        try:
            self.assertFalse(legacy.disabled)
            self.assertTrue(legacy.explicit_password_only)
        finally:
            legacy.close()
        disabled = staged_password_guard(Settings(), simple=True)
        self.assertTrue(disabled.disabled)
        self.assertEqual(disabled.permit_revision(10, 11), 0)
        disabled.close()

    def test_explicit_password_metadata_and_win32_password_style_do_not_block_context(self):
        from typebridge_app import ES_PASSWORD
        harness = field_fixtures.FieldStateMatrixTests()
        hook, guard = harness.fixture(False, process='chrome.exe', cls='Edit', style=ES_PASSWORD)
        self.assertIsNone(hook.context())
        hook.simple_mode = True
        self.assertIsNotNone(hook.context())
        guard.ingest(field_fixtures.sample('uia:4,9', 'password'))
        self.assertIsNotNone(hook.context())

    def test_default_password_manager_exclusion_is_inactive_in_simple_mode(self):
        harness = field_fixtures.FieldStateMatrixTests()
        hook, guard = harness.fixture(False, process='keepass.exe')
        self.assertIsNone(hook.context())
        hook.simple_mode = True
        self.assertIsNotNone(hook.context())

    def test_quarantine_is_inert_for_global_and_scoped_failures(self):
        hook = make_hook()
        hook.simple_mode = True
        hook.native_edit_disabled = False
        hook.native_early_disabled = False
        hook.native_blocked_scopes = set()
        context = hook.context()
        for reason in ('input-barrier-lost', 'worker-error', 'uncertain-write'):
            hook._quarantine_native(reason, context)
            self.assertFalse(hook.native_edit_disabled)
            self.assertFalse(hook.native_early_disabled)
            self.assertFalse(hook.native_blocked_scopes)
            self.assertFalse(hook._native_blocked(context))
        self.assertFalse(any(c.args[0].startswith('transport-quarantine')
                             for c in hook.emit.call_args_list))

    def test_aborted_input_does_not_create_pending_recovery_or_disable_next_job(self):
        fixture = native_fixtures.OffHookNativeTests()
        hook = fixture.fixture()
        hook.simple_mode = True
        context = hook.context()
        gate = hook.native_queue
        trigger = PhysicalKey(VK_SPACE, 57, 0, WM_KEYDOWN, True)
        self.assertTrue(gate.begin(trigger, target_scope=hook._native_scope(context)))
        gate.capture(PhysicalKey(0x41, 30, 0, WM_KEYDOWN))
        gate.abort('focus-changed')
        hook._drain_native_input(context, gate, trigger, False)
        self.assertIsNone(hook.native_pending_recovery)
        self.assertFalse(hook.native_edit_disabled)
        self.assertFalse(hook._native_blocked(context))
        self.assertTrue(gate.begin(target_scope=hook._native_scope(context)))
        gate.close()

    def test_pause_uses_remembered_word_without_uia_read_or_password_guard(self):
        harness = cycle_fixtures.GenericCycleOffHookTests()
        hook, context = harness.fixture()
        # This fixture patches read_caret_word, so assert no new native read
        # in switcher-only mode. No live user content is accessed.
        hook.simple_mode = True
        hook.password_guard = None
        try:
            with patch('typebridge_app.read_caret_word', side_effect=AssertionError('UIA must be inactive')):
                hook._cycle_word(context)
                harness.release(hook)
        finally:
            harness.doCleanups()
        self.assertEqual((hook.last_word.text, hook.last_word.language), ('שלום', 'he'))
        self.assertFalse(hook.native_edit_disabled)

    def test_chrome_selected_pause_requires_no_password_guard(self):
        harness = browser_fixtures.ChromeSelectedPauseTests()
        hook, context, doc, _mapper, selected = harness.fixture()
        hook.simple_mode = True
        hook.password_guard = None
        selected = replace(selected, simple_mode=True)
        try:
            with patch('typebridge_app.read_selected_word', return_value=selected) as reader, \
                 patch('typebridge_app.verify_selected_word', return_value=True) as verified:
                hook._cycle_word(context)
            self.assertEqual(doc.text, 'PREFIX בית SUFFIX')
            self.assertEqual(hook.last_language, 'he')
            self.assertEqual(doc.attempts, [6])
            self.assertTrue(reader.call_args.kwargs['simple_mode'])
            self.assertEqual(reader.call_args.args[2], '')
            self.assertTrue(verified.called)
        finally:
            harness.doCleanups()

    def test_chrome_changed_selection_still_avoids_mutating_other_text(self):
        harness = browser_fixtures.ChromeSelectedPauseTests()
        hook, context, doc, _mapper, selected = harness.fixture()
        hook.simple_mode = True
        hook.password_guard = None
        selected = replace(selected, simple_mode=True)
        original = doc.text
        try:
            with patch('typebridge_app.read_selected_word', return_value=selected), \
                 patch('typebridge_app.verify_selected_word', return_value=False):
                hook._cycle_word(context)
            self.assertEqual(doc.text, original)
            self.assertEqual(doc.attempts, [])
        finally:
            harness.doCleanups()


if __name__ == '__main__':
    unittest.main()
