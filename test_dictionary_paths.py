"""Dictionary paths must not depend on working directory or extraction folder."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import engine


class DictionaryPathTests(unittest.TestCase):
    def test_source_uses_source_directory(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(engine.source_data_dir(), Path(engine.__file__).resolve().parent / "data")

    def test_frozen_uses_executable_directory(self):
        executable = Path(__file__).resolve().parent / "portable" / "TypeBridge.exe"
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)), patch.object(sys, "_MEIPASS", "unrelated-extraction-folder", create=True):
            self.assertEqual(engine.source_data_dir(), executable.parent / "data")


if __name__ == "__main__":
    unittest.main()
