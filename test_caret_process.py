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

    def test_window_changes_do_not_bypass_backoff_or_failure_limit(self):
        now = [100]
        reader = CaretProcess(clock=lambda: now[0])
        with patch.object(reader, '_start', side_effect=OSError) as start:
            reader.snapshot(scope=1)
            reader.snapshot(scope=2)
            self.assertEqual(start.call_count, 1)
            now[0] = 102
            reader.snapshot(scope=3)
            now[0] = 106
            reader.snapshot(scope=4)
            self.assertTrue(reader.disabled)
            now[0] = 200
            reader.snapshot(scope=5)
            self.assertEqual(start.call_count, 3)
            self.assertIn('phase=startup', reader.diagnostic)
            self.assertIn('retries-disabled=True', reader.diagnostic)
            reader.reset_failures()
            reader.snapshot(scope=5)
            self.assertEqual(start.call_count, 4)

    def test_healthy_connection_survives_window_change(self):
        reader, process = self.fixture()
        with patch.object(reader, '_receive', return_value=b'{"sample": null}'):
            reader.snapshot(scope=2)
        process.terminate.assert_not_called()
        reader.connection.send_bytes.assert_called_once_with(b'snapshot')

    def test_focus_change_during_startup_keeps_ready_process_without_reading(self):
        reader = CaretProcess()
        with (patch.object(reader, '_start') as start,
              patch.object(reader, '_receive') as receive):
            # Focus is lost while the child is starting; lifecycle is still live.
            reader.snapshot(scope=1, cancelled=Mock(side_effect=[False, True]),
                            lifecycle_cancelled=lambda: False)
        start.assert_called_once()
        receive.assert_not_called()
        self.assertEqual(reader.failures, 0)

    def test_focus_change_during_request_drains_response_without_restart(self):
        reader, process = self.fixture()
        cancel = Mock(side_effect=[False, False, True])
        lifecycle = lambda: False
        with patch.object(reader, '_receive', return_value=b'{"sample": null}') as receive:
            self.assertIsNone(reader.snapshot(scope=1, cancelled=cancel,
                                            lifecycle_cancelled=lifecycle))
        receive.assert_called_once_with(.3, lifecycle)
        process.terminate.assert_not_called()
        self.assertEqual(reader.failures, 0)

    def test_missing_frozen_runtime_stops_before_bootloader_launch(self):
        reader = CaretProcess()
        with (patch.object(reader, '_runtime_health', return_value='missing=base_library.zip'),
              patch.object(reader, '_start') as start):
            reader.snapshot(scope=1)
        start.assert_not_called()
        self.assertTrue(reader.disabled)
        self.assertIn('missing=base_library.zip', reader.diagnostic)

    def test_failed_child_exit_code_is_preserved_before_cleanup(self):
        reader, process = self.fixture()
        process.exitcode = 1
        with patch.object(reader, '_receive', side_effect=EOFError):
            reader.snapshot(scope=1)
        self.assertIn('exit=1', reader.diagnostic)


if __name__ == '__main__':
    unittest.main()
