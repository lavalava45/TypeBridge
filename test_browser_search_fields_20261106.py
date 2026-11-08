"""Browser/WebView search-field safety; no browser, OS input or global hooks."""
from dataclasses import replace
import unittest
from unittest.mock import Mock

from native_transaction_queue import PhysicalKey
from r04_text_target import native_field_metadata
from test_offhook_native import OffHookNativeTests
from typebridge_app import BROWSER_APPS, WM_KEYDOWN, VK_SPACE


class BrowserSearchFieldTests(unittest.TestCase):
    def fixture(self, browser='chrome.exe'):
        hook = OffHookNativeTests().fixture()
        source = replace(hook.context(), process_name=browser)
        hook.context = lambda: source
        hook._browser_field_revision = 17
        return hook, source

    def test_browser_family_keeps_element_identity_not_native_editor_shortcut(self):
        # A search control cannot bypass password/UIA verification just
        # because its HWND class looks like an ordinary Win32 edit.
        for browser in ('chrome.exe', 'msedge.exe', 'firefox.exe',
                        'brave.exe', 'vivaldi.exe'):
            with self.subTest(browser=browser):
                self.assertIn(browser, BROWSER_APPS)
                self.assertFalse(native_field_metadata(browser, browser, 'EDIT'))

    def test_browser_preflight_provider_error_is_scoped_not_global(self):
        # A provider failure BEFORE job() did not mutate a document.
        for browser in ('chrome.exe', 'msedge.exe', 'firefox.exe'):
            with self.subTest(browser=browser):
                hook, source = self.fixture(browser)
                hook.context = Mock(side_effect=RuntimeError('synthetic UIA failure'))
                job = Mock()
                self.assertTrue(hook._schedule_native(
                    source, job, trigger=hook._last_physical_event))
                hook.native_worker.join(2)
                self.assertFalse(hook.native_worker.is_alive())
                job.assert_not_called()
                hook.user32.SendInput.assert_not_called()
                self.assertEqual(hook.native_queue.aborted_reason,
                                 'worker-preflight-error')
                self.assertFalse(hook.native_edit_disabled)
                self.assertTrue(hook._native_blocked(source))
                self.assertFalse(hook._native_blocked(
                    replace(source, password_revision=source.password_revision + 1)))
                # If recovery is possible, it is explicit, never automatic.
                self.assertIsNotNone(hook.native_pending_recovery)

    def test_exception_after_entering_job_keeps_global_uncertain_quarantine(self):
        # Once an edit job was entered, an exception might follow a real
        # mutation. Never replay the delayed trigger or unblock all fields.
        hook, source = self.fixture()
        job = Mock(side_effect=RuntimeError('synthetic post-write exception'))
        self.assertTrue(hook._schedule_native(
            source, job, trigger=hook._last_physical_event))
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        job.assert_called_once()
        hook.user32.SendInput.assert_not_called()
        # The original trigger is irreversible; no synthetic replay occurs.
        # Independent following physical keys would still be replayed only
        # after an unchanged-target check, preserving real typing.
        self.assertFalse(hook.native_queue.busy)
        self.assertTrue(hook.native_edit_disabled)
        self.assertIsNone(hook.native_pending_recovery)

    def test_keyboard_context_change_before_job_is_local_and_recoverable(self):
        hook, source = self.fixture('msedge.exe')
        gate = hook.native_queue
        gate.begin(hook._last_physical_event, target_scope=hook._native_scope(source))
        gate.capture(PhysicalKey(0x41, 30, 0, WM_KEYDOWN))
        gate.abort('keyboard-context-changed')
        hook._drain_native_input(source, gate, hook._last_physical_event, False)
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(hook.native_edit_disabled)
        self.assertTrue(hook._native_blocked(source))
        self.assertFalse(hook._native_blocked(replace(source, focus=source.focus + 4)))
        self.assertIsNotNone(hook.native_pending_recovery)


if __name__ == '__main__':
    unittest.main()
