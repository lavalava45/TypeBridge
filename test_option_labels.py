"""Checkbox/config roundtrip with an unshown Tk root; no input or hooks."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock
from typebridge_app import TypeBridgeGui, Settings, save_settings, load_settings


class OptionLabelsTests(unittest.TestCase):
    def test_positive_rare_checkbox_preserves_existing_config_and_saves_inverse(self):
        root = tk.Tk()
        root.withdraw()  # No update/mainloop: the root is never mapped.
        try:
            for initial in (True, False):
                gui = TypeBridgeGui.__new__(TypeBridgeGui)
                gui.conservative = tk.BooleanVar(root, value=initial)
                checkbox = gui._make_rare_words_option(root)
                self.assertEqual(checkbox.instate(['selected']), not initial)
                checkbox.invoke()
                self.assertEqual(gui.conservative.get(), not initial)
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / 'settings.json'
                    save_settings(Settings(conservative=gui.conservative.get()), path)
                    self.assertEqual(load_settings(path).conservative, not initial)
                checkbox.destroy()
        finally:
            root.destroy()

    def test_single_early_checkbox_drives_both_legacy_fields_and_roundtrips(self):
        root = tk.Tk()
        root.withdraw()
        try:
            for master in (True, False):
                for old_secondary in (True, False):
                    gui = TypeBridgeGui.__new__(TypeBridgeGui)
                    gui.root = root
                    gui._init_early_options(Settings(early_correction=master, early_native=old_secondary))
                    checkbox = gui._make_early_option(root)
                    self.assertEqual(checkbox.instate(['selected']), master)
                    self.assertIs(gui.early_correction, gui.early_native)
                    checkbox.invoke()
                    with tempfile.TemporaryDirectory() as folder:
                        path = Path(folder) / 'settings.json'
                        save_settings(Settings(early_correction=gui.early_correction.get(),
                                               early_native=gui.early_native.get()), path)
                        loaded = load_settings(path)
                        self.assertEqual((loaded.early_correction, loaded.early_native), (not master, not master))
                    checkbox.destroy()
        finally:
            root.destroy()


if __name__ == '__main__': unittest.main()
