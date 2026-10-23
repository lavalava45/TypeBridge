"""Layout menu checks without installing layouts or touching user focus."""
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tray_layouts import LayoutChoice, LayoutTarget, WindowsLayouts
from tray_win32 import TrayController, menu_items, MENU_ENABLE


class LayoutMenuTests(unittest.TestCase):
    def test_menu_has_one_correction_checkbox_and_no_pause_or_stop_command(self):
        for state in ("stopped", "running", "paused", "starting", "error"):
            rows = menu_items(state)
            toggle = [row for row in rows if row[0] == MENU_ENABLE]
            self.assertEqual(len(toggle), 1)
            self.assertEqual(toggle[0][3], state == "running")
            self.assertFalse(any("Остановить" in row[1] or "продолжить" in row[1] for row in rows))

    def test_windows_enumeration_keeps_other_languages_and_native_64bit_hkl(self):
        service = WindowsLayouts.__new__(WindowsLayouts)
        service.u = Mock()
        service.k = Mock()
        handles = [0x04090409, 0x04190419, 0xFFFFFFFFF03D040D, 0x04070407]

        def get_list(count, output):
            if count:
                for index, handle in enumerate(handles):
                    output[index] = handle
            return len(handles)

        service.u.GetKeyboardLayoutList.side_effect = get_list
        with (patch("tray_layouts.keyboard_name", side_effect=lambda hkl: str(hkl)),
              patch("tray_layouts.configured_layout_list", return_value=[])):
            choices = service.installed()
        self.assertEqual([choice.hkl for choice in choices], handles)

        handles.append(0x040D040D)
        with patch("tray_layouts.configured_layout_list", return_value=[
                (h & 0xFFFF, "test", h & 0xFFFFFFFF) for h in handles[:-1]]):
            self.assertEqual([c.hkl for c in service.installed()], handles[:-1])
        # Intentionally configured variants of one language must both survive.
        with patch("tray_layouts.configured_layout_list", return_value=[
                (h & 0xFFFF, "test", h & 0xFFFFFFFF) for h in handles]):
            self.assertEqual([c.hkl for c in service.installed()], handles)

    def service_fixture(self):
        target = LayoutTarget(10, 11, 12, 13)
        service = WindowsLayouts.__new__(WindowsLayouts)
        service.u = Mock()
        service.valid = Mock(return_value=True)
        service.installed = Mock(return_value=[LayoutChoice(0xFFFFFFFFF03D040D, "Hebrew Standard")])
        service.current = Mock(side_effect=[0x04090409, 0xFFFFFFFFF03D040D])
        return service, target

    def test_exact_native_hkl_is_requested_and_actual_state_confirms_zero_return(self):
        service, target = self.service_fixture()
        service.u.SendMessageTimeoutW.return_value = 0
        service.request(target, 0xFFFFFFFFF03D040D)
        args = service.u.SendMessageTimeoutW.call_args.args
        self.assertEqual(args[:4], (target.focus, 0x50, 0,
                                   ctypes.c_ssize_t(0xFFFFFFFFF03D040D).value))

    def test_closed_target_is_refused_without_sending(self):
        service, target = self.service_fixture()
        service.valid.return_value = False
        with self.assertRaises(OSError):
            service.request(target, 0xFFFFFFFFF03D040D)
        service.u.SendMessageTimeoutW.assert_not_called()

    def test_removed_layout_is_refused_without_loading_or_sending(self):
        service, target = self.service_fixture()
        with self.assertRaises(OSError):
            service.request(target, 0x04070407)
        service.u.SendMessageTimeoutW.assert_not_called()
        service.u.LoadKeyboardLayoutW.assert_not_called()

    def test_shell_click_does_not_replace_last_application_target(self):
        tray = TrayController.__new__(TrayController)
        target = LayoutTarget(10, 11, 12, 13)
        tray.last_target = target
        tray.layouts = Mock()
        tray.layouts.capture.return_value = None
        tray.observe_foreground()
        self.assertEqual(tray.last_target, target)

    def test_selection_returns_to_saved_application_before_request(self):
        tray = TrayController.__new__(TrayController)
        target = LayoutTarget(10, 11, 12, 13)
        choice = LayoutChoice(0x04070407, "German")
        tray.hwnd = 99
        tray.layouts = Mock()
        tray.layouts.valid.return_value = True
        with patch("tray_win32.win32gui") as gui:
            gui.GetForegroundWindow.side_effect = [99, 10]
            tray.switch_layout(target, choice)
            gui.SetForegroundWindow.assert_called_once_with(10)
        tray.layouts.request.assert_called_once_with(target, choice.hkl)

    def test_selection_does_not_steal_focus_if_user_has_changed_application(self):
        tray = TrayController.__new__(TrayController)
        tray.hwnd = 99
        tray.layouts = Mock()
        tray.layouts.valid.return_value = True
        with patch("tray_win32.win32gui") as gui:
            gui.GetForegroundWindow.return_value = 88
            gui.GetClassName.return_value = "OtherEditor"
            with self.assertRaises(OSError):
                tray.switch_layout(LayoutTarget(10, 11, 12, 13), LayoutChoice(0x4090409, "US"))
            gui.SetForegroundWindow.assert_not_called()
        tray.layouts.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
