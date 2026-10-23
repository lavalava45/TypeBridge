"""Queue / partial-send counterexamples. Never invokes SendInput or OS hooks."""
import unittest
from itertools import product
from early_keyboard_probe import Field, Key, Transaction, plan, marker, decode_marker, input_batch


class KeyboardProbeTests(unittest.TestCase):
    def operation(self):
        return plan(Field((1, 2), 'start флг end', 9, 'ru'), 'флгщ', 'שלום', 'he')

    def delivered(self):
        transaction = Transaction(self.operation())
        transaction.submitted(len(transaction.plan.events))
        for index in range(len(transaction.plan.events)):
            self.assertTrue(transaction.own_event(index, (1, 2)))
        return transaction

    def test_six_directions_replace_only_prefix_and_never_append_held_character(self):
        for source, typed, replacement, target in (
                ('ru', 'руддщ', 'hello', 'en'), ('he', 'יקךךם', 'hello', 'en'),
                ('en', 'ghbdtn', 'привет', 'ru'), ('he', 'עינגאמ', 'привет', 'ru'),
                ('en', 'akuo', 'שלום', 'he'), ('ru', 'флгщ', 'שלום', 'he')):
            field = Field((1, 2), '🙂\n' + typed[:-1] + ' tail', len(typed)+1, source)
            operation = plan(field, typed, replacement, target)
            self.assertEqual(operation.expected.text, '🙂\n' + replacement + ' tail')
            self.assertEqual(sum(key.vk == 8 and not key.flags for key in operation.events), len(typed)-1)
            self.assertEqual(''.join(chr(key.scan) for key in operation.events if key.flags == 4), replacement)

    def test_successful_send_and_seen_keyups_are_not_text_confirmation(self):
        transaction = self.delivered()
        self.assertEqual(transaction.state, 'verifying')
        with self.assertRaises(RuntimeError): transaction.release((1, 2))
        self.assertFalse(transaction.confirm(transaction.plan.source))
        self.assertEqual(transaction.state, 'uncertain')

    def test_all_tail_arrival_positions_preserve_order_and_single_release(self):
        operation = self.operation()
        tail = (Key(vk=65), Key(vk=65, flags=2), Key(vk=32), Key(vk=8), Key(vk=16))
        for position in range(len(operation.events)+1):
            transaction = Transaction(operation)
            transaction.submitted(len(operation.events))
            for index in range(len(operation.events)+1):
                if index == position:
                    for event in tail: self.assertTrue(transaction.physical_event(event, (1, 2)))
                if index < len(operation.events): transaction.own_event(index, (1, 2))
            self.assertTrue(transaction.confirm(operation.expected))
            self.assertEqual(transaction.release((1, 2)), tail)
            with self.assertRaises(RuntimeError): transaction.release((1, 2))

    def test_every_partial_count_disables_remaining_input_without_retry(self):
        operation = self.operation()
        for count in range(len(operation.events)):
            transaction = Transaction(operation)
            transaction.submitted(count)
            for index in range(count): self.assertFalse(transaction.own_event(index, (1, 2)))
            self.assertTrue(transaction.drained)
            self.assertIn(transaction.state, ('refused', 'uncertain'))
            with self.assertRaises(RuntimeError): transaction.submitted(len(operation.events))
            with self.assertRaises(RuntimeError): transaction.release((1, 2))

    def test_reentrant_delivery_then_partial_return_is_not_declared_safe(self):
        transaction = Transaction(self.operation())
        self.assertTrue(transaction.own_event(0, (1, 2)))
        transaction.submitted(1)
        self.assertEqual(transaction.state, 'uncertain')
        self.assertEqual(transaction.forwarded, 1)
        self.assertEqual(transaction.pending_keyups(), (Key(vk=8, flags=2),))

    def test_focus_loss_at_every_event_blocks_new_window_writes_and_keeps_tail(self):
        operation = self.operation()
        for change_at in range(len(operation.events)):
            transaction = Transaction(operation)
            transaction.submitted(len(operation.events))
            event = Key(vk=65)
            transaction.physical_event(event, (1, 2))
            for index in range(len(operation.events)):
                scope = (1, 2) if index < change_at else (9, 9)
                forwarded = transaction.own_event(index, scope)
                if index >= change_at and forwarded:
                    self.assertEqual(index, change_at)
                    self.assertTrue(operation.events[index].flags & 2)
            self.assertEqual(transaction.buffer, [event])
            self.assertEqual(transaction.pending_keyups(), ())
            with self.assertRaises(RuntimeError): transaction.release((9, 9))
            self.assertFalse(transaction.physical_event(Key(vk=66), (9, 9)))
            self.assertEqual(transaction.state, 'uncertain')

    def test_readback_requires_scope_caret_layout_and_entire_document(self):
        from dataclasses import replace
        expected = self.operation().expected
        for difference in ({'scope': (1, 3)}, {'caret': 0}, {'language': 'ru'},
                           {'text': expected.text+'x'}, {'selection': True}):
            transaction = self.delivered()
            self.assertFalse(transaction.confirm(replace(expected, **difference)))

    def test_preflight_refuses_selection_password_bad_prefix_and_embedded_word(self):
        from dataclasses import replace
        original = self.operation().source
        for field in (replace(original, selection=True), replace(original, sensitive=True),
                      replace(original, caret=0), replace(original, text='start Xфлг end', caret=10)):
            with self.assertRaises(ValueError): plan(field, 'флгщ', 'שלום', 'he')

    def test_modifiers_and_timeout_cancel_but_do_not_flush_pending_keys(self):
        for reason in ('modifier-changed', 'timeout'):
            transaction = Transaction(self.operation())
            transaction.submitted(len(transaction.plan.events))
            transaction.physical_event(Key(vk=65), (1, 2))
            if reason == 'modifier-changed': self.assertFalse(transaction.own_event(0, (1, 2), True))
            else: transaction.abort(reason)
            self.assertEqual(len(transaction.buffer), 1)
            with self.assertRaises(RuntimeError): transaction.release((1, 2))

    def test_marker_and_input_ABI_without_injection(self):
        for sequence, index in product((0, 1, 2**32-1), (0, 1, 65535)):
            self.assertEqual(decode_marker(marker(sequence, index)), (sequence, index))
        self.assertIsNone(decode_marker(0))
        operation = self.operation()
        batch = input_batch(operation.events, sequence=10)
        self.assertEqual(len(batch), len(operation.events))
        for index, item in enumerate(batch):
            self.assertEqual(decode_marker(item.u.ki.dwExtraInfo), (10, index))

    def test_opening_hidden_trial_does_not_send_input_install_hooks_or_focus_fields(self):
        import tkinter as tk
        from unittest.mock import patch
        import keyboard_transport_trial
        root = tk.Tk(); root.withdraw()
        try:
            with patch('keyboard_transport_trial.tk.Tk', return_value=root), \
                 patch.object(keyboard_transport_trial.Trial, 'send') as send, \
                 patch.object(keyboard_transport_trial.Trial, 'hook_loop') as hooks:
                trial = keyboard_transport_trial.Trial()
                self.assertIsNone(trial.worker)
                self.assertIsNone(trial.transaction)
                self.assertFalse(root.winfo_ismapped())
                send.assert_not_called(); hooks.assert_not_called()
        finally: root.destroy()


class GuardTests(unittest.TestCase):
    def fixture(self):
        from unittest.mock import Mock
        from keyboard_transport_trial import Trial
        trial = Trial.__new__(Trial)
        trial.u = Mock()
        trial.u.CallNextHookEx.return_value = 0
        trial.u.GetAsyncKeyState.return_value = 0
        trial.hook = None
        trial.sequence = 1
        trial.transaction = Transaction(plan(Field((1,2),'флг',3,'ru'),'флгщ','שלום','he'))
        trial.transaction.submitted(len(trial.transaction.plan.events))
        trial.scope = lambda: (1,2)
        trial.closing = trial.replaying = False
        trial.running = True
        trial.trace, trial.extra, trial.replay_seen, trial.replay_keys_down = [], [], set(), set()
        trial.cleanup_seen = 0
        return trial

    def event(self, trial, extra, vk=65, up=False):
        import ctypes
        from typebridge_app import KbdHook
        event=KbdHook(vkCode=vk,flags=0x10,dwExtraInfo=extra)
        return trial.guard(0,0x101 if up else 0x100,ctypes.addressof(event))

    def test_queued_tail_is_buffered_without_reaching_the_field(self):
        from keyboard_transport_trial import TAIL
        trial=self.fixture()
        self.assertEqual(self.event(trial,TAIL,vk=32),1)
        self.assertEqual(trial.transaction.buffer,[Key(vk=32)])
        trial.u.CallNextHookEx.assert_not_called()

    def test_own_text_does_not_reach_a_changed_field(self):
        trial=self.fixture(); trial.scope=lambda: (9,9)
        self.assertEqual(self.event(trial,marker(1,0),vk=8),1)
        self.assertEqual(trial.transaction.reason,'focus-changed')
        trial.u.CallNextHookEx.assert_not_called()

    def test_new_window_physical_input_is_not_captured(self):
        trial=self.fixture(); trial.scope=lambda: (9,9)
        self.assertEqual(self.event(trial,0,vk=65),0)
        self.assertEqual(trial.transaction.buffer,[])
        trial.u.CallNextHookEx.assert_called_once()

    def test_abort_cleanup_releases_backspace_without_replaying_a_deletion(self):
        from keyboard_transport_trial import CLEANUP
        trial=self.fixture()
        trial.transaction.keys_down.add((8,0,0))
        self.assertEqual(self.event(trial,CLEANUP,vk=8,up=True),0)
        self.assertEqual(trial.transaction.keys_down,set())
        self.assertEqual(trial.cleanup_seen,1)


if __name__ == '__main__': unittest.main()
