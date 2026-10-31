"""Default-on R04 scope hardening; Win32 mocked, no real hook or text read."""
import unittest
from types import MethodType
from unittest.mock import Mock

from r04_text_target import BROWSER_HOSTS, trusted_mutation_target
from test_app_controls import key, make_hook
from test_short_words_settings import HostedFocusTests
from typebridge_app import LiveHook, Settings, VK_SPACE


class R04DefaultTargetTests(unittest.TestCase):
    def test_browser_and_webview_hosts_always_deny_every_control(self):
        for browser in sorted(BROWSER_HOSTS):
            for name in ('Edit', 'RichEdit50W', 'Scintilla',
                         'Chrome_RenderWidgetHostHWND', 'WebView2', ''):
                with self.subTest(browser=browser, name=name):
                    self.assertFalse(trusted_mutation_target(browser, browser, name))
                    self.assertFalse(trusted_mutation_target('editor.exe', browser, name))
                    self.assertFalse(trusted_mutation_target(browser, 'editor.exe', name))

    def test_only_verified_native_text_or_notepad_plus_plus_scintilla_pass(self):
        for name in ('Edit', 'RichEdit20W', 'RichEdit50W', 'RichEditD2DPT'):
            with self.subTest(name=name):
                self.assertTrue(trusted_mutation_target('custom.exe', 'custom.exe', name))
        self.assertTrue(trusted_mutation_target('NOTEPAD++.EXE', 'notepad++.exe', 'Scintilla'))
        for process, host, name in (
                ('custom.exe', 'custom.exe', 'Scintilla'),
                ('notepad++.exe', 'other.exe', 'Scintilla'),
                ('custom.exe', 'custom.exe', 'CustomWidgetXYZ'),
                ('custom.exe', 'custom.exe', 'Chrome_WidgetWin_1'),
                ('custom.exe', 'custom.exe', 'Windows.UI.Core.CoreWindow'),
                ('', 'custom.exe', 'Edit'),
                ('custom.exe', '', 'Edit')):
            with self.subTest(process=process, host=host, name=name):
                self.assertFalse(trusted_mutation_target(process, host, name))

    def test_embedded_webview_upper_window_blocks_even_native_edit(self):
        for top_class in ('Chrome_WidgetWin_1', 'CefBrowserWindow',
                          'WebView2Root', 'MozillaWindowClass', 'IEFrame'):
            with self.subTest(top_class=top_class):
                self.assertFalse(trusted_mutation_target(
                    'vendorapp.exe', 'vendorapp.exe', 'Edit', top_class))

    @staticmethod
    def hooked_context(process, host, focus_class='Edit', style=0):
        fixture = HostedFocusTests().fixture()
        hook = make_hook()
        hook.user32 = fixture.user32
        hook.user32.CallNextHookEx.return_value = 0
        hook.process_name = lambda pid: host if pid == 100 else process
        hook.user32.GetClassNameW.side_effect = (
            lambda hwnd, buf, count: setattr(buf, 'value',
                                            'ApplicationFrameWindow' if hwnd == 10 else focus_class))
        hook.user32.GetWindowLongPtrW.return_value = style
        hook.context = MethodType(LiveHook.context, hook)
        hook.settings = Settings()
        hook._cycle_word = Mock()
        hook._switch_direct = Mock()
        hook._try_snippet = Mock()
        hook._correct = Mock()
        return hook

    def test_html_field_change_with_same_hwnd_cannot_trigger_edit_even_with_guard_off(self):
        hook = self.hooked_context('chrome.exe', 'chrome.exe')
        hook.password_guard = None
        for style in (0, 0x20, 0, 0x20):  # plain -> password -> plain, same HWND
            hook.user32.GetWindowLongPtrW.return_value = style
            self.assertIsNone(hook.context())
            for vk in (ord('A'), ord('K'), ord('U'), ord('O'), VK_SPACE, 0x13):
                self.assertEqual(key(hook, vk), 0)
        hook.user32.SendInput.assert_not_called()
        hook.user32.SendMessageTimeoutW.assert_not_called()
        hook._correct.assert_not_called()
        hook._cycle_word.assert_not_called()
        hook._switch_direct.assert_not_called()
        hook._try_snippet.assert_not_called()
        self.assertFalse(hook.keys)

    def test_explicit_safe_uia_snapshot_cannot_bypass_browser_exclusion(self):
        hook = self.hooked_context('chrome.exe', 'chrome.exe', 'Edit')
        hook.password_guard = Mock()
        hook.password_guard.permit_revision.return_value = 7
        self.assertIsNone(hook.context())
        hook.password_guard.permit_revision.assert_not_called()
        self.assertEqual(key(hook, ord('O')), 0)
        hook.user32.SendInput.assert_not_called()

    def test_custom_unknown_field_and_webview_host_native_edit_passthrough(self):
        for process, host, name in (
                ('custom.exe', 'custom.exe', 'WebView2'),
                ('custom.exe', 'msedgewebview2.exe', 'Edit'),
                ('msedgewebview2.exe', 'custom.exe', 'RichEdit50W'),
                ('custom.exe', 'custom.exe', 'CustomWidgetXYZ')):
            with self.subTest(process=process, name=name):
                hook = self.hooked_context(process, host, name)
                self.assertIsNone(hook.context())
                self.assertEqual(key(hook, ord('O')), 0)
                hook.user32.SendInput.assert_not_called()

    def test_native_edit_in_embedded_chromium_window_is_not_authorized(self):
        hook = self.hooked_context('vendorapp.exe', 'vendorapp.exe', 'Edit')
        hook.user32.GetClassNameW.side_effect = (
            lambda hwnd, buf, size: setattr(buf, 'value',
                    'Chrome_WidgetWin_1' if hwnd == 10 else 'Edit'))
        self.assertIsNone(hook.context())
        self.assertEqual(key(hook, ord('O')), 0)
        hook.user32.SendInput.assert_not_called()

    def test_native_plain_and_password_style_are_distinguished_without_uia(self):
        hook = self.hooked_context('notepad.exe', 'notepad.exe', 'RichEditD2DPT')
        self.assertIsNotNone(hook.context())
        hook.user32.GetWindowLongPtrW.return_value = 0x20
        self.assertIsNone(hook.context())
        self.assertEqual(key(hook, ord('O')), 0)


if __name__ == '__main__':
    unittest.main()
