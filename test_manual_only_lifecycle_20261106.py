"""GUI startup when auto is disabled; Tk withdrawn, no live hooks or input."""
from dataclasses import replace
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from typebridge_app import Settings, TypeBridgeGui
from user_lexicon import UserLexicon


class ManualOnlyLifecycleTests(unittest.TestCase):
    def test_disabled_auto_schedules_paused_hook_and_preserves_checkbox(self):
        for profile, expected_manual in (
            (Settings(corrections_enabled=False), True),
            (replace(Settings(), word_key='Отключено'), False),
            (replace(Settings(), corrections_enabled=True), False),
        ):
            with self.subTest(profile=profile):
                with tempfile.TemporaryDirectory() as tmp:
                    root = tk.Tk(); root.withdraw()
                    events = []
                    try:
                        with (patch('typebridge_app.USER_DIR', Path(tmp)),
                              patch('typebridge_app.load_settings', return_value=profile),
                              patch('typebridge_app.load_user_lexicon', return_value=UserLexicon()),
                              patch('typebridge_app.read_registration', return_value=None),
                              patch.object(root, 'after', side_effect=lambda delay, callback: events.append((delay, callback)))):
                            gui = TypeBridgeGui(root)
                            callback = [cb for delay, cb in events if delay == 300]
                            self.assertEqual(len(callback), int(expected_manual or profile.corrections_enabled))
                            self.assertEqual(gui.corrections_enabled.get(), profile.corrections_enabled)
                            gui.start = Mock()
                            for cb in callback:
                                cb()
                            if expected_manual:
                                gui.start.assert_called_once_with(manual_only=True)
                            elif profile.corrections_enabled:
                                gui.start.assert_called_once_with()
                            else:
                                gui.start.assert_not_called()
                    finally:
                        root.destroy()

    def test_gui_manual_start_saves_auto_false_not_true(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.closing = False
        gui.corrections_enabled = Mock()
        gui.worker = None
        gui.run_state = 'stopped'
        gui.save = Mock(return_value=None)
        gui._sync_tray_state = Mock()
        gui.start(manual_only=True)
        gui.corrections_enabled.set.assert_called_once_with(False)
        self.assertTrue(gui._starting_manual_only)
        gui.save.assert_called_once()

    def test_switching_live_manual_auto_mode_persists_preference(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.closing = False
        gui.corrections_enabled = Mock()
        gui.worker = Mock()
        gui.worker.is_alive.return_value = True
        gui.hook = Mock()
        gui.hook.paused = True
        gui.save = Mock(return_value=Settings(corrections_enabled=True))
        gui._sync_tray_state = Mock()
        gui.start()
        gui.corrections_enabled.set.assert_called_with(True)
        gui.save.assert_called_once()
        gui.hook.request_pause.assert_called_once()

        gui.hook.request_pause.reset_mock()
        gui.save.reset_mock()
        gui.hook.paused = False
        gui.pause()
        gui.corrections_enabled.set.assert_called_with(False)
        gui.save.assert_called_once()
        gui.hook.request_pause.assert_called_once()


if __name__ == '__main__':
    unittest.main()
