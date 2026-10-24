import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from settings_widgets import ScrollablePage, Tooltip, window_size


class SettingsGeometryTests(unittest.TestCase):
    def test_large_content_stays_inside_small_and_scaled_work_areas(self):
        for width, height, line in ((1366, 728, 16), (1280, 680, 24), (1920, 1040, 32)):
            w, h = window_size(1100, 1300, width, height, line)
            self.assertLess(w, width)
            self.assertLessEqual(h + 2 * line + 24, height)
            self.assertGreater(h, 300)

    def test_tooltip_delay_is_cancelled_on_leave_without_opening_window(self):
        widget = Mock()
        widget.after.return_value = 'timer'
        tip = Tooltip(widget, 'help')
        tip.schedule()
        widget.after.assert_called_once_with(500, tip.show)
        tip.hide()
        widget.after_cancel.assert_called_once_with('timer')
        self.assertIsNone(tip.window)

    def test_page_content_is_scrollable_in_withdrawn_window(self):
        root = tk.Tk(); root.withdraw()
        try:
            page = ScrollablePage(root); page.pack(fill='both', expand=True)
            tk.Label(page.body, text='test\n' * 50).pack()
            root.update_idletasks()
            page._width(SimpleNamespace(width=500))
            page._region()
            self.assertEqual(float(page.canvas.itemcget(page.item, 'width')), 500)
            self.assertTrue(page.canvas.cget('scrollregion'))
            self.assertGreater(page.body.winfo_reqheight(), 500)
            self.assertFalse(root.winfo_ismapped())
        finally:
            root.destroy()


if __name__ == '__main__': unittest.main()
