"""Exclude navigation controls without excluding browsers by application name.

Mock the real Win32 context() path only; no global hook, foreground changes,
or physical keystrokes are used by these tests.
"""
import unittest

import test_short_words_settings as focus_fixtures


class NonTextFocusTests(unittest.TestCase):
    @staticmethod
    def focus(control_class, process='explorer.exe'):
        hook = focus_fixtures.HostedFocusTests().fixture()
        hook.process_name = lambda pid: process
        hook.user32.GetClassNameW.side_effect = (
            lambda hwnd, output, size: setattr(
                output, 'value', 'CabinetWClass' if hwnd == 10 else control_class))
        # Real GetWindowLongPtrW returns a numeric style; unconfigured Mock
        # cannot be masked with ES_PASSWORD when RichEdit is in scope.
        hook.user32.GetWindowLongPtrW.return_value = 0
        return hook

    def test_common_navigation_control_classes_are_rejected(self):
        for name in ('SysListView32', 'SysTreeView32', 'SHELLDLL_DefView',
                     'DirectUIHWND', 'ToolbarWindow32'):
            with self.subTest(focus=name):
                hook = self.focus(name)
                self.assertIsNone(hook.context())

    def test_explorer_window_and_desktop_surfaces_are_rejected(self):
        for name in ('CabinetWClass', 'ExploreWClass', 'Shell_TrayWnd',
                     'Progman', 'WorkerW'):
            with self.subTest(focus=name):
                self.assertIsNone(self.focus(name).context())

    def test_real_edit_class_remains_eligible_for_filename_rename(self):
        hook = self.focus('Edit')
        hook.user32.GetWindowLongPtrW.return_value = 0
        self.assertEqual(hook.context().process_name, 'explorer.exe')

    def test_native_rich_edit_and_notepad_plus_plus_scintilla_remain_eligible(self):
        for name, process in (('RichEditD2DPT', 'notepad.exe'),
                              ('Scintilla', 'notepad++.exe')):
            with self.subTest(focus=name):
                hook = self.focus(name, process)
                self.assertIsNotNone(hook.context())

    def test_browser_widgets_are_not_globally_excluded_without_metadata_guard(self):
        for name, process in (('Chrome_RenderWidgetHostHWND', 'chrome.exe'),
                              ('MozillaWindowClass', 'firefox.exe'),
                              ('Edit', 'msedge.exe'),
                              ('Edit', 'msedgewebview2.exe'),
                              ('Chrome_WidgetWin_1', 'custom-app.exe')):
            with self.subTest(focus=name, process=process):
                hook = self.focus(name, process)
                self.assertIsNotNone(hook.context())

    def test_existing_password_edit_rejection_remains(self):
        hook = self.focus('Edit')
        hook.user32.GetWindowLongPtrW.return_value = 0x20
        self.assertIsNone(hook.context())

    def test_unknown_custom_control_is_not_automatically_certified_text_safe(self):
        # Eligibility is checked per element by the production metadata guard.
        hook = self.focus('CustomWidgetXYZ', 'custom-app.exe')
        self.assertIsNotNone(hook.context())


if __name__ == '__main__':
    unittest.main()
