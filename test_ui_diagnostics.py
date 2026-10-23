import tempfile
import unittest
from pathlib import Path
from ui_diagnostics import record_callback_error


class CallbackDiagnosticsTests(unittest.TestCase):
    def test_logs_only_exception_type_and_frame_locations(self):
        with tempfile.TemporaryDirectory() as folder:
            try:
                raise RuntimeError('secret clipboard value')
            except RuntimeError as error:
                path = record_callback_error(folder, type(error), error.__traceback__)
            text = path.read_text(encoding='utf-8')
            self.assertIn('RuntimeError', text)
            self.assertIn('test_ui_diagnostics.py:', text)
            self.assertNotIn('secret', text)
            path.write_text('x' * 65537, encoding='utf-8')
            record_callback_error(folder, ValueError, None)
            self.assertLess(path.stat().st_size, 1024)


if __name__ == '__main__':
    unittest.main()
