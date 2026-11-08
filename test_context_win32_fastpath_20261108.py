"""Context fast path: fewer Win32 reads, unchanged focus/HKL admission.

Mock-only tests; never install a hook, type keys or modify foreground windows.
"""

from dataclasses import replace
import unittest

from test_short_words_settings import HostedFocusTests
from tray_layouts import layout_owner


class ContextWin32FastpathTests(unittest.TestCase):
    @staticmethod
    def hook():
        hook = HostedFocusTests().fixture()
        hook.simple_mode = True
        return hook

    def test_default_mode_skips_unused_title_and_duplicate_class_queries(self):
        hook = self.hook()
        context = hook.context()
        self.assertIsNotNone(context)
        self.assertEqual((context.focus, context.thread_id, context.language),
                         (20, 22, 'en'))
        self.assertEqual(context.title, '')
        hook.user32.GetWindowTextW.assert_not_called()
        self.assertEqual(hook.user32.GetClassNameW.call_count, 2)
        self.assertEqual([call.args[0] for call in hook.user32.GetClassNameW.call_args_list],
                         [20, 10])

    def test_user_title_filter_still_reads_and_checks_title(self):
        hook = self.hook()
        hook.settings = replace(hook.settings, only_window_title='eDiToR')
        hook.user32.GetWindowTextW.side_effect = (
            lambda hwnd, buf, size: setattr(buf, 'value', 'Alpha Editor'))
        context = hook.context()
        self.assertIsNotNone(context)
        self.assertEqual(context.title, 'Alpha Editor')
        hook.user32.GetWindowTextW.assert_called_once()
        self.assertEqual(hook.user32.GetClassNameW.call_count, 2)

    def test_title_filter_refuses_mismatched_window_before_reading_classes(self):
        hook = self.hook()
        hook.settings = replace(hook.settings, only_window_title='Other window')
        hook.user32.GetWindowTextW.side_effect = (
            lambda hwnd, buf, size: setattr(buf, 'value', 'Alpha Editor'))
        self.assertIsNone(hook.context())
        hook.user32.GetWindowTextW.assert_called_once()
        hook.user32.GetClassNameW.assert_not_called()
        hook.user32.GetKeyboardLayout.assert_not_called()

    def test_uwp_host_layout_uses_same_raw_class_names_without_reread(self):
        hook = self.hook()
        hook.user32.GetClassNameW.side_effect = (
            lambda hwnd, buf, size: setattr(buf, 'value',
                'ApplicationFrameWindow' if hwnd == 10
                else 'Windows.UI.Core.CoreWindow'))
        context = hook.context()
        self.assertIsNotNone(context)
        self.assertEqual((context.thread_id, context.layout_window, context.language),
                         (11, 10, 'ru'))
        self.assertEqual(hook.user32.GetClassNameW.call_count, 2)

    def test_same_top_and_focus_reads_class_only_once(self):
        hook = self.hook()
        hook.user32.GetGUIThreadInfo.side_effect = (
            lambda _thread, info: setattr(info._obj, 'hwndFocus', 10) or True)
        context = hook.context()
        self.assertIsNotNone(context)
        self.assertEqual((context.top, context.focus, context.language),
                         (10, 10, 'ru'))
        hook.user32.GetClassNameW.assert_called_once()

    def test_layout_owner_without_prefetched_classes_preserves_old_api(self):
        hook = self.hook()
        hook.user32.GetClassNameW.side_effect = (
            lambda hwnd, buf, size: setattr(buf, 'value',
                'ApplicationFrameWindow' if hwnd == 10
                else 'Windows.UI.Core.CoreWindow'))
        result = layout_owner(hook.user32, 10, 20, 11, 22)
        self.assertEqual(result, (10, 11))
        self.assertEqual(hook.user32.GetClassNameW.call_count, 2)

        hook.user32.GetClassNameW.reset_mock()
        result = layout_owner(hook.user32, 10, 20, 11, 22,
            top_class='ApplicationFrameWindow', focus_class='Windows.UI.Core.CoreWindow')
        self.assertEqual(result, (10, 11))
        hook.user32.GetClassNameW.assert_not_called()

    def test_missing_cached_class_retries_original_win32_read(self):
        hook = self.hook()
        hook.user32.GetClassNameW.side_effect = (
            lambda hwnd, buf, size: setattr(buf, 'value',
                'ApplicationFrameWindow' if hwnd == 10
                else 'Windows.UI.Core.CoreWindow'))
        result = layout_owner(hook.user32, 10, 20, 11, 22,
                              top_class='', focus_class='')
        self.assertEqual(result, (10, 11))
        self.assertEqual(hook.user32.GetClassNameW.call_count, 2)


if __name__ == '__main__':
    unittest.main()
