"""Geometry-only subprocess and timeout tests; no foreground or input changes."""
import json
import unittest
from unittest.mock import Mock, patch

from caret_process import CaretProcess, smoke_test


class CaretProcessTests(unittest.TestCase):
    def test_real_spawn_synthetic_geometry_and_child_cleanup(self):
        self.assertTrue(smoke_test())

    def fixture(self):
        reader = CaretProcess(clock=lambda: 100)
        process = reader.process = Mock(pid=1)
        process.is_alive.side_effect = [True, False, False]
        reader.connection = Mock()
        reader.scope = 1
        return reader, process

    def test_hung_provider_is_terminated_and_backed_off(self):
        reader, process = self.fixture()
        with patch.object(reader, '_receive', side_effect=TimeoutError):
            self.assertIsNone(reader.snapshot(scope=1))
        process.terminate.assert_called_once()
        self.assertIsNone(reader.process)
        self.assertEqual(reader.retry_at, 102)
        with patch.object(reader, '_start') as start:
            self.assertIsNone(reader.snapshot(scope=1))
            start.assert_not_called()

    def test_scope_cancellation_has_no_failure_penalty(self):
        reader, process = self.fixture()
        with patch.object(reader, '_receive', side_effect=InterruptedError):
            self.assertIsNone(reader.snapshot(scope=1))
        self.assertEqual(reader.retry_at, 0)
        self.assertEqual(reader.failures, 0)
        process.terminate.assert_called_once()

    def test_failed_termination_cannot_spawn_another_helper(self):
        reader = CaretProcess(clock=lambda: 100)
        reader.scope = 1
        process = reader.process = Mock(pid=1)
        process.is_alive.return_value = True
        reader.connection = Mock()
        reader.cancel()
        self.assertIs(reader.process, process)
        self.assertIsNone(reader.connection)
        with patch.object(reader, '_start') as start:
            self.assertIsNone(reader.snapshot(scope=1))
            start.assert_not_called()

    def test_malformed_response_does_not_become_healthy_no_caret(self):
        reader, process = self.fixture()
        with patch.object(reader, '_receive', return_value=json.dumps({'unexpected': 1}).encode()):
            self.assertIsNone(reader.snapshot(scope=1))
        self.assertEqual(reader.last_error, 'ValueError')
        self.assertEqual(reader.retry_at, 102)


if __name__ == '__main__':
    unittest.main()
