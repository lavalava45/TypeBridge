"""Pure matrix-runner contract tests, never execute another unittest process."""
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

import test_matrix_runner as runner
from test_matrix_module import owned_suite


class MatrixRunnerTests(unittest.TestCase):
    def test_module_loader_ignores_imported_testcase_fixture(self):
        fake = ModuleType('test_matrix_fake')
        imported = type('ImportedFixture', (unittest.TestCase,), {
            '__module__': 'test_some_other_module', 'test_duplicate': lambda self: None,
        })
        owned = type('OwnedTests', (unittest.TestCase,), {
            '__module__': 'test_matrix_fake', 'test_owned': lambda self: None,
        })
        fake.ImportedFixture, fake.OwnedTests = imported, owned
        suite = owned_suite(fake)
        self.assertEqual(suite.countTestCases(), 1)
        self.assertEqual(next(iter(suite))._testMethodName, 'test_owned')

    def test_success_records_counts_in_separate_log(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = Mock(returncode=0,
                             stdout=b'test_a ... ok\nRan 1 test in 0.001s\nOK\n')
            with patch.object(runner.subprocess, 'run', return_value=completed) as run:
                result = runner.module_run('test_example', Path(directory))
            self.assertEqual((result['status'], result['tests']), ('passed', 1))
            self.assertIn('test_a', (Path(directory) / 'test_example.log').read_text())
            self.assertIn('timeout', run.call_args.kwargs)
            self.assertFalse(run.call_args.kwargs['shell'] if 'shell' in run.call_args.kwargs else False)

    def test_timeout_is_distinct_from_failed_assertion(self):
        with tempfile.TemporaryDirectory() as directory:
            expired = subprocess.TimeoutExpired(['python', '-m', 'unittest'], 1,
                                                output=b'test_stalled ... ')
            with patch.object(runner.subprocess, 'run', side_effect=expired):
                result = runner.module_run('test_stalled', Path(directory))
            self.assertEqual(result['status'], 'timeout')
            self.assertIsNone(result['tests'])
            self.assertIn('MODULE TIMED OUT', (Path(directory) / 'test_stalled.log').read_text())

    def test_failed_assertion_is_reported_by_exact_test_name(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = Mock(returncode=1, stdout=(
                b'FAIL: test_changed (test_example.Example.test_changed)\n'
                b'Ran 1 test in 0.001s\nFAILED (failures=1)\n'))
            with patch.object(runner.subprocess, 'run', return_value=completed):
                result = runner.module_run('test_example', Path(directory))
            self.assertEqual(result['status'], 'failed')
            self.assertIn('test_changed', result['failures'][0])


if __name__ == '__main__':
    unittest.main()
