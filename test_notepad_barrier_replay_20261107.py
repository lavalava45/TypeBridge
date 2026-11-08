"""Pure physical-input barrier tests; never start TypeBridge or inject input."""
import unittest
from test_app_controls import key as hook_key
from test_offhook_native import OffHookNativeTests
from typebridge_app import VK_SPACE, WM_KEYDOWN, WM_KEYUP

from native_transaction_queue import (
    NativeTransactionQueue, PhysicalKey, MODIFIER_VKS, KEYUP_MESSAGES,
)


def key(vk, message=0x100, *, trigger=False, scan=None):
    return PhysicalKey(vk, vk if scan is None else scan, 0, message, trigger)


class NotepadBarrierReplayTests(unittest.TestCase):
    def test_hook_space_release_after_own_layout_change_is_not_a_false_focus_abort(self):
        hook = OffHookNativeTests.fixture(self)
        original = hook.context()
        gate = hook.native_queue
        trigger = PhysicalKey(VK_SPACE, 57, 0, WM_KEYDOWN, True)
        self.assertTrue(gate.begin(trigger, target_scope=hook._native_scope(original)))
        # The editor's HKL/UIA lease changed because correction completed.
        # The original physical Space KEYUP cannot be a new field edit.
        hook.context = lambda: None
        self.assertEqual(hook_key(hook, VK_SPACE, WM_KEYUP), 1)
        self.assertFalse(gate.aborted_reason)
        hook._drain_native_input(original, gate, trigger, True)
        self.assertFalse(hook.native_edit_disabled)
        self.assertFalse(hook._native_blocked(original))
        self.assertIsNone(hook.native_pending_recovery)
        hook.user32.SendInput.assert_not_called()

    def test_hook_other_character_still_aborts_on_changed_field(self):
        hook = OffHookNativeTests.fixture(self)
        original = hook.context()
        gate = hook.native_queue
        trigger = PhysicalKey(VK_SPACE, 57, 0, WM_KEYDOWN, True)
        gate.begin(trigger, target_scope=hook._native_scope(original))
        hook.context = lambda: None
        self.assertEqual(hook_key(hook, ord('A'), WM_KEYDOWN), 0)
        self.assertEqual(gate.aborted_reason, 'focus-changed')

    def test_post_commit_layout_change_does_not_turn_original_space_keyup_into_input(self):
        gate = NativeTransactionQueue()
        self.assertTrue(gate.begin(key(0x20, trigger=True),
                                   target_scope=('notepad++', 'scintilla', 3),
                                   expected_hkl=0x04190419,
                                   expected_language='ru'))
        # The job has already committed SendInput and confirmed EN HKL.
        gate.expected_hkl, gate.expected_language = 0x04090409, 'en'
        # The matching release is the ONLY buffered physical event. It is
        # consumed before a stale post-commit app context could abort scope.
        self.assertTrue(gate.capture_trigger_keyup(key(0x20, 0x101)))
        held = gate.take()
        self.assertEqual([(e.vk, e.message, e.trigger) for e in held],
                         [(0x20, 0x100, True), (0x20, 0x101, True)])
        self.assertFalse(gate.aborted_reason)
        self.assertTrue(gate.close_if_drained())

    def test_normal_keyups_and_other_keys_must_not_bypass_scope_checks(self):
        gate = NativeTransactionQueue()
        gate.begin(key(0x20, trigger=True))
        self.assertFalse(gate.capture_trigger_keyup(key(0x41, 0x101)))
        self.assertFalse(gate.capture_trigger_keyup(key(0x41)))
        self.assertFalse(gate.capture_trigger_keyup(key(0x20)))
        self.assertEqual(len(gate.take()), 1)
        # True following physical typing continues to require capture() and
        # an app-level same-field check: never bless it as a trigger release.
        self.assertTrue(gate.capture(key(0x41)))
        self.assertTrue(gate.capture(key(0x41, 0x101)))
        self.assertEqual([e.vk for e in gate.take()], [0x41, 0x41])

    def test_repeat_keeps_trigger_keyup_for_real_printable_input(self):
        gate = NativeTransactionQueue()
        gate.begin(key(0x20, trigger=True), command_trigger=False)
        self.assertTrue(gate.capture(key(0x20)))
        self.assertTrue(gate.capture_trigger_keyup(key(0x20, 0x101)))
        held = gate.take()
        self.assertEqual([e.trigger for e in held], [True, False, False])

    def test_command_hotkey_repeats_are_all_trigger_events(self):
        gate = NativeTransactionQueue()
        gate.begin(key(0x13, trigger=True), command_trigger=True)
        self.assertTrue(gate.capture(key(0x13)))
        self.assertTrue(gate.capture_trigger_keyup(key(0x13, 0x101)))
        self.assertTrue(all(e.trigger for e in gate.take()))

    def test_no_release_interception_after_abort_or_close(self):
        gate = NativeTransactionQueue()
        gate.begin(key(0x20, trigger=True))
        gate.abort('focus-changed')
        self.assertFalse(gate.capture_trigger_keyup(key(0x20, 0x101)))
        gate.close()
        self.assertFalse(gate.capture_trigger_keyup(key(0x20, 0x101)))

    def test_modifier_keyups_are_never_swallowed_by_special_trigger_capture(self):
        gate = NativeTransactionQueue()
        gate.begin(key(0x20, trigger=True))
        for vk in MODIFIER_VKS:
            for message in KEYUP_MESSAGES:
                with self.subTest(vk=vk, message=message):
                    self.assertFalse(gate.capture_trigger_keyup(key(vk, message)))
        self.assertFalse(gate.aborted_reason)
        self.assertEqual(gate.modifier_states, {})

    def test_matching_release_cannot_be_double_consumed(self):
        gate = NativeTransactionQueue()
        gate.begin(key(0x20, trigger=True))
        self.assertTrue(gate.capture_trigger_keyup(key(0x20, 0x101)))
        self.assertFalse(gate.capture_trigger_keyup(key(0x20, 0x101)))


if __name__ == '__main__':
    unittest.main()
