"""Isolated Chrome simple-mode Pause safety regression; simulated document only."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_browser_selected_pause_20261107 as chrome_fixture
from test_app_controls import key
from typebridge_app import VK_PAUSE
from uia_native_worker import read_native_caret
from word_cycle import remember_word


class BrowserSimplePauseRegression(unittest.TestCase):
    def collapsed_fixture(self, *, suffix='', browser='chrome.exe'):
        text = 'PREFIX ch,' + suffix + ' SUFFIX'
        hook, context, doc, mapper, _selected = chrome_fixture.ChromeSelectedPauseTests.fixture(self, text=text)
        context = replace(context, process_name=browser)
        hook.context = lambda: context
        hook.simple_mode = True
        start = text.index('ch,')
        doc.selection(start + len('ch,') + len(suffix),
                      start + len('ch,') + len(suffix))
        hook.last_word = remember_word(mapper.keys_for('ch,', 'en'), 'ch,', 'en',
                                       suffix, hook._word_scope(context), whole=True)
        return hook, context, doc, mapper

    @staticmethod
    def modeled_read(doc, *args, **kwargs):
        kwargs.pop('simple_mode', None)
        return doc.caret_word(*args, **kwargs)

    def test_selected_then_collapsed_second_and_third_pause_cycle_same_word(self):
        """One document, real controller path, three layouts; no re-selection.

        The first Pause replaces a validated selection, so there is no saved
        word afterwards. Subsequent independent Pause events must recover the
        current word at the collapsed caret through read-only UIA preflights.
        """
        hook, source, doc, mapper, selected = (
            chrome_fixture.ChromeSelectedPauseTests.fixture(
                self, text='PREFIX ch, SUFFIX'))
        hook.simple_mode = True
        hook.password_guard = None  # Production SIMPLE_SWITCHER_MODE never starts the guard.
        selected = replace(selected, simple_mode=True)
        keys = mapper.keys_for('ch,', 'en')

        def current():
            hkl = int(hook.user32.GetKeyboardLayout.return_value)
            return replace(source, hkl=hkl,
                           language=hook._language_from_id(hkl & 0xFFFF))

        hook.context = current
        first_context = current()
        self.assertEqual((first_context.language, doc.anchor != doc.caret),
                         ('en', True))

        def selected_read(*_args, **_kwargs):
            return selected if doc.anchor != doc.caret else None

        with patch('typebridge_app.read_selected_word', side_effect=selected_read), \
                patch('typebridge_app.verify_selected_word', return_value=True), \
                patch('typebridge_app.read_caret_word',
                      side_effect=lambda *a, **kw: self.modeled_read(doc, *a, **kw)):
            for target in ('he', 'ru', 'en'):
                # Use the actual physical-hotkey handler, including its
                # missing-KEYUP Pause semantics (modeled Win32 only).
                self.assertEqual(key(hook, VK_PAUSE), 1)
                self.assertEqual(current().language, target)
                self.assertEqual(doc.text, 'PREFIX ' + mapper.word(keys, target) + ' SUFFIX')
                self.assertEqual(doc.caret, doc.anchor)
                self.assertEqual(doc.caret, len('PREFIX ') + len(keys))
                self.assertTrue(doc.text.startswith('PREFIX '))
                self.assertTrue(doc.text.endswith(' SUFFIX'))

        self.assertEqual(len(doc.attempts), 3)
        self.assertEqual(doc.attempts[0], 6)  # One Unicode-only selected replacement.
        self.assertEqual(doc.text, 'PREFIX ch, SUFFIX')
        self.assertFalse(getattr(hook, '_selection_cycle', None))

    def test_selected_replacement_not_applied_cannot_authorize_followup_cycle(self):
        hook, source, doc, mapper, selected = (
            chrome_fixture.ChromeSelectedPauseTests.fixture(
                self, text='PREFIX ch, SUFFIX'))
        hook.simple_mode = True
        hook.password_guard = None
        selected = replace(selected, simple_mode=True)
        def current():
            hkl = int(hook.user32.GetKeyboardLayout.return_value)
            return replace(source, hkl=hkl,
                           language=hook._language_from_id(hkl & 0xFFFF))
        hook.context = current
        original = doc.text
        start = doc.text.index('ch,')
        end = start + len('ch,')
        self.assertEqual((doc.anchor, doc.caret), (start, end))
        packets = []
        def accepted_without_edit(count, events, _size):
            # Browser reports acceptance of all Unicode events, yet doesn't
            # apply their text. Model its selection collapsing after the send;
            # the old EN letters remain beside the caret under the new HE HKL.
            packets.append(tuple((event.u.ki.wVk, event.u.ki.dwFlags)
                                 for event in list(events)[:count]))
            doc.selection(end, end)
            return count
        hook.user32.SendInput.side_effect = accepted_without_edit
        reads = []
        def selected_read(*_args, **_kwargs):
            reads.append((doc.anchor, doc.caret))
            return selected if doc.anchor != doc.caret else None
        caret_reads = []
        def caret_read(*args, **kwargs):
            value = self.modeled_read(doc, *args, **kwargs)
            caret_reads.append(value)
            return value
        with patch('typebridge_app.read_selected_word', side_effect=selected_read), \
                patch('typebridge_app.verify_selected_word', return_value=True), \
                patch('typebridge_app.read_caret_word',
                      side_effect=caret_read):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.assertEqual(doc.text, original)
            self.assertEqual((doc.anchor, doc.caret), (end, end))
            self.assertEqual(current().language, 'he')
            self.assertEqual(len(packets), 1)
            # The key is physically pressed again WITHOUT KEYUP or reselection.
            self.assertEqual(key(hook, VK_PAUSE), 1)
        # The previous selection is no longer present; the second call must
        # independently prove an HE word and must not delete old EN text.
        self.assertEqual(reads[0], (start, end))
        self.assertEqual(reads[1], (end, end))
        self.assertTrue(caret_reads)  # Second press attempted safe UIA recovery.
        self.assertIsNone(caret_reads[-1])
        self.assertEqual(len(packets), 1)  # No blind/repeated SendInput.
        self.assertTrue(all(vk == 0 for vk, _flags in packets[0]))  # Unicode only.
        self.assertEqual(doc.text, original)  # All neighboring text survives.
        self.assertEqual((doc.anchor, doc.caret), (end, end))
        self.assertEqual(doc.attempts, [])
        self.assertTrue(hook.token_blocked)
        self.assertIsNone(hook.last_word)
        self.assertEqual(current().language, 'he')

    def test_unapplied_selection_collapsing_to_start_must_not_replace_neighbor(self):
        """The previous HE word must never impersonate the selected EN word.

        If Chrome reports accepted Unicode keys but does not apply them, it
        may collapse the selection at its START. A fresh UIA caret read then
        sees a real HE word immediately *before* the cursor. It must not
        convert that unrelated word on the next physical Pause.
        """
        hook, source, doc, mapper, selected = (
            chrome_fixture.ChromeSelectedPauseTests.fixture(
                self, text='בית ch, SUFFIX'))
        hook.simple_mode = True
        hook.password_guard = None
        selected = replace(selected, simple_mode=True)
        start = doc.text.index('ch,')
        original = doc.text
        self.assertEqual(doc.anchor, start)
        def current():
            hkl = int(hook.user32.GetKeyboardLayout.return_value)
            return replace(source, hkl=hkl,
                           language=hook._language_from_id(hkl & 0xFFFF))
        hook.context = current
        packets = []
        original_send = doc.send
        def first_accepted_but_unapplied(count, events, size):
            packets.append(count)
            if len(packets) == 1:
                doc.selection(start, start)  # Browser loses selection at its start.
                return count
            return original_send(count, events, size)  # Any second packet truly edits.
        hook.user32.SendInput.side_effect = first_accepted_but_unapplied
        with patch('typebridge_app.read_selected_word',
                   side_effect=lambda *_a, **_kw: selected if doc.anchor != doc.caret else None), \
                patch('typebridge_app.verify_selected_word', return_value=True), \
                patch('typebridge_app.read_caret_word',
                      side_effect=lambda *a, **kw: self.modeled_read(doc, *a, **kw)):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.assertEqual(doc.text, original)
            self.assertEqual((doc.anchor, doc.caret), (start, start))
            self.assertEqual(current().language, 'he')
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.assertEqual(key(hook, VK_PAUSE), 1)  # Refusal remains sticky.
        self.assertEqual(len(packets), 1, 'Second Pause must not send a neighbor edit')
        self.assertEqual(doc.text, original)
        self.assertEqual((doc.anchor, doc.caret), (start, start))

    def test_unapplied_generic_pause_followup_cannot_replace_neighbor(self):
        """Even a previously caret-verified packet may be ignored by Chrome."""
        hook, source, doc, mapper, _selected = (
            chrome_fixture.ChromeSelectedPauseTests.fixture(
                self, text='בית ch, SUFFIX'))
        hook.simple_mode = True
        hook.password_guard = None
        start = doc.text.index('ch,')
        doc.selection(start + 3, start + 3)
        original = doc.text
        def current():
            hkl = int(hook.user32.GetKeyboardLayout.return_value)
            return replace(source, hkl=hkl,
                           language=hook._language_from_id(hkl & 0xFFFF))
        hook.context = current
        packets = []
        original_send = doc.send
        def simulated_send(count, events, size):
            packets.append(count)
            if len(packets) == 1:
                doc.selection(start, start)  # No edit, now caret at prior target start.
                return count
            return original_send(count, events, size)
        hook.user32.SendInput.side_effect = simulated_send
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word',
                      side_effect=lambda *a, **kw: self.modeled_read(doc, *a, **kw)):
            for _ in range(3):
                self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(current().language, 'he')
        self.assertEqual(len(packets), 1)
        self.assertEqual(doc.text, original)
        self.assertEqual((doc.anchor, doc.caret), (start, start))

    def test_unapplied_selected_packet_retains_selection_does_not_retry_blindly(self):
        hook, source, doc, mapper, selected = (
            chrome_fixture.ChromeSelectedPauseTests.fixture(
                self, text='PREFIX ch, SUFFIX'))
        hook.simple_mode = True
        hook.password_guard = None
        selected = replace(selected, simple_mode=True)
        original, original_range = doc.text, (doc.anchor, doc.caret)
        def current():
            hkl = int(hook.user32.GetKeyboardLayout.return_value)
            return replace(source, hkl=hkl,
                           language=hook._language_from_id(hkl & 0xFFFF))
        hook.context = current
        packets = []
        def accepted_without_edit(count, _events, _size):
            packets.append(count)
            return count  # Chrome keeps the original text AND selection.
        hook.user32.SendInput.side_effect = accepted_without_edit
        with patch('typebridge_app.read_selected_word', return_value=selected), \
                patch('typebridge_app.verify_selected_word', return_value=True):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.assertEqual(current().language, 'he')
            self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(len(packets), 1)
        self.assertEqual(doc.text, original)
        self.assertEqual((doc.anchor, doc.caret), original_range)

    def test_verified_after_spaces_can_cycle_three_times_with_followup_guard(self):
        hook, source, doc, mapper, _selected = (
            chrome_fixture.ChromeSelectedPauseTests.fixture(
                self, text='PREFIX ch,  SUFFIX'))
        hook.simple_mode = True
        hook.password_guard = None
        keys = mapper.keys_for('ch,', 'en')
        caret = doc.text.index('ch,') + len('ch,  ')
        doc.selection(caret, caret)
        def current():
            hkl = int(hook.user32.GetKeyboardLayout.return_value)
            return replace(source, hkl=hkl,
                           language=hook._language_from_id(hkl & 0xFFFF))
        hook.context = current
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word',
                      side_effect=lambda *a, **kw: self.modeled_read(doc, *a, **kw)):
            for target in ('he', 'ru', 'en'):
                self.assertEqual(key(hook, VK_PAUSE), 1)
                self.assertEqual(doc.text,
                                 'PREFIX ' + mapper.word(keys, target) + '  SUFFIX')
                self.assertEqual(current().language, target)
                self.assertEqual((doc.anchor, doc.caret), (caret, caret))
        self.assertEqual(len(doc.attempts), 3)

    def test_unapplied_generic_after_space_must_not_convert_identical_neighbor(self):
        """Same spelling and suffix alone cannot prove the caret's position."""
        hook, source, doc, mapper, _selected = (
            chrome_fixture.ChromeSelectedPauseTests.fixture(
                self, text='בית ch, SUFFIX'))
        hook.simple_mode = True
        hook.password_guard = None
        start = doc.text.index('ch,')
        doc.selection(start + len('ch, '), start + len('ch, '))
        original = doc.text
        def current():
            hkl = int(hook.user32.GetKeyboardLayout.return_value)
            return replace(source, hkl=hkl,
                           language=hook._language_from_id(hkl & 0xFFFF))
        hook.context = current
        packets = []
        real_send = doc.send
        def send(count, events, size):
            packets.append(count)
            if len(packets) == 1:
                doc.selection(start, start)  # Accepted, not applied; wrong endpoint.
                return count
            return real_send(count, events, size)
        hook.user32.SendInput.side_effect = send
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word',
                      side_effect=lambda *a, **kw: self.modeled_read(doc, *a, **kw)):
            for _ in range(3):
                self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(current().language, 'he')
        self.assertEqual(len(packets), 1)
        self.assertEqual(doc.text, original)
        self.assertEqual((doc.anchor, doc.caret), (start, start))

    def test_changed_text_after_caret_rejects_unverified_followup(self):
        hook, source, doc, mapper, _selected = (
            chrome_fixture.ChromeSelectedPauseTests.fixture(
                self, text='PREFIX ch,  SUFFIX'))
        hook.simple_mode = True
        hook.password_guard = None
        caret = doc.text.index('ch,') + len('ch,  ')
        doc.selection(caret, caret)
        def current():
            hkl = int(hook.user32.GetKeyboardLayout.return_value)
            return replace(source, hkl=hkl,
                           language=hook._language_from_id(hkl & 0xFFFF))
        hook.context = current
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word',
                      side_effect=lambda *a, **kw: self.modeled_read(doc, *a, **kw)):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.assertEqual(current().language, 'he')
            doc.text = doc.text.replace('SUFFIX', 'CHANGED')  # Programmatic DOM change.
            changed = doc.text
            self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertEqual(doc.text, changed)
        self.assertEqual(len(doc.attempts), 1)
        self.assertEqual((doc.anchor, doc.caret), (caret, caret))

    def test_interior_caret_three_pause_cycles_keep_relative_position(self):
        """Only context after WHOLE word is stable across interior edits."""
        hook, source, doc, mapper, _selected = (
            chrome_fixture.ChromeSelectedPauseTests.fixture(
                self, text='PREFIX ch, SUFFIX'))
        hook.simple_mode = True
        hook.password_guard = None
        keys = mapper.keys_for('ch,', 'en')
        caret = doc.text.index('ch,') + 1  # c|h, within the word.
        doc.selection(caret, caret)
        def current():
            hkl = int(hook.user32.GetKeyboardLayout.return_value)
            return replace(source, hkl=hkl,
                           language=hook._language_from_id(hkl & 0xFFFF))
        hook.context = current
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word',
                      side_effect=lambda *a, **kw: self.modeled_read(doc, *a, **kw)):
            for target in ('he', 'ru', 'en'):
                self.assertEqual(key(hook, VK_PAUSE), 1)
                self.assertEqual(current().language, target)
                self.assertEqual(doc.text, 'PREFIX ' + mapper.word(keys, target) + ' SUFFIX')
                self.assertEqual((doc.anchor, doc.caret), (caret, caret))
        self.assertEqual(len(doc.attempts), 3)

    def exercise_refused_selection(self, *, browser='chrome.exe', text='PREFIX ch, SUFFIX', suffix=''):
        hook, context, doc, mapper, _selected = chrome_fixture.ChromeSelectedPauseTests.fixture(self, text=text)
        context = replace(context, process_name=browser)
        hook.context = lambda: context
        hook.simple_mode = True
        source = 'ch,'
        keys = mapper.keys_for(source, 'en')
        hook.last_word = remember_word(keys, source, 'en', suffix,
                                       hook._word_scope(context), whole=True)
        original = doc.text
        # A provider may refuse a nonempty selection. Fail closed before
        # sending any Backspace packet; this must hold even in simple mode.
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word', return_value=None):
            hook._cycle_word(context)
        self.assertEqual(doc.text, original)
        self.assertEqual(doc.attempts, [])


    def test_reader_refusal_does_not_delete_existing_selection_in_simple_mode(self):
        self.exercise_refused_selection()

    def test_firefox_selected_word_refusal_preserves_preceding_text(self):
        self.exercise_refused_selection(browser='firefox.exe')

    def test_browser_after_space_refusal_preserves_selected_word_and_suffix(self):
        self.exercise_refused_selection(text='PREFIX ch,  SUFFIX', suffix='  ')

    def test_collapsed_caret_two_spaces_can_cycle_once(self):
        hook, context, doc, mapper = self.collapsed_fixture(suffix='  ')
        reads = []
        def read(*args, **kwargs):
            sample = self.modeled_read(doc, *args, **kwargs)
            reads.append(sample)
            return sample
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word', side_effect=read):
            hook._cycle_word(context)
        self.assertGreaterEqual(len(reads), 2)
        self.assertEqual(doc.text, 'PREFIX ' + mapper.word(mapper.keys_for('ch,', 'en'), 'he') + '   SUFFIX')
        self.assertEqual(len(doc.attempts), 1)

    def test_collapsed_caret_changed_runtime_id_refuses_before_send(self):
        hook, context, doc, _ = self.collapsed_fixture()
        original = doc.text
        reads = [0]
        def read(*args, **kwargs):
            from dataclasses import replace as change
            reads[0] += 1
            sample = self.modeled_read(doc, *args, **kwargs)
            return change(sample, element_id='uia:OTHER') if reads[0] == 2 and sample else sample
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word', side_effect=read):
            hook._cycle_word(context)
        self.assertGreaterEqual(reads[0], 2)
        self.assertEqual(doc.text, original)
        self.assertEqual(doc.attempts, [])

    def test_caret_becomes_selection_between_two_reads_refuses(self):
        hook, context, doc, _ = self.collapsed_fixture()
        original = doc.text
        reads = [0]
        def read(*args, **kwargs):
            sample = self.modeled_read(doc, *args, **kwargs)
            reads[0] += 1
            if reads[0] == 1:
                start = doc.text.index('ch,')
                doc.selection(start, start + 3)
            return sample
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word', side_effect=read):
            hook._cycle_word(context)
        self.assertGreaterEqual(reads[0], 2)
        self.assertEqual(doc.text, original)
        self.assertEqual(doc.attempts, [])

    def test_focus_a_b_a_revision_change_refuses_even_when_hwnd_is_same(self):
        hook, context, doc, _ = self.collapsed_fixture()
        original = doc.text
        def read(*args, **kwargs):
            sample = self.modeled_read(doc, *args, **kwargs)
            # Same native HWND eventually becomes current again, but an
            # intermediate browser-field change invalidates the old lease.
            hook._browser_field_revision = getattr(hook, '_browser_field_revision', 0) + 2
            return sample
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word', side_effect=read):
            hook._cycle_word(context)
        self.assertEqual(doc.text, original)
        self.assertEqual(doc.attempts, [])

    def test_unavailable_caret_provider_refuses_without_fallback_packet(self):
        hook, context, doc, _ = self.collapsed_fixture()
        original = doc.text
        # Public read_caret_word returns None on provider timeout/failure.
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word', return_value=None):
            hook._cycle_word(context)
        self.assertEqual(doc.text, original)
        self.assertEqual(doc.attempts, [])

class NativeSimpleCaretReadOnlyContracts(unittest.TestCase):
    """All UIA and foreground APIs modeled; no real COM/Win32 calls."""

    def fixture(self):
        uia = SimpleNamespace(
            UIA_IsPasswordPropertyId=101,
            UIA_NativeWindowHandlePropertyId=102,
            UIA_TextPatternId=103,
            IUIAutomationTextPattern=object(),
            TextPatternRangeEndpoint_Start=0,
            TextPatternRangeEndpoint_End=1,
            TextUnit_Character=2)
        automation = Mock()
        leaf = automation.GetFocusedElement.return_value
        ancestor = Mock()
        leaf.GetRuntimeId.return_value = [7, 12]
        automation.CompareElements.return_value = True
        automation.RawViewWalker.GetParentElement.side_effect = lambda current: (
            ancestor if current is leaf else None)

        def property_value(handle):
            def read(prop):
                if prop == uia.UIA_IsPasswordPropertyId:
                    raise AssertionError('IsPassword must not be called')
                if prop == uia.UIA_NativeWindowHandlePropertyId:
                    return handle
                raise AssertionError('Unexpected UIA property')
            return read

        leaf.GetCurrentPropertyValue.side_effect = property_value(56)
        ancestor.GetCurrentPropertyValue.side_effect = property_value(55)
        pattern = leaf.GetCurrentPattern.return_value.QueryInterface.return_value
        selections = pattern.GetSelection.return_value
        selections.Length = 1
        caret = selections.GetElement.return_value.Clone.return_value
        caret.CompareEndpoints.return_value = 0  # Collapsed, not a selection.
        before, after = caret.Clone.side_effect = [Mock(), Mock()]
        before.GetText.return_value = 'ch,'
        after.GetText.return_value = '  SUFFIX'
        before.CompareEndpoints.return_value = 0
        after.CompareEndpoints.return_value = 0
        selections.GetElement.return_value.CompareEndpoints.return_value = 0
        request = {'top': 55, 'focus': 56, 'element_id': '', 'simple_mode': True}
        return uia, automation, leaf, ancestor, caret, before, after, request

    def test_simple_caret_binds_focus_runtime_id_collapsed_without_password_properties(self):
        uia, automation, leaf, ancestor, caret, before, after, request = self.fixture()
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            sample = read_native_caret(automation, request, uia)
        self.assertEqual(sample, {'before': 'ch,', 'after': '  SUFFIX',
                                  'at_start': True, 'at_end': True,
                                  'element_id': 'uia:7,12'})
        self.assertGreaterEqual(automation.CompareElements.call_count, 3)
        self.assertGreaterEqual(leaf.GetRuntimeId.call_count, 3)
        self.assertEqual(caret.CompareEndpoints.call_count, 1)
        self.assertTrue(before.MoveEndpointByUnit.called)
        self.assertTrue(after.MoveEndpointByUnit.called)
        self.assertTrue(all(call.args[0] == uia.UIA_NativeWindowHandlePropertyId
                            for element in (leaf, ancestor)
                            for call in element.GetCurrentPropertyValue.call_args_list))

    def test_oversized_chrome_span_retries_from_same_caret_without_trimming_text(self):
        uia, automation, leaf, ancestor, caret, first, after, request = self.fixture()
        shorter = Mock()
        first.GetText.return_value = 'x' * 100  # Beyond the 81 UTF-16-unit limit.
        shorter.GetText.return_value = 'PREFIX tot'
        shorter.CompareEndpoints.return_value = -1  # Not the document start.
        after.GetText.return_value = ''
        caret.Clone.side_effect = [first, shorter, after]
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            sample = read_native_caret(automation, request, uia)
        self.assertEqual(sample['before'], 'PREFIX tot')
        self.assertEqual(sample['after'], '')
        self.assertFalse(sample['at_start'])
        self.assertEqual(first.MoveEndpointByUnit.call_args.args[-1], -81)
        self.assertEqual(shorter.MoveEndpointByUnit.call_args.args[-1], -64)
        self.assertEqual(after.MoveEndpointByUnit.call_args.args[-1], 81)
        self.assertEqual(first.GetText.call_count, 1)
        self.assertEqual(shorter.GetText.call_count, 2)  # Revalidated after GetSelection.

    def test_oversized_after_span_retries_independently(self):
        uia, automation, leaf, ancestor, caret, before, after, request = self.fixture()
        smaller_after = Mock()
        before.GetText.return_value = 'tot'
        after.GetText.return_value = 'z' * 100
        smaller_after.GetText.return_value = ' WORD'
        smaller_after.CompareEndpoints.return_value = -1
        caret.Clone.side_effect = [before, after, smaller_after]
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            sample = read_native_caret(automation, request, uia)
        self.assertEqual((sample['before'], sample['after']), ('tot', ' WORD'))
        self.assertEqual(smaller_after.MoveEndpointByUnit.call_args.args[-1], 64)
        self.assertFalse(sample['at_end'])

    def test_oversized_caret_ranges_fail_closed_when_all_bounded_reads_exceed_limit(self):
        uia, automation, leaf, ancestor, caret, before, after, request = self.fixture()
        spans = []
        def span():
            current = Mock()
            current.GetText.return_value = 'z' * 100
            spans.append(current)
            return current
        caret.Clone.side_effect = span
        reasons = []
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            self.assertIsNone(read_native_caret(automation, request, uia,
                                               reason_out=reasons))
        self.assertEqual(reasons, ['range-before-content-out-of-bounds'])
        self.assertEqual([s.MoveEndpointByUnit.call_args.args[-1] for s in spans],
                         [-81, -64, -48])
        self.assertEqual(sum(s.GetText.call_count for s in spans), 3)

    def test_nontext_provider_result_has_distinct_safe_refusal_not_a_retry(self):
        uia, automation, leaf, ancestor, caret, before, after, request = self.fixture()
        before.GetText.return_value = None
        reasons = []
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            self.assertIsNone(read_native_caret(automation, request, uia,
                                               reason_out=reasons))
        self.assertEqual(reasons, ['range-before-invalid-text'])
        self.assertEqual(before.GetText.call_count, 1)
        after.GetText.assert_not_called()

    def test_caret_selection_moving_during_oversize_retry_still_refuses(self):
        uia, automation, leaf, ancestor, caret, first, after, request = self.fixture()
        shorter = Mock()
        first.GetText.return_value = 'x' * 100
        shorter.GetText.return_value = 'tot'
        after.GetText.return_value = ''
        caret.Clone.side_effect = [first, shorter, after]
        # The final readback now has different endpoints than the original
        # collapsed range. An oversized first span must not bypass this proof.
        pattern = leaf.GetCurrentPattern.return_value.QueryInterface.return_value
        pattern.GetSelection.return_value.GetElement.return_value.CompareEndpoints.side_effect = [1]
        reasons = []
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            self.assertIsNone(read_native_caret(automation, request, uia,
                                               reason_out=reasons))
        self.assertEqual(reasons, ['range-changed-or-target-unverified'])
        self.assertEqual(shorter.GetText.call_count, 1)  # No stale readback accepted.

    def test_simple_caret_rejects_other_focus_or_runtime_id_and_selection(self):
        for failure in ('focus', 'runtime-id', 'selection'):
            with self.subTest(failure=failure):
                uia, automation, leaf, ancestor, caret, before, after, request = self.fixture()
                if failure == 'runtime-id':
                    request['element_id'] = 'uia:7,99'
                if failure == 'selection':
                    caret.CompareEndpoints.return_value = -1
                scope = (55, 99) if failure == 'focus' else (55, 56)
                with patch('uia_native_worker._scope', return_value=scope):
                    self.assertIsNone(read_native_caret(automation, request, uia))
                before.GetText.assert_not_called()
                after.GetText.assert_not_called()
                self.assertTrue(all(call.args[0] == uia.UIA_NativeWindowHandlePropertyId
                                    for element in (leaf, ancestor)
                                    for call in element.GetCurrentPropertyValue.call_args_list))

    def test_caret_refusal_reports_only_bounded_stage_without_text(self):
        for failure, expected in (('focus', 'target-unverified'),
                                  ('selection', 'noncollapsed-selection'),
                                  ('pattern', 'text-pattern-unsupported')):
            with self.subTest(failure=failure):
                uia, automation, leaf, ancestor, caret, before, after, request = self.fixture()
                if failure == 'selection':
                    caret.CompareEndpoints.return_value = -1
                elif failure == 'pattern':
                    leaf.GetCurrentPattern.return_value = None
                scope = (55, 99) if failure == 'focus' else (55, 56)
                reasons = []
                with patch('uia_native_worker._scope', return_value=scope):
                    self.assertIsNone(read_native_caret(automation, request, uia,
                                                       reason_out=reasons))
                self.assertEqual(reasons, [expected])
                self.assertNotIn('ch,', str(reasons))

    def test_strict_mode_rejects_when_password_property_unsupported(self):
        uia, automation, leaf, ancestor, caret, before, after, request = self.fixture()
        request.pop('simple_mode')
        request['element_id'] = 'uia:7,12'
        with patch('uia_native_worker._scope', return_value=(55, 56)):
            self.assertIsNone(read_native_caret(automation, request, uia))
        self.assertEqual(leaf.GetCurrentPropertyValue.call_args_list[0].args,
                         (uia.UIA_IsPasswordPropertyId,))
        ancestor.GetCurrentPropertyValue.assert_not_called()
        before.GetText.assert_not_called()
        after.GetText.assert_not_called()


if __name__ == '__main__':
    unittest.main()
