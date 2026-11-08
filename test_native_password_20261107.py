"""Native COM metadata reader contracts, tested without COM or active UIA."""
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from password_fields import PasswordFieldGuard
from uia_password_native import (PasswordProbe, editable_metadata,
                                 native_style_state, wire_line)


class NativePasswordContractTests(unittest.TestCase):
    def test_native_edit_style_and_browser_metadata_policies(self):
        for cls in ('Edit', 'RichEdit20W', 'RichEdit50W', 'RichEditD2DPT'):
            self.assertEqual(native_style_state(cls, 0), 0)
            self.assertEqual(native_style_state(cls, 0x20), 1)
        self.assertEqual(native_style_state('Chrome_WidgetWin_1', 0), -1)
        self.assertFalse(editable_metadata(50030, True, True, False, True,
                                           True, 1, True))
        self.assertFalse(editable_metadata(50003, True, True, False, True,
                                           False, -1, True))
        self.assertTrue(editable_metadata(50003, True, True, True, False,
                                          False, -1, True))
        self.assertFalse(editable_metadata(50025, True, True, True, False,
                                           False, -1, True))

    def test_native_positive_password_overrules_false_uia(self):
        scope = Mock()
        scope.scope.return_value = (10, 20)
        scope.native_state.return_value = 1
        scope.shared.return_value = False
        automation = Mock()
        uia = Mock(UIA_IsPasswordPropertyId=30019)
        probe = PasswordProbe(automation, uia, scope)
        with patch.object(probe, 'prop', return_value=False):
            result = probe.query().split('|')
        self.assertEqual(result[2], 'password')
        self.assertEqual(result[5], 'win32:20')
        self.assertEqual(result[6], '0')

    def test_browser_never_inherits_win32_edit_whitelist(self):
        scope = Mock()
        scope.scope.return_value = (10, 20)
        scope.native_state.return_value = 0
        scope.shared.return_value = True
        automation = Mock()
        automation.GetFocusedElement.return_value = None
        from types import SimpleNamespace
        uia = SimpleNamespace(UIA_IsPasswordPropertyId=30019,
                              UIA_NativeWindowHandlePropertyId=30020)
        probe = PasswordProbe(automation, uia, scope)
        self.assertEqual(probe.query().split('|')[2], 'unknown')
        automation.ElementFromHandle.assert_not_called()

    def test_browser_ancestor_password_is_positive_even_when_child_is_safe(self):
        from types import SimpleNamespace
        scope = Mock()
        scope.scope.return_value = (10, 20)
        scope.native_state.return_value = -1
        scope.shared.return_value = True
        child, ancestor = Mock(), Mock()
        child.GetRuntimeId.return_value = [4, 8]
        automation = Mock()
        automation.GetFocusedElement.return_value = child
        automation.CompareElements.return_value = True
        automation.RawViewWalker.GetParentElement.side_effect = [ancestor, None]
        uia = SimpleNamespace(UIA_IsPasswordPropertyId=30019,
                              UIA_NativeWindowHandlePropertyId=30020)
        probe = PasswordProbe(automation, uia, scope)
        def property_value(element, key):
            if key == 30019:
                return element is ancestor
            if key == 30020:
                return 10
            return None
        with patch.object(probe, 'prop', side_effect=property_value):
            reply = probe.query().split('|')
        self.assertEqual(reply[2], 'password')
        self.assertEqual(reply[5], 'uia:4,8')
        self.assertEqual(reply[6], '0')

    def test_unsupported_password_property_on_unrelated_chrome_ancestor(self):
        from types import SimpleNamespace
        scope = Mock()
        scope.scope.return_value = (10, 20)
        scope.native_state.return_value = -1
        scope.shared.return_value = True
        child, ancestor = Mock(), Mock()
        child.GetRuntimeId.return_value = [4, 9]
        automation = Mock()
        automation.GetFocusedElement.return_value = child
        automation.CompareElements.return_value = True
        automation.RawViewWalker.GetParentElement.side_effect = [ancestor, None]
        uia = SimpleNamespace(UIA_IsPasswordPropertyId=30019,
                              UIA_NativeWindowHandlePropertyId=30020)
        probe = PasswordProbe(automation, uia, scope)
        def property_value(element, key):
            if key == 30020:
                return 10
            return None if element is ancestor else False
        with patch.object(probe, 'prop', side_effect=property_value), \
                patch.object(probe, 'editable', return_value=True):
            reply = probe.query().split('|')
        # Leaf reports explicit IsPassword=False; ancestor NotSupported is
        # common in Chromium, but positive ancestor True still blocks (above).
        self.assertEqual(reply[2], 'safe')
        self.assertEqual(reply[6], '1')

    def test_missing_focused_password_property_is_unknown(self):
        from types import SimpleNamespace
        scope = Mock()
        scope.scope.return_value = (10, 20)
        scope.native_state.return_value = -1
        scope.shared.return_value = True
        child = Mock()
        child.GetRuntimeId.return_value = [4, 9]
        automation = Mock()
        automation.GetFocusedElement.return_value = child
        automation.CompareElements.return_value = True
        automation.RawViewWalker.GetParentElement.return_value = None
        uia = SimpleNamespace(UIA_IsPasswordPropertyId=30019,
                              UIA_NativeWindowHandlePropertyId=30020)
        probe = PasswordProbe(automation, uia, scope)
        with patch.object(probe, 'prop', side_effect=lambda _e, key: 10 if key == 30020 else None), \
                patch.object(probe, 'editable', return_value=True):
            reply = probe.query().split('|')
        self.assertEqual(reply[2], 'unknown')
        self.assertEqual(reply[6], '0')

    def test_null_com_ancestor_pointer_is_root_not_provider_error(self):
        from types import SimpleNamespace
        scope = Mock()
        scope.scope.return_value = (10, 20)
        scope.native_state.return_value = -1
        scope.shared.return_value = True
        child = Mock()
        child.GetRuntimeId.return_value = [9, 12]
        null_pointer = MagicMock()
        null_pointer.__bool__.return_value = False
        # Distinguish null COM interface from Python None; no property access.
        null_pointer.GetCurrentPropertyValueEx.side_effect = AssertionError('null COM pointer dereferenced')
        automation = Mock()
        automation.GetFocusedElement.return_value = child
        automation.CompareElements.return_value = True
        automation.RawViewWalker.GetParentElement.return_value = null_pointer
        uia = SimpleNamespace(UIA_IsPasswordPropertyId=30019,
                              UIA_NativeWindowHandlePropertyId=30020)
        probe = PasswordProbe(automation, uia, scope)
        with patch.object(probe, 'prop', side_effect=lambda element, key: 10 if key == 30020 else False), \
                patch.object(probe, 'editable', return_value=True):
            reply = probe.query().split('|')
        self.assertEqual(reply[2], 'safe')
        self.assertEqual(reply[6], '1')
        automation.RawViewWalker.GetParentElement.assert_called_once_with(child)

    def test_positive_password_survives_missing_runtime_id(self):
        from types import SimpleNamespace
        scope = Mock()
        scope.scope.return_value = (10, 20)
        scope.native_state.return_value = -1
        scope.shared.return_value = True
        element = Mock()
        element.GetRuntimeId.side_effect = RuntimeError('provider')
        automation = Mock()
        automation.GetFocusedElement.return_value = element
        automation.CompareElements.return_value = True
        automation.RawViewWalker.GetParentElement.return_value = None
        uia = SimpleNamespace(UIA_IsPasswordPropertyId=30019,
                              UIA_NativeWindowHandlePropertyId=30020)
        probe = PasswordProbe(automation, uia, scope)
        def known_properties(_element, key):
            return 10 if key == 30020 else True
        with patch.object(probe, 'prop', side_effect=known_properties):
            reply = probe.query().split('|')
        self.assertEqual(reply[2], 'password')
        self.assertEqual(reply[6], '0')

    def test_verified_browser_focus_exposes_exact_uia_identity_and_readiness(self):
        from types import SimpleNamespace
        scope = Mock()
        scope.scope.return_value = (10, 20)
        scope.native_state.return_value = -1
        scope.shared.return_value = True
        element = Mock()
        element.GetRuntimeId.return_value = [4, 17, -2]
        automation = Mock()
        automation.GetFocusedElement.return_value = element
        automation.CompareElements.return_value = True
        automation.RawViewWalker.GetParentElement.return_value = None
        uia = SimpleNamespace(UIA_IsPasswordPropertyId=30019,
                              UIA_NativeWindowHandlePropertyId=30020)
        probe = PasswordProbe(automation, uia, scope)
        with patch.object(probe, 'prop', side_effect=lambda _e, k: 10 if k == 30020 else False), \
                patch.object(probe, 'editable', return_value=True):
            reply = probe.query().split('|')
        self.assertEqual(reply[2], 'safe')
        self.assertEqual(reply[4], 'checked')
        self.assertEqual(reply[5], 'uia:4,17,-2')
        self.assertEqual(reply[6], '1')

    def test_focus_move_during_ancestor_check_invalidates_response(self):
        scope = Mock()
        scope.scope.side_effect = [(10, 20), (10, 21)]
        scope.native_state.return_value = -1
        scope.shared.return_value = True
        element = Mock()
        automation = Mock()
        automation.GetFocusedElement.return_value = element
        automation.RawViewWalker.GetParentElement.return_value = None
        probe = PasswordProbe(automation, Mock(), scope)
        with patch.object(probe, 'prop', side_effect=lambda _e, key: 10 if key == 30020 else False):
            reply = probe.query().split('|')
        self.assertEqual(reply[2], 'unknown')
        self.assertEqual(reply[4], 'focus-change')

    def test_wire_response_recognized_and_stale_does_not_authorize(self):
        guard = PasswordFieldGuard(require_element_id=True)
        guard.ingest(wire_line(10, 20, 'safe', time.time(), 'checked', 'uia:1,2', True))
        self.assertIsNotNone(guard.permit_revision(10, 20))
        guard.ingest(wire_line(10, 20, 'safe', time.time()-3, 'checked', 'uia:1,2', True))
        self.assertEqual(guard.snapshot.reason, 'stale-helper-response')
        self.assertIsNone(guard.permit_revision(10, 20))

    def test_generation_change_invalidates_same_runtime_id_and_same_hwnd(self):
        guard = PasswordFieldGuard(require_element_id=True, explicit_password_only=True)
        def reply(generation):
            return wire_line(10, 20, 'safe', time.time(), 'checked',
                             'uia:3,5', True) + f'|{generation}'
        guard.ingest(reply(20))
        first = guard.permit_revision(10, 20)
        guard.ingest(reply(20))
        self.assertEqual(guard.permit_revision(10, 20), first)
        # Focus A->B->A occurred between samples, with the same final identity.
        guard.ingest(reply(22))
        changed = guard.permit_revision(10, 20)
        self.assertNotEqual(changed, first)
        self.assertFalse(guard.revalidate(10, 20, first))
        self.assertEqual(guard.snapshot.element_id, 'uia:3,5')
        guard.ingest(reply(22))
        self.assertEqual(guard.permit_revision(10, 20), changed)

    def test_generation_regression_and_invalid_encoding_cannot_authorize(self):
        guard = PasswordFieldGuard(require_element_id=True)
        def reply(value):
            return wire_line(10, 20, 'safe', time.time(), 'checked',
                             'uia:3,5', True) + f'|{value}'
        guard.ingest(reply(7))
        self.assertIsNotNone(guard.permit_revision(10, 20))
        guard.ingest(reply(6))
        self.assertEqual(guard.snapshot.state, 'unknown')
        self.assertIsNone(guard.permit_revision(10, 20))
        for invalid in ('', '-1', '1.0', 'x', '18446744073709551616'):
            with self.subTest(invalid=invalid):
                guard.ingest(reply(invalid))
                self.assertIsNone(guard.permit_revision(10, 20))

    def test_old_seven_field_worker_response_refused_without_subscription(self):
        guard = PasswordFieldGuard(require_element_id=True)
        service = Mock()
        replies = iter([wire_line(10, 20, 'safe', time.time(),
                                  'checked', 'uia:1,2', True), None])
        def response(*_args, **_kwargs):
            value = next(replies)
            if value is None:
                guard.stopped.set()
            return value
        service.request.side_effect = response
        with patch('uia_native_worker.get_uia_service', return_value=service), \
                patch.object(guard.stopped, 'wait', return_value=False):
            guard._watch_once()
        self.assertIsNone(guard.permit_revision(10, 20))
        self.assertEqual(guard.snapshot.reason, 'helper-unavailable')

    def test_missing_event_subscription_fails_closed_before_any_safe_reply(self):
        guard = PasswordFieldGuard(require_element_id=True)
        service = Mock()
        def response(*_args, **_kwargs):
            guard.stopped.set()
            return None  # native worker could not register COM events
        service.request.side_effect = response
        with patch('uia_native_worker.get_uia_service', return_value=service):
            guard._watch_once()
        self.assertEqual(guard.snapshot.state, 'unknown')
        self.assertIsNone(guard.permit_revision(10, 20))
        guard.invalidate()
        guard.ingest(wire_line(10, 20, 'safe', time.time()-2, 'checked', 'uia:1,2', True))
        self.assertIsNone(guard.permit_revision(10, 20))

    def test_native_password_supervisor_reuses_shared_service_and_focus_invalidates(self):
        guard = PasswordFieldGuard(require_element_id=True, explicit_password_only=True)
        service = Mock()
        replies = iter([('safe', 'uia:1,2'), ('password', 'uia:1,3'), None])
        def response(*_args, **_kwargs):
            try:
                item = next(replies)
            except StopIteration:
                guard.stopped.set()
                return None
            return (wire_line(10, 20, item[0], time.time() + .01,
                              'password' if item[0] == 'password' else 'checked',
                              item[1], item[0] == 'safe') + '|3' if item else None)
        service.request.side_effect = response
        with patch('uia_native_worker.get_uia_service', return_value=service), \
                patch.object(guard.stopped, 'wait', return_value=False):
            guard._watch_once()
        self.assertEqual(service.request.call_count, 4)
        self.assertEqual(guard.snapshot.state, 'password')
        self.assertIsNone(guard.permit_revision(10, 20))
        self.assertEqual(guard.snapshot.element_id, 'uia:1,3')

    def test_service_unavailable_denies_strict_policy(self):
        guard = PasswordFieldGuard(require_element_id=True)
        guard.ingest(wire_line(10, 20, 'safe', time.time(), 'checked', 'uia:1,2', True))
        service = Mock()
        def missing(*_args, **_kwargs):
            guard.stopped.set()
            return None
        service.request.side_effect = missing
        with patch('uia_native_worker.get_uia_service', return_value=service):
            guard._watch_once()
        self.assertEqual(guard.snapshot.state, 'unknown')
        self.assertIsNone(guard.permit_revision(10, 20))

    def test_crashed_event_worker_must_revoke_and_reenter_with_new_revision(self):
        """COM event-subscription crashes must never preserve a text lease."""
        guard = PasswordFieldGuard(require_element_id=True, explicit_password_only=True)
        def wire(generation):
            return (wire_line(10, 20, 'safe', time.time(), 'checked',
                              'uia:7,8', True) + f'|{generation}')
        guard.ingest(wire(12))
        original = guard.snapshot.revision
        replies = iter([None, wire(0)])  # dead child, fresh process generation epoch
        service = Mock()
        def request(*_args, **_kwargs):
            result = next(replies)
            if result is not None:
                guard.stopped.set()
            return result
        service.request.side_effect = request
        with patch('uia_native_worker.get_uia_service', return_value=service), \
                patch.object(guard.stopped, 'wait', return_value=False):
            guard._watch_once()
        self.assertEqual(service.request.call_count, 2)
        self.assertEqual(guard.snapshot.state, 'safe')
        self.assertEqual(guard._event_generation, 0)
        self.assertNotEqual(guard.snapshot.revision, original)
        self.assertFalse(guard.revalidate(10, 20, original))

    def test_busy_shared_com_service_preserves_generation_without_extending_ttl(self):
        clock = [10.0]
        guard = PasswordFieldGuard(require_element_id=True, clock=lambda: clock[0])
        guard.ingest(wire_line(10, 20, 'safe', time.time(), 'checked',
                               'uia:7,8', True) + '|12')
        original = guard.snapshot
        service = Mock()
        calls = [0]
        def busy(*_args, **kwargs):
            self.assertEqual(kwargs['on_busy'], 'metadata-service-busy')
            calls[0] += 1
            clock[0] += .3
            if calls[0] == 2:
                guard.stopped.set()
            return kwargs['on_busy']
        service.request.side_effect = busy
        with patch('uia_native_worker.get_uia_service', return_value=service), \
                patch.object(guard.stopped, 'wait', return_value=False):
            guard._watch_once()
        self.assertEqual(service.request.call_count, 2)
        self.assertEqual(guard.snapshot, original)
        self.assertEqual(guard._event_generation, 12)
        # Contention did not extend the 500 ms lease: fail closed after TTL.
        self.assertIsNone(guard.permit_revision(10, 20))

    def test_busy_then_recovered_same_generation_does_not_revise_identity(self):
        guard = PasswordFieldGuard(require_element_id=True, explicit_password_only=True)
        packet = wire_line(10, 20, 'safe', time.time(), 'checked', 'uia:7,8', True) + '|12'
        guard.ingest(packet)
        revision = guard.snapshot.revision
        replies = iter(['metadata-service-busy', packet])
        service = Mock()
        def sample(*_args, **_kwargs):
            reply = next(replies)
            if reply == packet:
                guard.stopped.set()
            return reply
        service.request.side_effect = sample
        with patch('uia_native_worker.get_uia_service', return_value=service), \
                patch.object(guard.stopped, 'wait', return_value=False):
            guard._watch_once()
        self.assertEqual(guard.snapshot.state, 'safe')
        self.assertEqual(guard.snapshot.revision, revision)
        self.assertEqual(guard._event_generation, 12)

    def test_browser_context_during_busy_retains_only_original_fresh_lease(self):
        from test_field_states_20261106 import FieldStateMatrixTests
        hook, guard = FieldStateMatrixTests().fixture(
            False, process='chrome.exe', cls='Chrome_WidgetWin_1')
        clock = [100.0]
        guard.clock = lambda: clock[0]
        guard.ingest(wire_line(10, 20, 'safe', time.time(), 'checked',
                               'uia:7,8', True) + '|4')
        service = Mock()
        def busy(*_args, **kwargs):
            self.assertEqual(kwargs['on_busy'], 'metadata-service-busy')
            guard.stopped.set()
            return kwargs['on_busy']
        service.request.side_effect = busy
        with patch('uia_native_worker.get_uia_service', return_value=service):
            guard._watch_once()
        self.assertIsNotNone(hook.context())
        clock[0] += .501
        self.assertIsNone(hook.context())
        self.assertEqual(guard._event_generation, 4)


if __name__ == '__main__':
    unittest.main()
