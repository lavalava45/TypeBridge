"""Caret/overlay tests without input, activation or visible overlays."""
import ctypes
from ctypes import wintypes as w
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import win32con
from caret_indicator import (CaretReader, CaretFlagIndicator, CaretSample,
                             Variant, flag_position, usable_rect)
from typebridge_app import Settings, load_settings, save_settings, TypeBridgeGui


class PositionTests(unittest.TestCase):
    def test_edges_and_negative_monitor_coordinates(self):
        for work in ((0, 0, 1920, 1040), (-1920, 0, 0, 1040)):
            for x, y in ((work[0], work[1]), (work[2]-1, work[3]-30)):
                px, py = flag_position((x,y,x+1,y+20), work, 22,16)
                self.assertTrue(work[0] <= px <= work[2]-22)
                self.assertTrue(work[1] <= py <= work[3]-16)

    def test_invalid_or_offscreen_caret_rejected(self):
        bounds = (0,0,800,600)
        self.assertTrue(usable_rect((10,20,10,40), bounds))
        for rect in ((0,0,0,0), (-10,20,-9,40), (20,20,500,40), (10,20,11,800)):
            self.assertFalse(usable_rect(rect, bounds))

    def test_state_hides_on_disable_expiry_and_focus_change(self):
        indicator = CaretFlagIndicator()
        indicator.hwnd = 7
        indicator.sample = CaretSample(10,11,(100,100,101,120),"he",(0,0,800,600),96,100)
        with patch("caret_indicator.win32gui") as gui, patch("caret_indicator.monotonic", return_value=100.1):
            gui.GetForegroundWindow.return_value = 10
            indicator._tick()
            gui.ShowWindow.assert_called_with(7,win32con.SW_HIDE)
            indicator.enabled.set()
            indicator._tick()
            self.assertEqual(gui.SetWindowPos.call_args.args[-1],
                             win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
            gui.SetWindowPos.reset_mock()
            gui.GetForegroundWindow.return_value = 12
            indicator._tick()
            gui.SetWindowPos.assert_not_called()
            gui.GetForegroundWindow.return_value = 10
            with patch("caret_indicator.monotonic", return_value=101):
                indicator._tick()
            gui.SetWindowPos.assert_not_called()

    def test_hit_testing_never_takes_focus(self):
        indicator = CaretFlagIndicator()
        self.assertEqual(indicator._wndproc(1,win32con.WM_MOUSEACTIVATE,0,0), win32con.MA_NOACTIVATE)
        self.assertEqual(indicator._wndproc(1,win32con.WM_NCHITTEST,0,0), win32con.HTTRANSPARENT)


class AccessibilityAbiTests(unittest.TestCase):
    def test_own_com_fixture_reads_only_caret_state_and_location_and_releases(self):
        self.assertEqual(ctypes.sizeof(Variant),24 if ctypes.sizeof(ctypes.c_void_p)==8 else 16)
        reader = CaretReader.__new__(CaretReader)
        released = []
        hidden = [False]
        state_type = ctypes.WINFUNCTYPE(w.LONG,ctypes.c_void_p,Variant,ctypes.POINTER(Variant))
        location_type = ctypes.WINFUNCTYPE(w.LONG,ctypes.c_void_p,
                                            *([ctypes.POINTER(w.LONG)]*4),Variant)
        release_type = ctypes.WINFUNCTYPE(w.ULONG,ctypes.c_void_p)
        @state_type
        def state(this,child,output):
            output.contents.vt=3
            output.contents.value.number=0x8000 if hidden[0] else 0
            return 0
        @location_type
        def location(this,x,y,width,height,child):
            x[0],y[0],width[0],height[0]=100,200,1,20
            return 0
        @release_type
        def release(this):
            released.append(True)
            return 0
        table=(ctypes.c_void_p*23)()
        for index,method in ((14,state),(22,location),(2,release)):
            table[index]=ctypes.cast(method,ctypes.c_void_p).value
        instance=(ctypes.POINTER(ctypes.c_void_p)*1)(table)
        def accessible(hwnd,object_id,iid,output):
            self.assertEqual(object_id,0xFFFFFFF8)
            output._obj.value=ctypes.addressof(instance)
            return 0
        reader.oleacc=SimpleNamespace(AccessibleObjectFromWindow=accessible)
        reader.oleaut=SimpleNamespace(VariantClear=Mock())
        reader.iid=None
        self.assertEqual(reader._accessible_caret(10),(100,200,101,220))
        hidden[0]=True
        self.assertIsNone(reader._accessible_caret(10))
        self.assertEqual(len(released),2)


class SettingsAndLifecycleTests(unittest.TestCase):
    def test_tray_menu_flag_check_is_independent_of_autocorrection(self):
        from tray_win32 import menu_items, MENU_CARET_FLAG
        for state in ("running", "paused", "stopped", "starting", "error"):
            for enabled in (False, True):
                item = next(row for row in menu_items(state, caret_flag=enabled)
                            if row[0] == MENU_CARET_FLAG)
                self.assertTrue(item[2])
                self.assertEqual(item[3], enabled)

    def test_tray_toggle_saves_only_flag_and_syncs_gui(self):
        app = TypeBridgeGui.__new__(TypeBridgeGui)
        app.tray = SimpleNamespace(caret_flag=False)
        app.show_caret_flag = Mock()
        app.show_caret_flag.get.return_value = True  # Unsaved GUI edit.
        app._apply_caret_indicator = Mock()
        app.emit = Mock()
        saved = replace(Settings(), plain_paste=True, conservative=False)
        with (patch('typebridge_app.load_settings', return_value=saved),
              patch('typebridge_app.save_settings') as write):
            app.toggle_caret_flag()
            write.assert_called_once_with(replace(saved, show_caret_flag=True))
            app.show_caret_flag.set.assert_called_once_with(True)
            app._apply_caret_indicator.assert_called_once_with(True)

    def test_failed_tray_save_does_not_change_indicator_or_check(self):
        app = TypeBridgeGui.__new__(TypeBridgeGui)
        app.tray = SimpleNamespace(caret_flag=True)
        app.show_caret_flag = Mock()
        app._apply_caret_indicator = Mock()
        app.emit = Mock()
        with (patch('typebridge_app.load_settings', return_value=Settings()),
              patch('typebridge_app.save_settings', side_effect=OSError('read-only'))):
            app.toggle_caret_flag()
        app.show_caret_flag.set.assert_not_called()
        app._apply_caret_indicator.assert_not_called()
        app.emit.assert_called_once()

    def test_option_persistence_legacy_defaults_and_invalid_type(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"settings.json"
            expected=replace(Settings(),show_caret_flag=True)
            save_settings(expected,path)
            self.assertEqual(load_settings(path),expected)
            path.write_text('{}',encoding='utf-8')
            self.assertFalse(load_settings(path).show_caret_flag)
            path.write_text('{"show_caret_flag":"false"}',encoding='utf-8')
            with self.assertRaises(ValueError): load_settings(path)

    def test_disabled_option_creates_no_controller_and_toggling_reuses_it(self):
        app=TypeBridgeGui.__new__(TypeBridgeGui)
        app.closing=False
        app.caret_indicator=None
        with patch("caret_indicator.CaretFlagIndicator") as controller:
            app._apply_caret_indicator(False)
            controller.assert_not_called()
            controller.return_value.closed.is_set.return_value=False
            app._apply_caret_indicator(True)
            app._apply_caret_indicator(False)
            controller.assert_called_once()
            controller.return_value.start.assert_called_once()
            controller.return_value.set_enabled.assert_called_with(False)


if __name__ == '__main__': unittest.main()
