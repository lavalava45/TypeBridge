"""Settings visibility/persistence and copy in withdrawn UI; no live hooks."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
from unittest.mock import patch

from typebridge_app import TypeBridgeGui, Settings, save_settings, load_settings
from user_lexicon import UserLexicon
from settings_copy import PASSWORD_CHECK_HELP, PASSWORD_CHECK_LABEL, INDICATOR_HELP
from ui_translations import CATALOG


class SettingsCopyTests(unittest.TestCase):
    def test_log_setting_defaults_off_and_roundtrips_strict_boolean(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            p.write_text('{}',encoding='utf-8')
            self.assertFalse(load_settings(p).show_events)
            save_settings(replace(Settings(),show_events=True),p)
            self.assertTrue(load_settings(p).show_events)
            p.write_text('{"show_events":"true"}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'show_events'):
                load_settings(p)

    def test_visibility_changes_immediately_without_starting_services(self):
        with TemporaryDirectory() as tmp:
            root=tk.Tk();root.withdraw()
            try:
                with patch('typebridge_app.USER_DIR',Path(tmp)), \
                     patch('typebridge_app.load_settings',return_value=Settings()), \
                     patch('typebridge_app.load_user_lexicon',return_value=UserLexicon()), \
                     patch('typebridge_app.read_registration',return_value=None), \
                     patch('typebridge_app.DEBUG_ENABLED',False):
                    gui=TypeBridgeGui(root)
                    self.assertEqual(gui.log_frame.winfo_manager(),'')
                    self.assertFalse(gui.current_settings().show_events)
                    gui.show_events.set(True);gui._set_event_log_visibility()
                    self.assertEqual(gui.log_frame.winfo_manager(),'pack')
                    self.assertTrue(gui.current_settings().show_events)
                    gui.show_events.set(False);gui._set_event_log_visibility()
                    self.assertEqual(gui.log_frame.winfo_manager(),'')
                    self.assertIsNone(gui.hook)
                    self.assertFalse(root.winfo_ismapped())
            finally:
                root.destroy()

    def test_current_help_has_no_internal_gate_names_and_is_translated(self):
        self.assertNotIn('R04',PASSWORD_CHECK_HELP)
        self.assertNotIn('DOM',PASSWORD_CHECK_HELP)
        self.assertNotIn('RU / EN / HE',INDICATOR_HELP)
        self.assertIn('AR',INDICATOR_HELP)
        for text in (PASSWORD_CHECK_HELP,PASSWORD_CHECK_LABEL,INDICATOR_HELP):
            self.assertEqual(set(CATALOG[text]),{'en','he'})


if __name__=='__main__':
    unittest.main()
