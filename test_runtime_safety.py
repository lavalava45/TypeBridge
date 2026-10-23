"""Non-invasive tests: no global hooks, user processes or windows touched."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from runtime_safety import original_switcher_running, require_no_original_switcher


def fake_process(name):
    return SimpleNamespace(info={"name": name})


class RuntimeSafetyTests(unittest.TestCase):
    def test_original_switcher_detected_case_insensitively(self):
        with patch("runtime_safety.psutil.process_iter", return_value=[
                fake_process("explorer.exe"), fake_process("SwitchER64.EXE")]):
            self.assertTrue(original_switcher_running())
            with self.assertRaisesRegex(RuntimeError, "SwitchER is still running"):
                require_no_original_switcher()

    def test_no_competing_switcher_is_allowed(self):
        with patch("runtime_safety.psutil.process_iter", return_value=[
                fake_process(None), fake_process("notepad.exe")]):
            self.assertFalse(original_switcher_running())
            require_no_original_switcher()

    def test_enumeration_error_fails_closed(self):
        import psutil
        with patch("runtime_safety.psutil.process_iter",
                   side_effect=psutil.AccessDenied()):
            with self.assertRaisesRegex(RuntimeError, "Cannot verify"):
                require_no_original_switcher()


if __name__ == "__main__":
    unittest.main()
