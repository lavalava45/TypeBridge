import unittest
from unittest.mock import MagicMock, Mock, patch

import windows_shortcuts as shortcuts


class WindowsShortcutsTests(unittest.TestCase):
    def read(self, values):
        def query(_key, name):
            if name not in values:
                raise FileNotFoundError(name)
            return values[name], 1

        with patch.object(shortcuts.winreg, "OpenKey", return_value=MagicMock()), \
                patch.object(shortcuts.winreg, "QueryValueEx", side_effect=query):
            return shortcuts.read_windows_shortcuts()

    def test_language_and_layout_are_independent(self):
        self.assertEqual(self.read({"Language Hotkey": "2", "Layout Hotkey": "3"}),
                         ("Ctrl+Shift", "Не назначено"))
        self.assertEqual(self.read({"Language Hotkey": "1", "Layout Hotkey": "2"}),
                         ("Левый Alt+Shift", "Ctrl+Shift"))

    def test_missing_or_unsupported_does_not_guess_from_legacy_value(self):
        self.assertEqual(self.read({"Hotkey": "2", "Layout Hotkey": "4"}),
                         (shortcuts.UNKNOWN, shortcuts.UNKNOWN))

    def test_access_failure(self):
        with patch.object(shortcuts.winreg, "OpenKey", side_effect=PermissionError):
            self.assertEqual(shortcuts.read_windows_shortcuts(),
                             (shortcuts.UNKNOWN, shortcuts.UNKNOWN))

    def test_opener_uses_settings_uri_without_changing_registry(self):
        with patch.object(shortcuts.os, "startfile") as launch:
            shortcuts.open_windows_keyboard_settings()
        launch.assert_called_once_with("ms-settings:typing")

    def test_gui_refresh_reads_updated_values(self):
        from typebridge_app import TypeBridgeGui

        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.windows_language_shortcut = Mock()
        gui.windows_layout_shortcut = Mock()
        with patch("typebridge_app.read_windows_shortcuts", side_effect=[
            ("Ctrl+Shift", "Не назначено"), ("Левый Alt+Shift", "Ctrl+Shift")
        ]):
            gui.refresh_windows_shortcuts()
            gui.windows_language_shortcut.set.assert_called_with("Ctrl+Shift")
            gui.refresh_windows_shortcuts()
            gui.windows_language_shortcut.set.assert_called_with("Левый Alt+Shift")
            gui.windows_layout_shortcut.set.assert_called_with("Ctrl+Shift")


if __name__ == "__main__":
    unittest.main()
