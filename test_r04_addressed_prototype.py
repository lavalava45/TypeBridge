"""Deterministic R04 models. No OS APIs, hooks, files, or physical input."""
from dataclasses import dataclass
from types import SimpleNamespace
from time import perf_counter
import unittest
from unittest.mock import Mock

from experiments.r04_addressed_richedit import (
    AddressedRichEditExperiment, EM_REPLACESEL, EM_SETSEL, SMTO_FLAGS, TargetLease)


@dataclass
class Field:
    text: str
    caret: int
    selection: tuple
    pid: int = 100
    tid: int = 200
    cls: str = 'RichEdit50W'


class FakeWindows:
    def __init__(self):
        self.fields = {10: Field('ghbdtn', 6, (6, 6)),
                       20: Field('B_UNCHANGED', 11, (11, 11))}
        self.focus, self.epoch = 10, 1
        self.hkl, self.revision, self.safety = 0x0409, 5, 'safe'
        self.calls, self.before_replace = [], None
        self.fail_mode = None

    def identity(self, hwnd):
        f = self.fields.get(hwnd)
        return (f.pid, f.tid, f.cls) if f else None

    def context(self):
        return self.focus, self.epoch, self.hkl, self.revision, self.safety

    def document(self, hwnd):
        f = self.fields[hwnd]
        return f.text, f.caret, f.selection

    def change_focus(self, hwnd, *, safety='safe', revision=None):
        self.focus, self.safety = hwnd, safety
        self.epoch += 1
        if revision is not None:
            self.revision = revision

    def message(self, hwnd, code, wp, lp, flags):
        self.calls.append((hwnd, code, flags))
        assert flags == SMTO_FLAGS
        if self.fail_mode == 'selection-failure' and code == EM_SETSEL:
            return False
        if code == EM_SETSEL:
            self.fields[hwnd].selection = (wp, lp)
            return True
        assert code == EM_REPLACESEL
        if self.before_replace:
            self.before_replace()  # AFTER the last caller-side validity check.
        f = self.fields[hwnd]     # Destination is the bound HWND, NOT focus.
        start, end = f.selection
        if self.fail_mode == 'before-write-timeout':
            return False
        replacement = lp
        if self.fail_mode == 'partial-change':
            replacement = lp[:2]  # Model a nonconforming/partial provider.
        f.text = f.text[:start] + replacement + f.text[end:]
        f.caret = start + len(replacement)
        f.selection = (f.caret, f.caret)
        if self.fail_mode == 'after-write-timeout':
            return False
        if self.fail_mode == 'write-exception':
            raise RuntimeError('simulated-provider-failure')
        return True

    def global_sendinput_model(self, actions, *, accepted=None):
        """Counterexample: deliberately model SendInput's global recipient.

        This is NOT a call to real SendInput or a Windows event-loop emulator.
        """
        count = len(actions) if accepted is None else accepted
        for kind, value in actions[:count]:
            f = self.fields[self.focus]
            if kind == 'backspace' and f.caret:
                f.text = f.text[:f.caret-1] + f.text[f.caret:]
                f.caret -= 1
            elif kind == 'char':
                f.text = f.text[:f.caret] + value + f.text[f.caret:]
                f.caret += len(value)
            f.selection = (f.caret, f.caret)
        return count


def candidate(api):
    return AddressedRichEditExperiment(api, TargetLease(
        10, 100, 200, 'RichEdit50W', api.epoch, 0x0409, 5))


class AddressedR04Tests(unittest.TestCase):
    def test_existing_native_and_scintilla_adapters_bind_the_original_hwnd(self):
        from early_native_edit import NativeEditBridge
        from early_scintilla import ScintillaEarlyBridge
        for bridge_type, code in ((NativeEditBridge, 0x102), (ScintillaEarlyBridge, 2194)):
            with self.subTest(bridge=bridge_type.__name__):
                api = FakeWindows()
                bridge = bridge_type.__new__(bridge_type)
                bridge.context = SimpleNamespace(focus=10)
                bridge.u = Mock()
                bridge.started = perf_counter()
                bridge.timeout = bridge.budget_exceeded = bridge.write_started = False
                bridge.phase = 'write-replacement'
                bridge.trace = []
                bridge.check_deadline = Mock()
                def receive(hwnd, message, wp, lp, flags, timeout, output):
                    api.change_focus(20, safety='password', revision=6)
                    self.assertEqual(hwnd, 10)
                    self.assertEqual(message, code)
                    return 1
                bridge.u.SendMessageTimeoutW.side_effect = receive
                bridge.msg(code, 1, 1)
                bridge.u.SendMessageTimeoutW.assert_called_once()
                self.assertEqual(api.fields[20].text, 'B_UNCHANGED')

    def test_baseline_global_full_and_partial_touch_b(self):
        actions = [('backspace', '')] * 6 + [('char', c) for c in 'привет']
        for safety in ('password', 'safe', 'unknown'):
            for accepted in (len(actions), 2):
                with self.subTest(safety=safety, accepted=accepted):
                    api = FakeWindows()
                    api.change_focus(20, safety=safety, revision=6)
                    count = api.global_sendinput_model(actions, accepted=accepted)
                    self.assertEqual(count, accepted)
                    self.assertEqual(api.fields[10].text, 'ghbdtn')
                    self.assertNotEqual(api.fields[20].text, 'B_UNCHANGED')

    def test_addressed_full_write_changes_only_a_even_when_focus_moves_at_dispatch(self):
        for safety in ('password', 'safe', 'unknown'):
            with self.subTest(safety=safety):
                api = FakeWindows()
                api.before_replace = lambda: api.change_focus(20, safety=safety, revision=6)
                result = candidate(api).commit('ghbdtn', 'привет', held_tail=('X', 'Y'))
                self.assertEqual(api.fields[10].text, 'привет')
                self.assertEqual(api.fields[20].text, 'B_UNCHANGED')
                self.assertEqual(result.state, 'uncertain')  # No proof of safe completion.
                self.assertEqual(result.tail_for_manual_recovery, ('X', 'Y'))
                self.assertFalse(result.tail_ready_for_ordered_replay)
                self.assertEqual([hwnd for hwnd, _, _ in api.calls], [10, 10])

    def test_addressed_verified_when_stable_preserves_tail_order(self):
        api = FakeWindows()
        tx = candidate(api)
        result = tx.commit('ghbdtn', 'привет', held_tail=('X', 'Y', 'Z'))
        self.assertEqual(result.state, 'verified')
        self.assertEqual(result.tail_ready_for_ordered_replay, ('X', 'Y', 'Z'))
        self.assertEqual(api.fields[20].text, 'B_UNCHANGED')
        self.assertEqual(tx.commit('ghbdtn', 'привет').reason, 'duplicate-attempt')
        self.assertEqual(len(api.calls), 2)

    def test_focus_changes_before_first_mutation_and_aba_refuse(self):
        for trace in ((20,), (20, 10)):
            with self.subTest(trace=trace):
                api = FakeWindows()
                tx = candidate(api)
                for hwnd in trace:
                    api.change_focus(hwnd)
                result = tx.commit('ghbdtn', 'привет')
                self.assertEqual(result.state, 'refused')
                self.assertFalse(api.calls)
                self.assertEqual(api.fields[20].text, 'B_UNCHANGED')

    def test_delayed_metadata_and_provider_error_fail_closed(self):
        for safety in ('unknown', 'password', 'stale'):
            api = FakeWindows()
            api.safety = safety
            self.assertEqual(candidate(api).commit('ghbdtn', 'привет').state, 'refused')
            self.assertFalse(api.calls)
        api = FakeWindows()
        api.context = lambda: (_ for _ in ()).throw(RuntimeError('provider'))
        self.assertEqual(candidate(api).commit('ghbdtn', 'привет').state, 'refused')
        self.assertFalse(api.calls)
        self.assertEqual(api.fields[20].text, 'B_UNCHANGED')

    def test_source_read_error_refuses_without_mutation(self):
        api = FakeWindows()
        api.document = lambda hwnd: (_ for _ in ()).throw(TimeoutError('mock-timeout'))
        result = candidate(api).commit('ghbdtn', 'привет', held_tail=('K',))
        self.assertEqual((result.state, result.reason), ('refused', 'source-read-unavailable'))
        self.assertEqual(result.tail_for_manual_recovery, ('K',))
        self.assertFalse(api.calls)
        self.assertEqual(api.fields[20].text, 'B_UNCHANGED')

    def test_same_hwnd_two_dom_elements_are_not_addressable_with_hwnd_only(self):
        api = FakeWindows()
        api.fields[10].cls = 'Chrome_RenderWidgetHostHWND'
        dom_text = {'uia:1,100': 'ghbdtn', 'uia:1,101': 'B_UNCHANGED'}
        # Both DOM elements share the same HWND; a global keyboard packet
        # goes to the newly focused element, not the old UIA Runtime ID.
        dom_focus = 'uia:1,101'
        actions = [('backspace', '')] * 6 + [('char', c) for c in 'привет']
        text = dom_text[dom_focus]
        for kind, value in actions:
            text = text[:-1] if kind == 'backspace' and text else text + (value if kind == 'char' else '')
        dom_text[dom_focus] = text
        self.assertEqual(dom_text['uia:1,100'], 'ghbdtn')
        self.assertNotEqual(dom_text['uia:1,101'], 'B_UNCHANGED')
        self.assertEqual(candidate(api).commit('ghbdtn', 'привет').state, 'refused')
        self.assertFalse(api.calls)

    def test_aba_during_addressed_dispatch_is_uncertain_not_verified(self):
        api = FakeWindows()
        def bounce():
            api.change_focus(20, safety='password', revision=6)
            api.change_focus(10, revision=5)
        api.before_replace = bounce
        result = candidate(api).commit('ghbdtn', 'привет', held_tail=('Q',))
        self.assertEqual(result.state, 'uncertain')
        self.assertEqual(result.tail_for_manual_recovery, ('Q',))
        self.assertEqual(api.fields[20].text, 'B_UNCHANGED')
        self.assertEqual(api.fields[10].text, 'привет')

    def test_same_hwnd_dom_and_native_control_replacement_rejected(self):
        api = FakeWindows()
        api.fields[10].cls = 'Chrome_RenderWidgetHostHWND'
        self.assertEqual(candidate(api).commit('ghbdtn', 'привет').state, 'refused')
        self.assertFalse(api.calls)

    def test_destroyed_control_before_preflight_refuses_without_mutation(self):
        api = FakeWindows()
        tx = candidate(api)
        del api.fields[10]
        self.assertEqual(tx.commit('ghbdtn', 'привет').state, 'refused')
        self.assertFalse(api.calls)
        self.assertEqual(api.fields[20].text, 'B_UNCHANGED')

    def test_hwnd_reuse_during_dispatch_is_unresolved_counterexample(self):
        # Adversarial MODEL, NOT proof that Win32 reuses handles at this point.
        # A numerical HWND + identical class/PID/TID is not a lifetime token.
        api = FakeWindows()
        original = api.fields[10]
        def replace_control():
            api.fields[10] = Field('ghbdtn', 6, (0, 6))
        api.before_replace = replace_control
        result = candidate(api).commit('ghbdtn', 'привет')
        self.assertIsNot(api.fields[10], original)
        self.assertEqual(api.fields[10].text, 'привет')
        self.assertEqual(result.state, 'verified')  # Unsafe false positive in this model.
        self.assertEqual(api.fields[20].text, 'B_UNCHANGED')
        api = FakeWindows()
        api.fields[10].pid = 901
        self.assertEqual(candidate(api).commit('ghbdtn', 'привет').state, 'refused')
        self.assertFalse(api.calls)

    def test_timeout_partial_exception_never_retry_or_redirect_to_b(self):
        for mode in ('selection-failure', 'before-write-timeout',
                     'partial-change', 'after-write-timeout', 'write-exception'):
            with self.subTest(mode=mode):
                api = FakeWindows()
                api.fail_mode = mode
                tx = candidate(api)
                result = tx.commit('ghbdtn', 'привет', held_tail=('A', 'B'))
                self.assertEqual(result.state, 'uncertain')
                self.assertEqual(result.tail_for_manual_recovery, ('A', 'B'))
                self.assertFalse(result.tail_ready_for_ordered_replay)
                self.assertEqual(api.fields[20].text, 'B_UNCHANGED')
                calls = len(api.calls)
                self.assertEqual(tx.commit('ghbdtn', 'привет').state, 'refused')
                self.assertEqual(len(api.calls), calls)

    def test_caret_change_refuses_before_write(self):
        api = FakeWindows()
        api.fields[10].caret = 3
        api.fields[10].selection = (3, 3)
        self.assertEqual(candidate(api).commit('ghbdtn', 'привет').state, 'refused')
        self.assertFalse(api.calls)


if __name__ == '__main__':
    unittest.main()
