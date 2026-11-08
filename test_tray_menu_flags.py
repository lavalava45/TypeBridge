"""Menu construction with mocked Win32 handles: never opens foreground GUI."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import win32con

from tray_layouts import LayoutChoice
from tray_win32 import MENU_LAYOUT_BASE, TrayController, menu_layout_icon_code


class TrayMenuFlagTests(unittest.TestCase):
    def test_manual_layout_menu_title_is_translated_for_all_supported_ui_languages(self):
        from ui_i18n import tr
        phrase = 'Переключить раскладку вручную'
        for language, title in (
                ('ru', phrase),
                ('en', 'Switch keyboard layout manually'),
                ('he', 'החלפת פריסת מקלדת באופן ידני')):
            with self.subTest(language=language), patch('ui_i18n._language', language):
                self.assertEqual(tr(phrase), title)

    def fixture(self):
        tray = TrayController.__new__(TrayController)
        tray.observe_foreground = Mock()
        tray.notify = Mock()
        tray.last_target = object()
        tray.run_state = ('running', '')
        tray.caret_flag = False
        tray.journal_enabled = False
        tray.native_recovery_available = False
        tray.hwnd = 80
        tray.layouts = Mock()
        tray.layouts.current.return_value = 0x04190419
        tray.layouts.valid.return_value = True
        tray.layouts.installed.return_value = [
            LayoutChoice(0x04090409, 'English'),
            LayoutChoice(0x04190419, 'Russian'),
            LayoutChoice(0xF03D040D, 'Hebrew variant'),
            LayoutChoice(0x04070407, 'German'),
            LayoutChoice(0xFFFF, 'Unknown'),
        ]
        return tray

    def test_hkl_language_mapping_and_unknown_fallback(self):
        self.assertEqual([menu_layout_icon_code(v) for v in
                          (0x04090409, 0x04190419, 0xFFFFFFFFF03D040D,
                           0x04070407, 0xFFFF)],
                         ['en', 'ru', 'he', 'de', 'idle'])

    def test_dynamic_flags_checked_language_and_cleanup_after_menu_closes(self):
        tray = self.fixture()
        with (patch('tray_win32.win32gui') as gui,
              patch('tray_win32._icon_files', return_value={
                  code: Path(code + '.ico') for code in ('en', 'ru', 'he', 'de', 'idle')}) as files,
              patch('tray_win32._menu_flag_bitmap', side_effect=[71, 72, 73, 74, 75]) as bitmap):
            gui.CreatePopupMenu.side_effect = [10, 11]
            gui.GetCursorPos.return_value = (0, 0)
            gui.TrackPopupMenu.return_value = 0
            tray._show_menu()
        tray.observe_foreground.assert_called_once()
        files.assert_called_once_with(['en', 'ru', 'he', 'de', 'idle'])
        self.assertEqual(bitmap.call_count, 5)
        parent = [call.args for call in gui.AppendMenu.call_args_list
                  if call.args[0] == 10 and call.args[1] & win32con.MF_POPUP]
        self.assertEqual(parent[0][3], 'Переключить раскладку вручную')
        language_items = [call.args for call in gui.AppendMenu.call_args_list
                          if call.args[0] == 11 and MENU_LAYOUT_BASE <= call.args[2] < MENU_LAYOUT_BASE + 5]
        self.assertEqual(len(language_items), 5)
        self.assertFalse(language_items[0][1] & win32con.MF_CHECKED)
        self.assertTrue(language_items[1][1] & win32con.MF_CHECKED)
        self.assertTrue(language_items[1][3].startswith('✓ '))
        self.assertEqual([call.args[-2:] for call in gui.SetMenuItemBitmaps.call_args_list],
                         [(v, v) for v in (71, 72, 73, 74, 75)])
        gui.DestroyMenu.assert_called_once_with(10)
        self.assertEqual([c.args[0] for c in gui.DeleteObject.call_args_list],
                         [71, 72, 73, 74, 75])
        tray.notify.assert_not_called()

    def test_menu_bitmap_failure_still_offers_layouts_and_frees_existing_gdi(self):
        tray = self.fixture()
        with (patch('tray_win32.win32gui') as gui,
              patch('tray_win32._icon_files', return_value={
                  code: Path(code + '.ico') for code in ('en', 'ru', 'he', 'de', 'idle')}),
              patch('tray_win32._menu_flag_bitmap', side_effect=[71, OSError('no art'), 73, 74, 75])):
            gui.CreatePopupMenu.side_effect = [10, 11]
            gui.GetCursorPos.return_value = (0, 0)
            gui.TrackPopupMenu.return_value = 0
            tray._show_menu()
        self.assertEqual([c.args[0] for c in gui.DeleteObject.call_args_list],
                         [71, 73, 74, 75])
        self.assertEqual(gui.SetMenuItemBitmaps.call_count, 4)
        self.assertEqual(sum(1 for c in gui.AppendMenu.call_args_list
                             if c.args[0] == 11), 5)

    def test_readonly_or_unavailable_icon_assets_leave_menu_fully_functional(self):
        tray = self.fixture()
        with (patch('tray_win32.win32gui') as gui,
              patch('tray_win32._icon_files', side_effect=OSError('read-only profile')),
              patch('tray_win32._menu_flag_bitmap') as bitmap):
            gui.CreatePopupMenu.side_effect = [10, 11]
            gui.GetCursorPos.return_value = (0, 0)
            gui.TrackPopupMenu.return_value = 0
            tray._show_menu()
        bitmap.assert_not_called()
        self.assertEqual(sum(1 for c in gui.AppendMenu.call_args_list
                             if c.args[0] == 11), 5)
        gui.DestroyMenu.assert_called_once_with(10)

    def test_bitmap_factory_creates_real_gdi_bitmap_from_temporary_ico(self):
        # Real GDI allocation is safe and does not show a window or load a hook.
        import ctypes
        import tempfile
        import win32gui
        from ctypes import wintypes
        from PIL import Image
        from tray_win32 import _menu_flag_bitmap

        with tempfile.TemporaryDirectory() as temporary:
            icon = Path(temporary) / 'fixture.ico'
            Image.new('RGBA', (64, 64), (8, 36, 64, 255)).save(icon, format='ICO')
            bitmap = _menu_flag_bitmap(icon)
            try:
                gdi = ctypes.WinDLL('gdi32')
                gdi.GetObjectType.argtypes = [wintypes.HGDIOBJ]
                gdi.GetObjectType.restype = wintypes.DWORD
                self.assertEqual(gdi.GetObjectType(bitmap), 7)  # OBJ_BITMAP
            finally:
                win32gui.DeleteObject(bitmap)

    def test_track_popup_exception_still_destroys_menu_then_bitmaps(self):
        tray = self.fixture()
        order = []
        with (patch('tray_win32.win32gui') as gui,
              patch('tray_win32._icon_files', return_value={
                  code: Path(code + '.ico') for code in ('en', 'ru', 'he', 'de', 'idle')}),
              patch('tray_win32._menu_flag_bitmap', side_effect=[71, 72, 73, 74, 75])):
            gui.CreatePopupMenu.side_effect = [10, 11]
            gui.GetCursorPos.return_value = (0, 0)
            gui.TrackPopupMenu.side_effect = RuntimeError('popup failed')
            gui.DestroyMenu.side_effect = lambda *_: order.append('menu')
            gui.DeleteObject.side_effect = lambda *_: order.append('bitmap')
            with self.assertRaisesRegex(RuntimeError, 'popup failed'):
                tray._show_menu()
        self.assertEqual(order, ['menu', 'bitmap', 'bitmap', 'bitmap', 'bitmap', 'bitmap'])


if __name__ == '__main__':
    unittest.main()
