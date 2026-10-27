import unittest
from threading import Thread

from native_transaction_queue import NativeTransactionQueue, PhysicalKey


class NativeTransactionQueueTests(unittest.TestCase):
    def key(self, vk, message=0x100, trigger=False):
        return PhysicalKey(vk, vk, 0, message, trigger)

    def test_serializes_events_and_prevents_two_transactions(self):
        gate = NativeTransactionQueue()
        self.assertTrue(gate.begin(self.key(0x20, trigger=True)))
        self.assertFalse(gate.begin())
        self.assertTrue(gate.capture(self.key(0x41)))
        self.assertTrue(gate.capture(self.key(0x41, 0x101)))
        self.assertEqual([e.vk for e in gate.take()], [0x20, 0x41, 0x41])
        self.assertTrue(gate.close_if_drained())
        self.assertFalse(gate.capture(self.key(0x42)))

    def test_arriving_during_replay_stays_held_in_order(self):
        gate = NativeTransactionQueue()
        self.assertTrue(gate.begin())
        gate.capture(self.key(0x41))
        first = gate.take()
        gate.capture(self.key(0x42))
        self.assertFalse(gate.close_if_drained())
        self.assertEqual(first, [self.key(0x41)])
        self.assertEqual(gate.take(), [self.key(0x42)])
        self.assertTrue(gate.close_if_drained())

    def test_bounded_queue_records_overflow_without_unlimited_allocation(self):
        gate = NativeTransactionQueue(limit=2)
        self.assertTrue(gate.begin())
        self.assertTrue(gate.capture(self.key(0)))
        self.assertTrue(gate.capture(self.key(1)))
        for i in range(2, 10):
            self.assertFalse(gate.capture(self.key(i)))
        self.assertTrue(gate.overflow)
        self.assertEqual(gate.aborted_reason, 'buffer-overflow')
        self.assertFalse(gate.active)
        self.assertTrue(gate.busy)
        self.assertFalse(gate.begin())  # Cannot overlap old worker cleanup.
        self.assertEqual(len(gate.take()), 2)
        self.assertTrue(gate.close_if_drained())
        self.assertTrue(gate.begin())

    def test_focus_abort_stops_capture_without_allowing_overlapping_job(self):
        gate = NativeTransactionQueue()
        self.assertTrue(gate.begin(self.key(0x20, trigger=True), target_scope=('A', 1)))
        self.assertTrue(gate.capture(self.key(0x41)))
        self.assertEqual(gate.abort('focus-changed'), 2)
        self.assertFalse(gate.active)
        self.assertEqual(gate.aborted_reason, 'focus-changed')
        self.assertFalse(gate.capture(self.key(0x42)))
        self.assertFalse(gate.begin())
        self.assertEqual(len(gate.close()), 2)
        self.assertFalse(gate.busy)

    def test_close_recovers_initial_and_captured_events(self):
        gate = NativeTransactionQueue()
        gate.begin(self.key(0x20, trigger=True))
        gate.capture(self.key(0x20, 0x101))
        self.assertEqual(len(gate.close()), 2)
        self.assertFalse(gate.active)

    def test_trigger_keyup_is_identified_once_without_dropping_key_repeats(self):
        gate = NativeTransactionQueue()
        self.assertTrue(gate.begin(self.key(0x20, trigger=True)))
        gate.capture(self.key(0x20, 0x101))
        gate.capture(self.key(0x20))
        gate.capture(self.key(0x20, 0x101))
        self.assertEqual([e.trigger for e in gate.take()], [True, True, False, False])

    def test_trigger_key_repeat_before_release_is_marked_as_hotkey(self):
        gate = NativeTransactionQueue()
        gate.begin(self.key(0x13, trigger=True), command_trigger=True)
        gate.capture(self.key(0x13))
        gate.capture(self.key(0x13))
        gate.capture(self.key(0x13, 0x101))
        self.assertTrue(all(e.trigger for e in gate.take()))

    def test_printable_trigger_keeps_repeat_down_and_balancing_keyup(self):
        gate = NativeTransactionQueue()
        gate.begin(self.key(0x42, trigger=True))
        gate.capture(self.key(0x42))
        gate.capture(self.key(0x42))
        gate.capture(self.key(0x42, 0x101))
        self.assertTrue(gate.trigger_repeated)
        self.assertEqual([e.trigger for e in gate.take()],
                         [True, False, False, False])

    def test_printable_trigger_without_repeat_suppresses_only_original_keyup(self):
        gate = NativeTransactionQueue()
        gate.begin(self.key(0x42, trigger=True))
        gate.capture(self.key(0x42, 0x101))
        self.assertEqual([e.trigger for e in gate.take()], [True, True])

    def test_irreversible_trigger_marker_survives_worker_failure(self):
        gate = NativeTransactionQueue()
        gate.begin(self.key(0x42, trigger=True))
        gate.mark_trigger_irreversible()
        self.assertTrue(gate.trigger_irreversible)
        gate.close()
        self.assertTrue(gate.trigger_irreversible)
        gate.begin(self.key(0x43, trigger=True))
        self.assertFalse(gate.trigger_irreversible)
