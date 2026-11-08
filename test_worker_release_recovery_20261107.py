"""Scheduling after an aborted command: no hooks, input or user documents."""
import unittest
from dataclasses import replace
from unittest.mock import Mock

import test_offhook_native as native_tests
import test_hook_cycle_offhook as keyboard_tests
from native_transaction_queue import PhysicalKey
from typebridge_app import VK_PAUSE, WM_KEYDOWN, WM_KEYUP


class WorkerReleaseRecoveryTests(unittest.TestCase):
    def aborted_command(self, following=()):
        hook = native_tests.OffHookNativeTests.fixture(self)
        context = hook.context()
        trigger = PhysicalKey(VK_PAUSE, 0, 0, WM_KEYDOWN, True)
        gate = hook.native_queue
        gate.begin(trigger, target_scope=hook._native_scope(context), command_trigger=True)
        gate.capture(PhysicalKey(VK_PAUSE, 0, 0, WM_KEYUP))
        for event in following:
            gate.capture(event)
        gate.abort('field-revision-changed')
        hook._drain_native_input(context, gate, trigger, True)
        return hook, context

    def test_consumed_pause_never_creates_phantom_pending_recovery(self):
        hook, context = self.aborted_command()
        self.assertIsNone(hook.native_pending_recovery)
        self.assertFalse(hook.native_queue.busy)
        hook.user32.SendInput.assert_not_called()
        self.assertFalse(any(call.args[0] == 'native-recovery-available'
                             for call in hook.emit.call_args_list))

    def test_command_is_removed_but_real_following_typing_remains_recoverable(self):
        hook, context = self.aborted_command((PhysicalKey(0x41,30,0,WM_KEYDOWN),
                                             PhysicalKey(0x41,30,0,WM_KEYUP)))
        self.assertEqual([event.vk for event in hook.native_pending_recovery[1]], [0x41,0x41])
        hook.user32.SendInput.assert_not_called()

    def test_aborted_pause_does_not_block_next_browser_operation(self):
        hook, context = self.aborted_command()
        browser = replace(context, process_name='chrome.exe', focus=context.focus+1)
        hook.context = lambda: browser
        job = Mock(return_value=False)
        self.assertTrue(hook._schedule_native(browser, job))
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        job.assert_called_once()

    def test_worker_preflight_failure_clears_release_slot_without_ack_message(self):
        hook, context = keyboard_tests.GenericCycleOffHookTests.fixture(self)
        hook.context = lambda: replace(context, focus=999)
        job = Mock()
        self.assertTrue(hook._schedule_keyboard(context, job, trigger=hook._last_physical_event,
                                               drop_always=True))
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        job.assert_not_called()
        self.assertIsNone(hook._keyboard_job_release)
        self.assertIsNone(getattr(hook, 'native_pending_recovery', None))
        self.assertFalse(hook.native_queue.busy)


if __name__ == '__main__':
    unittest.main()
