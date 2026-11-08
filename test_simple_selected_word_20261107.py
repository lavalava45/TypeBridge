"""Mock-only tests for simple selection leases; no GUI, input, or live text."""
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from caret_word_recovery import read_selected_word, verify_selected_word
from uia_native_worker import _safe_target
from uia_selection_native import SelectedWordLeases, read_native_selected_word


class SimpleSelectedWordTests(unittest.TestCase):
    def setUp(self):
        self.automation = Mock()
        self.leaf = self.automation.GetFocusedElement.return_value
        self.parent = Mock()
        self.leaf.GetRuntimeId.return_value = [7, 12]
        self.automation.CompareElements.return_value = True
        self.automation.RawViewWalker.GetParentElement.side_effect = lambda element: (
            self.parent if element is self.leaf else None)
        self.uia = SimpleNamespace(UIA_IsPasswordPropertyId=101,
                                   UIA_NativeWindowHandlePropertyId=102,
                                   UIA_TextPatternId=103,
                                   IUIAutomationTextPattern=object(),
                                   TextPatternRangeEndpoint_Start=0,
                                   TextPatternRangeEndpoint_End=1)
        self.leaf.GetCurrentPropertyValue.side_effect = lambda prop: (
            0 if prop == 102 else (_ for _ in ()).throw(AssertionError('password read')))
        self.parent.GetCurrentPropertyValue.side_effect = lambda prop: (
            55 if prop == 102 else (_ for _ in ()).throw(AssertionError('password read')))
        self.pattern = self.leaf.GetCurrentPattern.return_value.QueryInterface.return_value
        self.ranges = self.pattern.GetSelection.return_value
        self.ranges.Length = 1
        self.selected = self.ranges.GetElement.return_value.Clone.return_value
        self.selected.CompareEndpoints.return_value = -1
        self.selected.GetText.return_value = 'ghbdtn'
        self.ranges.GetElement.return_value.CompareEndpoints.return_value = 0
        self.selected.Clone.return_value.CompareEndpoints.return_value = 0
        self.leases = SelectedWordLeases()
        self.base = {'top': 55, 'focus': 56, 'element_id': '', 'simple_mode': True}

    def test_simple_capture_omits_all_password_properties(self):
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            self.assertTrue(_safe_target(self.automation, self.leaf,
                {**self.base, 'element_id':'uia:7,12'}, self.uia))
        self.leaf.GetCurrentPropertyValue.assert_called()
        self.assertTrue(all(c.args[0] == 102 for c in
                            self.leaf.GetCurrentPropertyValue.call_args_list))
        self.assertTrue(all(c.args[0] == 102 for c in
                            self.parent.GetCurrentPropertyValue.call_args_list))

    def test_simple_read_captures_runtime_id_and_verify_uses_original_range(self):
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            read = read_native_selected_word(self.automation,
                {**self.base, 'mode':'read'}, self.uia,
                generation=9, leases=self.leases)
            self.assertEqual(read['state'], 'ok')
            self.assertEqual(read['element_id'], 'uia:7,12')
            verify = {**self.base, 'mode':'verify', 'element_id':read['element_id'],
                      'generation':9, 'digest':read['digest'], 'lease':read['lease']}
            self.assertEqual(read_native_selected_word(self.automation, verify,
                self.uia, generation=9, leases=self.leases), {'state':'matched'})
            self.assertEqual(read_native_selected_word(self.automation, verify,
                self.uia, generation=9, leases=self.leases), {'state':'refused'})

    def test_different_identical_word_occurrence_is_refused(self):
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            read = read_native_selected_word(self.automation,
                {**self.base, 'mode':'read'}, self.uia,
                generation=9, leases=self.leases)
            self.selected.Clone.return_value.CompareEndpoints.side_effect = [1, 0]
            verify = {**self.base, 'mode':'verify', 'element_id':read['element_id'],
                      'generation':9, 'digest':read['digest'], 'lease':read['lease']}
            self.assertEqual(read_native_selected_word(self.automation, verify,
                self.uia, generation=9, leases=self.leases), {'state':'refused'})

    def test_wrong_focus_fails_before_selected_text_read(self):
        with patch('uia_native_worker._scope', return_value=(55, 99)):
            result = read_native_selected_word(self.automation,
                {**self.base, 'mode':'read'}, self.uia,
                generation=9, leases=self.leases)
        self.assertEqual(result, {'state':'refused'})
        self.selected.GetText.assert_not_called()

    def test_strict_mode_empty_id_still_refused(self):
        result = read_native_selected_word(self.automation,
            {'top':55,'focus':56,'element_id':'','mode':'read'},
            self.uia, generation=9, leases=self.leases)
        self.assertEqual(result, {'state':'refused'})

    def test_wrapper_signature_and_simple_verify_token(self):
        from engine import LayoutMapper
        text = 'ghbdtn'
        digest = hashlib.sha256(text.encode('utf-16-le')).hexdigest()
        service = Mock()
        service.request.side_effect = [
            {'state':'ok','text':text,'element_id':'uia:7,12',
             'generation':9,'digest':digest,'lease':'a'*32},
            {'state':'matched'}]
        context = SimpleNamespace(top=55,focus=56,language='en')
        with patch('uia_native_worker.get_uia_service', return_value=service):
            word = read_selected_word(context, LayoutMapper(), '', lambda: True,
                                      simple_mode=True)
            self.assertIsNotNone(word)
            self.assertTrue(word.simple_mode)
            self.assertEqual(word.element_id, 'uia:7,12')
            self.assertTrue(verify_selected_word(context, word, lambda: True))
        self.assertEqual(service.request.call_args.args[0]['simple_mode'], True)
        self.assertEqual(service.request.call_args.args[0]['element_id'], 'uia:7,12')

    def test_read_only_selection_refusal_reports_stage_without_selected_text(self):
        # A true, noncollapsed Chrome selection can still be rejected by the
        # provider reader. Diagnostics must never leak its contents or range.
        stages = []
        self.selected.GetText.return_value = 'tot '
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            result = read_native_selected_word(
                self.automation, {**self.base, 'mode': 'read'}, self.uia,
                generation=0, leases=self.leases, reason_out=stages)
        self.assertEqual(result, {'state': 'refused'})
        self.assertEqual(stages, ['selected-text-invalid-characters'])
        self.assertNotIn('tot', str(stages))
        self.assertIsNone(self.leases.entry)

    def test_selection_reader_reports_collapsed_and_valid_without_changing_contract(self):
        self.selected.CompareEndpoints.return_value = 0
        stages = []
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            self.assertEqual(read_native_selected_word(
                self.automation, {**self.base, 'mode': 'read'}, self.uia,
                generation=0, leases=self.leases, reason_out=stages),
                {'state': 'refused'})
            self.assertEqual(stages, ['selection-collapsed'])
            self.selected.CompareEndpoints.return_value = -1
            self.selected.GetText.return_value = 'tot'
            stages.clear()
            result = read_native_selected_word(
                self.automation, {**self.base, 'mode': 'read'}, self.uia,
                generation=0, leases=self.leases, reason_out=stages)
        self.assertEqual(result['state'], 'ok')
        self.assertEqual(stages, [])

    def test_wrapper_diagnostic_reasons_are_allowlisted_and_read_only(self):
        from engine import LayoutMapper
        context = SimpleNamespace(top=55, focus=56, language='en')
        service = Mock()
        stages = []
        service.request.return_value = {
            'state': 'refused', 'reason': 'selection-changed-or-target-unverified'}
        with patch('uia_native_worker.get_uia_service', return_value=service):
            self.assertIsNone(read_selected_word(
                context, LayoutMapper(), '', lambda: True, simple_mode=True,
                on_refusal=stages.append))
        self.assertEqual(stages, ['selection-changed-or-target-unverified'])
        sent = service.request.call_args.args[0]
        self.assertEqual(sent['command'], 'selected-word')
        self.assertEqual(sent['mode'], 'read')
        self.assertTrue(sent['diagnostic'])
        self.assertNotIn('text', sent)
        service.request.return_value = {
            'state': 'refused', 'reason': 'tot\nunauthorized'}
        with patch('uia_native_worker.get_uia_service', return_value=service):
            self.assertIsNone(read_selected_word(
                context, LayoutMapper(), '', lambda: True, simple_mode=True,
                on_refusal=stages.append))
        self.assertEqual(stages[-1], 'selection-service-unavailable')


if __name__ == '__main__':
    unittest.main()
