import unittest
from unittest.mock import Mock

from input_transport import send_with_remainder
import test_word_cycle as cycle_tests


class InputCompletionTests(unittest.TestCase):
    def test_real_hook_batch_completes_word_without_duplicate_deletion(self):
        driver = cycle_tests.WordCycleTests(); driver.setUpClass(); driver.setUp()
        calls = []
        def partial_then_finish(count, events, size):
            calls.append(count)
            accepted = 3 if len(calls) == 1 else count
            driver.inject(accepted, events[:accepted], size)
            return accepted
        driver.hook.user32.SendInput.side_effect = partial_then_finish
        driver.type('ghbdtn ')
        self.assertEqual(driver.text, 'привет ')
        self.assertEqual(driver.language, 'ru')
        self.assertEqual(len(calls), 2)
    def test_each_accepted_event_is_applied_exactly_once(self):
        events = ['back-down', 'back-up', 'char-down', 'char-up']
        for cut in (1, 2, 3):
            accepted = []
            calls = []
            def send(part):
                calls.append(part)
                n = cut if len(calls) == 1 else len(part)
                accepted.extend(part[:n])
                return n
            self.assertEqual(send_with_remainder(events, send, lambda: True), len(events))
            self.assertEqual(accepted, events)
            self.assertEqual(calls[1], events[cut:])

    def test_no_progress_or_changed_field_does_not_retry(self):
        send = Mock(return_value=0)
        self.assertEqual(send_with_remainder([1, 2], send, lambda: True), 0)
        send.assert_called_once()
        send = Mock(return_value=1)
        self.assertEqual(send_with_remainder([1, 2], send, lambda: False), 1)
        send.assert_called_once()

    def test_second_failure_is_bounded_and_preserves_exact_count(self):
        send = Mock(side_effect=[1, 0])
        self.assertEqual(send_with_remainder([1, 2, 3], send, lambda: True), 1)
        self.assertEqual(send.call_count, 2)
        send.assert_called_with([2, 3])


if __name__ == '__main__': unittest.main()
