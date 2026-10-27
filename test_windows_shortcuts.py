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

    def test_legacy_value_is_labelled_and_never_used_for_layout_shortcut(self):
        self.assertEqual(self.read({"Hotkey": "2", "Layout Hotkey": "4"}),
                         ("Ctrl+Shift (сохранено в Hotkey; применение Windows не подтверждено)", shortcuts.UNKNOWN))

    def test_access_failure(self):
        with patch.object(shortcuts.winreg, "OpenKey", side_effect=PermissionError):
            self.assertEqual(shortcuts.read_windows_shortcuts(),
                             (shortcuts.WIN_SPACE + ' (параметры недоступны)', shortcuts.UNKNOWN))

    def test_unset_optional_hotkeys_show_system_default_not_fake_error(self):
        self.assertEqual(self.read({}),
                         (shortcuts.DEFAULT_LANGUAGE,
                          shortcuts.NOT_CONFIGURED))
        self.assertEqual(self.read({'Language Hotkey': '3'}),
                         ('Не назначено (дополнительно; Win+Space доступно)',
                          shortcuts.NOT_CONFIGURED))

    def test_absent_toggle_branch_keeps_built_in_windows_shortcut_visible(self):
        with patch.object(shortcuts.winreg, 'OpenKey', side_effect=FileNotFoundError):
            self.assertEqual(shortcuts.read_windows_shortcuts(),
                             (shortcuts.DEFAULT_LANGUAGE,
                              shortcuts.NOT_CONFIGURED))

    def test_opener_uses_settings_uri_without_changing_registry(self):
        with patch.object(shortcuts.os, "startfile") as launch:
            shortcuts.open_windows_keyboard_settings()
        launch.assert_called_once_with("ms-settings:typing")

    def test_gui_refresh_reads_updated_values(self):
        from typebridge_app import TypeBridgeGui

        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.emit = Mock()
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

    def test_explicit_disabled_setting_never_falls_back_to_alt_shift(self):
        language, _ = self.read({'Language Hotkey': '3', 'Hotkey': '1'})
        self.assertNotIn('Alt+Shift', language)
        self.assertIn('Не назначено', language)

    def test_unknown_setting_never_claims_default_is_active(self):
        language, _ = self.read({'Language Hotkey': 'unexpected'})
        self.assertIn('параметры недоступны', language)
        self.assertNotEqual(language, shortcuts.DEFAULT_LANGUAGE)

    def test_unreadable_legacy_value_is_not_treated_as_absent(self):
        def query(_key, name):
            if name == 'Hotkey':
                raise PermissionError('denied')
            raise FileNotFoundError(name)
        with patch.object(shortcuts.winreg, 'OpenKey', return_value=MagicMock()), \
                patch.object(shortcuts.winreg, 'QueryValueEx', side_effect=query):
            language, _ = shortcuts.read_windows_shortcuts()
        self.assertIn('параметры недоступны', language)


if __name__ == "__main__":
    unittest.main()
