"""P0-D: no generic Backspace packet for an unobserved selection.

Model hooks/UIA/Win32; never reads real focus, installs hooks, or types text.
Every assertion checks final document contents, not only a refused call.
"""
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from caret_word_recovery import parse_caret_word
from experiments.r10_selection_reuse_audit_20261107 import FrozenMapper, KEYMAP
from test_app_controls import key
from test_hook_cycle_offhook import GenericCycleOffHookTests
from typebridge_app import (VK_BACK, VK_DELETE, VK_LEFT, VK_PAUSE,
                            KEYEVENTF_KEYUP, KEYEVENTF_UNICODE)
from word_cycle import remember_word


class Document:
    def __init__(self, text, caret, anchor=None):
        self.text = text
        self.caret = caret
        self.anchor = caret if anchor is None else anchor
        self.attempts = []

    def selection(self, a, b):
        self.anchor, self.caret = a, b

    def caret_word(self, ctx, mapper, _element_id, valid, *,
                   active_languages=None, allow_following_word=False):
        if not valid() or self.caret != self.anchor:
            return None
        sample = {'before': self.text[:self.caret],
                  'after': self.text[self.caret:],
                  'at_start': True, 'at_end': True, 'element_id': 'uia:11,22'}
        return parse_caret_word(sample, mapper, ctx.language, 'uia:11,22',
                                active_languages=active_languages,
                                allow_following_word=allow_following_word)

    def send(self, count, events, _size):
        self.attempts.append(count)
        for event in list(events)[:count]:
            ki = event.u.ki
            if ki.dwFlags & KEYEVENTF_KEYUP:
                continue
            a, b = sorted((self.anchor, self.caret))
            if ki.wVk == VK_BACK:
                if a == b:
                    a = max(0, a - 1)
                self.text = self.text[:a] + self.text[b:]
                self.anchor = self.caret = a
            elif ki.wVk == VK_DELETE:
                if a == b:
                    b = min(len(self.text), b + 1)
                self.text = self.text[:a] + self.text[b:]
                self.anchor = self.caret = a
            elif ki.wVk == VK_LEFT:
                self.anchor = self.caret = max(0, self.caret - 1)
            elif ki.dwFlags & KEYEVENTF_UNICODE:
                self.text = self.text[:a] + chr(ki.wScan) + self.text[b:]
                self.anchor = self.caret = a + 1
        return count


class GenericPauseSelectionSafetyTests(unittest.TestCase):
    def fixture(self, *, text='PREFIX ghbdtn SUFFIX', suffix='', selected=False,
                token='ghbdtn', caret_offset=None, use_keys=False):
        hook, _ = GenericCycleOffHookTests.fixture(self)
        mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
        mapper.layouts = {c: int(v, 16) if isinstance(v, str) else v
                          for c, v in mapper.layouts.items()}
        hook.engine.mapper = mapper
        hook.engine.active_languages = ('ru', 'en', 'he')
        hook.user32.GetKeyboardLayout.return_value = mapper.layouts['en']
        hook.last_language = 'en'
        keys = mapper.keys_for(token, 'en')
        self.assertEqual(mapper.word(keys, 'en'), token)
        hook.keys = list(keys) if use_keys else []
        hook.last_word = (None if use_keys else
                          remember_word(keys, token, 'en', suffix,
                                        hook._word_scope(hook.context()), whole=True,
                                        caret_offset=caret_offset))
        start = text.index(token)
        end = start + (len(token) if caret_offset is None else caret_offset)
        document = Document(text, end + len(suffix), start if selected else None)
        hook.user32.SendInput.side_effect = document.send
        return hook, mapper, document, keys

    def release(self, hook):
        GenericCycleOffHookTests.release(self, hook)
        self.assertIsNone(getattr(hook, '_keyboard_job_release', None))

    def test_original_prefix_and_suffix_survive_unobserved_selection(self):
        hook, _, doc, _ = self.fixture(selected=True)
        before = doc.text
        with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertEqual(doc.text, before)
        self.assertTrue(doc.text.startswith('PREFIX '))
        self.assertTrue(doc.text.endswith(' SUFFIX'))
        self.assertEqual(doc.attempts, [])

    def test_selection_changes_between_two_reads_before_send(self):
        hook, _, doc, _ = self.fixture()
        before = doc.text
        seen = []
        def read(*args, **kwargs):
            observed = doc.caret_word(*args, **kwargs)
            seen.append(observed)
            if len(seen) == 1:
                start = doc.text.index('ghbdtn')
                doc.selection(start, start + 6)
            return observed
        with patch('typebridge_app.read_caret_word', side_effect=read):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(doc.text, before)
        self.assertEqual(doc.attempts, [])

    def test_selection_changed_during_password_preflight_is_rechecked(self):
        """The last caret read must follow, not precede, password revalidation."""
        hook, _, doc, _ = self.fixture()
        before = doc.text
        reads = []
        def read(*args, **kwargs):
            snapshot = doc.caret_word(*args, **kwargs)
            reads.append(snapshot)
            return snapshot
        def revalidate(_top, _focus, _revision):
            start = doc.text.index('ghbdtn')
            doc.selection(start, start + 6)
            return True
        guard = SimpleNamespace(explicit_password_only=True,
                                revalidate=Mock(side_effect=revalidate))
        with patch('typebridge_app.read_caret_word', side_effect=read):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            # Install a deterministic guard AFTER the hook has captured the
            # command; this isolates the worker's admission ordering rather
            # than changing tracking during the fixture's keyboard callback.
            hook.password_guard = guard
            self.release(hook)
        self.assertEqual(guard.revalidate.call_count, 1)
        self.assertEqual(len(reads), 2)
        self.assertIsNotNone(reads[0])
        self.assertIsNone(reads[1])
        self.assertEqual(doc.text, before)
        self.assertEqual(doc.attempts, [])
        self.assertFalse(hook.native_queue.busy)
        self.assertIsNone(getattr(hook, 'native_pending_recovery', None))

    def test_normal_no_selection_preserves_prefix_suffix_and_cycles(self):
        hook, mapper, doc, keys = self.fixture()
        with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
            self.assertEqual(doc.text, 'PREFIX ' + mapper.word(keys, 'he') + ' SUFFIX')
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
            self.assertEqual(doc.text, 'PREFIX ' + mapper.word(keys, 'ru') + ' SUFFIX')
        self.assertEqual(len(doc.attempts), 2)

    def test_normal_after_space_preserves_suffix(self):
        hook, mapper, doc, keys = self.fixture(text='PREFIX ghbdtn  SUFFIX',
                                               suffix='  ')
        with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertEqual(doc.text, 'PREFIX ' + mapper.word(keys, 'he') + '  SUFFIX')

    def test_second_pause_after_browser_dom_change_reports_safe_refusal(self):
        hook, mapper, doc, keys = self.fixture(text='PREFIX ghbdtn  SUFFIX',
                                               suffix='  ')
        hook.pipeline_debug = True
        with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
            self.assertEqual(len(doc.attempts), 1)
            different = mapper.word(mapper.keys_for('aaaaaa', 'en'), 'he')
            self.assertEqual(len(different), len(keys))
            doc.text = 'PREFIX ' + different + '  SUFFIX'
            before_second = doc.text
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertEqual(doc.text, before_second)
        self.assertEqual(len(doc.attempts), 1)
        reasons = [call.args[1] for call in hook.emit.call_args_list
                   if call.args[0] == 'probe'
                   and call.args[1].startswith('pipeline:manual-cycle:')]
        self.assertTrue(any('reason=text-changed' in line for line in reasons), reasons)
        self.assertFalse(hook.native_queue.busy)
        self.assertTrue(all('ghbdtn' not in line and different not in line for line in reasons))

    def test_literal_punctuation_and_brackets_do_not_block_safe_pause(self):
        for source, suffix in (
                ('PREFIX ghbdtn!  SUFFIX', '!  '),
                ('PREFIX (ghbdtn)  SUFFIX', ')  '),
                ('PREFIX ((ghbdtn))!!  SUFFIX', '))!!  ')):
            with self.subTest(source=source):
                hook, mapper, doc, keys = self.fixture(text=source, suffix=suffix)
                with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
                    self.assertEqual(key(hook, VK_PAUSE), 1)
                    self.release(hook)
                self.assertEqual(doc.text, source.replace('ghbdtn', mapper.word(keys, 'he')))
                self.assertTrue(doc.text.startswith('PREFIX '))
                self.assertTrue(doc.text.endswith(' SUFFIX'))
                self.assertEqual(len(doc.attempts), 1)

    def test_live_tracked_word_before_space_stays_addressed(self):
        hook, mapper, doc, keys = self.fixture(use_keys=True)
        with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertEqual(doc.text, 'PREFIX ' + mapper.word(keys, 'he') + ' SUFFIX')
        self.assertTrue(doc.text.startswith('PREFIX '))

    def test_oem_comma_in_interior_word_preserves_surrounding_text_and_caret(self):
        hook, mapper, doc, keys = self.fixture(
            text='PREFIX z,kj SUFFIX', token='z,kj', caret_offset=2)
        with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertEqual(doc.text, 'PREFIX ' + mapper.word(keys, 'he') + ' SUFFIX')
        self.assertEqual(doc.caret, 7 + len(keys) - 2)
        self.assertEqual(doc.anchor, doc.caret)

    def test_uia_identity_change_between_reads_rejects_equal_text(self):
        hook, _, doc, _ = self.fixture()
        original = doc.text
        observed = []
        def altered_identity(*args, **kwargs):
            word = doc.caret_word(*args, **kwargs)
            observed.append(word)
            return replace(word, element_id='uia:different') if len(observed) == 2 else word
        with patch('typebridge_app.read_caret_word', side_effect=altered_identity):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertEqual(len(observed), 2)
        self.assertEqual(doc.text, original)
        self.assertEqual(doc.attempts, [])

    def test_unsupported_reader_refuses_without_mutation(self):
        hook, _, doc, _ = self.fixture()
        initial = doc.text
        with patch('typebridge_app.read_caret_word', return_value=None):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertEqual(doc.text, initial)
        self.assertEqual(doc.attempts, [])

    def test_verified_richedit_needs_no_textpattern_and_browser_cannot_impersonate_it(self):
        # Supported HWND-addressed RichEdit may proceed without UIA. An Edit-
        # looking browser window must not inherit that permission, even if a
        # stale native_target flag erroneously says True.
        for process, native_allowed in (('notepad.exe', True), ('chrome.exe', False)):
            with self.subTest(process=process):
                hook, _, doc, _ = self.fixture()
                hook._in_keyboard_callback = False
                context = replace(hook.context(), process_name=process, native_target=True)
                hook.context = lambda: context
                hook.last_word.scope = hook._word_scope(context)
                hook.user32.GetClassNameW.side_effect = (
                    lambda _hwnd, buf, _size: setattr(buf, 'value', 'RichEditD2DPT'))
                hook._cycle_edit_word = Mock()
                with patch('typebridge_app.read_caret_word', return_value=None) as reader:
                    hook._cycle_word(context)
                if native_allowed:
                    hook._cycle_edit_word.assert_called_once()
                    reader.assert_not_called()
                else:
                    hook._cycle_edit_word.assert_not_called()
                    reader.assert_called_once()
                hook.user32.SendInput.assert_not_called()
                self.assertEqual(doc.text, 'PREFIX ghbdtn SUFFIX')

    def test_next_command_after_safe_refusal_is_not_blocked(self):
        hook, _, doc, keys = self.fixture(selected=True)
        with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
            self.assertFalse(hook.native_queue.busy)
            self.assertIsNone(getattr(hook, 'native_pending_recovery', None))
            doc.selection(doc.text.index('ghbdtn') + 6, doc.text.index('ghbdtn') + 6)
            # Another correctly tracked word may initiate a fresh operation.
            hook.last_word = remember_word(keys, 'ghbdtn', 'en', '',
                                           hook._word_scope(hook.context()), whole=True)
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertTrue(doc.text.startswith('PREFIX '))
        self.assertTrue(doc.text.endswith(' SUFFIX'))
        self.assertEqual(len(doc.attempts), 1)

    def test_uncertain_send_does_not_retry_whole_packet_or_undo(self):
        hook, _, doc, _ = self.fixture()
        original_send = doc.send
        def partial_then_zero(count, events, size):
            if not doc.attempts:
                accepted = min(2, count)  # One Backspace down/up is accepted.
                original_send(accepted, events, size)
                return accepted
            doc.attempts.append(count)
            return 0
        hook.user32.SendInput.side_effect = partial_then_zero
        with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertEqual(len(doc.attempts), 2)
        self.assertFalse(hook.native_queue.busy)
        self.assertIsNone(hook._keyboard_job_release)
        self.assertTrue(doc.text.startswith('PREFIX '))
        self.assertTrue(doc.text.endswith(' SUFFIX'))

    def test_sendinput_exception_after_unknown_partial_write_never_retries(self):
        hook, _, doc, _ = self.fixture()
        initial = doc.text
        attempts = []
        def unknown_write(count, events, size):
            attempts.append(count)
            # The system may have applied part of the packet before failing.
            doc.send(2, events, size)
            raise OSError('injected-uncertain-sendinput')
        hook.user32.SendInput.side_effect = unknown_write
        with patch('typebridge_app.read_caret_word', side_effect=doc.caret_word):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.release(hook)
        self.assertEqual(len(attempts), 1)
        self.assertNotEqual(doc.text, initial)
        self.assertTrue(doc.text.startswith('PREFIX '))
        self.assertTrue(doc.text.endswith(' SUFFIX'))
        self.assertFalse(hook.native_queue.busy)


if __name__ == '__main__':
    unittest.main()
