"""Opt-in NativeEdit TargetLease checks; pure API stubs, no real Win32 calls."""
import ctypes
from ctypes import wintypes as w
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from early_native_edit import NativeEditBridge, NativeTargetLease


class FakeWin32:
    def __init__(self):
        self.windows = {
            90: (500, 700, 'Notepad'),
            100: (500, 700, 'RichEdit50W'),
            200: (600, 800, 'RichEdit50W'),
        }
        self.focus = 100
        self.focus_epoch = 1
        self.safety = 'safe'
        self.hkl = 0x0409
        self.style = 0
        self.sends = []
        self.layout_queries = []
        self.send_hook = None
        self.return_value = 1

    def IsWindow(self, hwnd):
        return int(hwnd) in self.windows

    def IsChild(self, top, focus):
        return int(top) == 90 and int(focus) == 100

    def GetClassNameW(self, hwnd, buf, length):
        buf.value = self.windows[int(hwnd)][2]
        return len(buf.value)

    def GetWindowThreadProcessId(self, hwnd, pid_ptr):
        pid, tid, _ = self.windows[int(hwnd)]
        ctypes.cast(pid_ptr, ctypes.POINTER(w.DWORD)).contents.value = pid
        return tid

    def GetWindowLongPtrW(self, hwnd, index):
        return self.style

    def GetKeyboardLayout(self, tid):
        self.layout_queries.append(int(tid))
        return self.hkl

    def SendMessageTimeoutW(self, hwnd, message, wp, lp, flags, timeout, result):
        self.sends.append((int(hwnd), int(message), int(flags), int(timeout)))
        if self.send_hook is not None:
            self.send_hook()
        return self.return_value


def make_bridge(api, *, enabled=True, initial_epoch=None):
    bridge = NativeEditBridge.__new__(NativeEditBridge)
    bridge.context = SimpleNamespace(top=90, focus=100, password_revision=7, hkl=0x0409)
    bridge.off_hook = True
    bridge.u = api
    bridge.experimental_target_lease = enabled
    bridge.target_lease = None
    bridge.phase = 'preflight'
    bridge.started = 0
    bridge.write_started = bridge.timeout = bridge.budget_exceeded = False
    bridge.trace = []
    bridge.failure = ''
    bridge.check_deadline = lambda: None  # Time budget not under test.
    epoch = api.focus_epoch if initial_epoch is None else initial_epoch
    bridge.still_valid = lambda: (
        api.focus == 100 and api.focus_epoch == epoch and api.safety == 'safe')
    return bridge


class NativeTargetLeaseTests(unittest.TestCase):
    def test_changed_password_style_blocks_read_before_gettext_dispatch(self):
        api = FakeWin32()
        bridge = make_bridge(api)
        with bridge:
            bridge.msg(0xB0)  # Selection read before the field changes.
            api.style = 0x20
            with self.assertRaisesRegex(ValueError, 'password-or-readonly'):
                bridge.msg(0xD, 256, 0)
        self.assertEqual([row[1] for row in api.sends], [0xB0])

    def test_changed_guard_blocks_read_as_well_as_mutation(self):
        api = FakeWin32()
        bridge = make_bridge(api)
        with bridge:
            api.safety = 'password'
            with self.assertRaisesRegex(ValueError, 'target-scope-changed'):
                bridge.msg(0xD, 256, 0)
        self.assertFalse(api.sends)

    def test_destination_stays_bound_if_context_reference_changes_after_check(self):
        # Adapter hardening, not a claim that production frozen FocusContext
        # is mutable or that the numeric HWND itself cannot be reused.
        api = FakeWin32()
        bridge = make_bridge(api)
        with bridge:
            def validate():
                bridge.context = SimpleNamespace(top=90, focus=200)
                return True
            with patch.object(bridge, '_experimental_verify_target', side_effect=validate):
                bridge.msg(0x102, ord('a'), 1)
        self.assertEqual(api.sends[0][0], 100)

    def test_default_mode_unchanged_address_and_flags(self):
        api = FakeWin32()
        bridge = make_bridge(api, enabled=False)
        with bridge:
            self.assertIsNone(bridge.target_lease)
            bridge.msg(0x102, ord('е'), 1)
            self.assertIsNone(bridge.check_identity())
        self.assertEqual(api.sends, [(100, 0x102, 3, 35)])

    def test_opt_in_immutable_lease_bound_to_original_field(self):
        api = FakeWin32()
        bridge = make_bridge(api)
        with bridge:
            self.assertIsInstance(bridge.target_lease, NativeTargetLease)
            self.assertEqual((bridge.target_lease.hwnd, bridge.target_lease.process_id,
                              bridge.target_lease.thread_id, bridge.target_lease.class_name,
                              bridge.target_lease.password_revision, bridge.target_lease.source_hkl),
                             (100, 500, 700, 'richedit50w', 7, 0x0409))
            self.assertEqual(bridge.target_lease.layout_thread_id, 700)
            with self.assertRaises(AttributeError):
                bridge.target_lease.hwnd = 200
            bridge.msg(0xB1, 0, 6)
            bridge.msg(0x102, ord('п'), 1)
            bridge.msg(0x458)
            bridge.msg(0xD)
        self.assertEqual([entry[0:3] for entry in api.sends],
                         [(100, 0xB1, 0x23), (100, 0x102, 0x23),
                          (100, 0x458, 0x23), (100, 0xD, 0x23)])

    def test_hkl_is_read_from_layout_owner_not_assumed_focus_thread(self):
        api = FakeWin32()
        bridge = make_bridge(api)
        bridge.context.thread_id = 899
        with bridge:
            self.assertEqual(bridge.target_lease.thread_id, 700)
            self.assertEqual(bridge.target_lease.layout_thread_id, 899)
            bridge.msg(0x102, ord('р'), 1)
        self.assertEqual(api.layout_queries, [899])

    def test_opt_in_em_replacesel_counts_as_mutation_and_uses_write_timeout(self):
        # This command is not used by live NativeEdit; modeled for a future
        # addressed adapter only, without altering Undo semantics.
        api = FakeWin32()
        bridge = make_bridge(api)
        with bridge:
            bridge.msg(0xC2, 1, 0)
        self.assertEqual(api.sends, [(100, 0xC2, 0x23, 35)])
        self.assertTrue(bridge.write_started)

    def test_no_mutation_without_entered_lease(self):
        api = FakeWin32()
        bridge = make_bridge(api)
        with self.assertRaisesRegex(ValueError, 'target-lease-not-initialized'):
            bridge.msg(0x102, ord('а'), 1)
        self.assertFalse(bridge.write_started)
        self.assertFalse(api.sends)

    def test_focus_switch_and_aba_refuse_before_dispatched_mutation(self):
        for trace in ((200,), (200, 100)):
            with self.subTest(trace=trace):
                api = FakeWin32()
                bridge = make_bridge(api)
                with bridge:
                    for focus in trace:
                        api.focus = focus
                        api.focus_epoch += 1
                    with self.assertRaisesRegex(ValueError, 'target-scope-changed'):
                        bridge.msg(0xB1, 0, 6)
                self.assertFalse(bridge.write_started)
                self.assertFalse(api.sends)

    def test_exact_class_pid_and_hkl_changes_fail_before_send(self):
        for change, expected in (
                (lambda a: a.windows.__setitem__(100, (500, 700, 'RichEditD2DPT')),
                 'target-identity-changed'),
                (lambda a: a.windows.__setitem__(100, (501, 700, 'RichEdit50W')),
                 'field-owner-changed'),
                (lambda a: setattr(a, 'hkl', 0x0419), 'target-layout-changed'),
                (lambda a: setattr(a, 'style', 0x20), 'password-or-readonly'),
                (lambda a: a.windows.pop(100), 'window-closed'),
        ):
            with self.subTest(expected=expected):
                api = FakeWin32()
                bridge = make_bridge(api)
                with bridge:
                    change(api)
                    with self.assertRaisesRegex(ValueError, expected):
                        bridge.msg(0x102, ord('а'), 1)
                self.assertFalse(api.sends)
                self.assertFalse(bridge.write_started)

    def test_unknown_or_failing_guard_never_mutates(self):
        for safety in ('password', 'unknown', 'stale'):
            api = FakeWin32()
            bridge = make_bridge(api)
            with bridge:
                api.safety = safety
                with self.assertRaisesRegex(ValueError, 'target-scope-changed'):
                    bridge.msg(0x102, ord('а'), 1)
            self.assertFalse(api.sends)
        api = FakeWin32()
        bridge = make_bridge(api)
        with bridge:
            bridge.still_valid = lambda: (_ for _ in ()).throw(TimeoutError('provider'))
            with self.assertRaises(TimeoutError):
                bridge.msg(0x102, ord('а'), 1)
        self.assertFalse(api.sends)

    def test_send_timeout_or_exception_is_uncertain_and_never_replayed_here(self):
        for error in ('timeout', 'exception'):
            with self.subTest(error=error):
                api = FakeWin32()
                bridge = make_bridge(api)
                with bridge:
                    if error == 'timeout':
                        api.return_value = 0
                        with self.assertRaises(TimeoutError):
                            bridge.msg(0x102, ord('а'), 1)
                    else:
                        def fail():
                            raise OSError('simulated-target-error')
                        api.send_hook = fail
                        with self.assertRaises(OSError):
                            bridge.msg(0x102, ord('а'), 1)
                self.assertEqual(len(api.sends), 1)
                self.assertTrue(bridge.write_started)
                self.assertEqual(api.sends[0][2], 0x23)

    def test_focus_moves_after_final_validation_before_dispatch_remains_unresolved(self):
        # Caller-side preflight cannot atomically bind field lifetime.
        api = FakeWin32()
        bridge = make_bridge(api)
        with bridge:
            api.send_hook = lambda: (
                setattr(api, 'focus', 200),
                setattr(api, 'focus_epoch', 2))
            bridge.msg(0x102, ord('а'), 1)
        self.assertEqual(api.sends[0][0], 100)  # Stable numeric HWND only.
        self.assertEqual(api.focus, 200)

    def test_same_hwnd_reuse_inside_dispatch_is_not_a_solved_guarantee(self):
        api = FakeWin32()
        bridge = make_bridge(api)
        with bridge:
            def reuse():
                # Same numeric HWND, owner, and class: impossible to distinguish
                # solely from these observations at caller-side preflight.
                api.windows[100] = (500, 700, 'RichEdit50W')
            api.send_hook = reuse
            bridge.msg(0x102, ord('а'), 1)
        self.assertEqual(api.sends[0][0], 100)
        self.assertTrue(bridge.write_started)


if __name__ == '__main__':
    unittest.main()
