"""P0 Chrome selected-word Pause: read-only UIA lease, no blind Backspace.

All document text is synthetic in memory. Never touch the user's foreground,
install hooks, or read actual UIA content during these tests.
"""
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from caret_word_recovery import SelectedWord
from experiments.r10_selection_reuse_audit_20261107 import FrozenMapper, KEYMAP
from test_p0d_generic_pause_20261107 import Document
from test_hook_cycle_offhook import GenericCycleOffHookTests


class ChromeSelectedPauseTests(unittest.TestCase):
    def fixture(self, *, text='PREFIX ch, SUFFIX'):
        hook, base = GenericCycleOffHookTests.fixture(self)
        mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
        mapper.layouts = {code: int(value, 16) if isinstance(value, str) else value
                          for code, value in mapper.layouts.items()}
        hook.engine.mapper = mapper
        hook.engine.active_languages = ('ru', 'en', 'he')
        hook.engine.language_catalog = None
        hook._in_keyboard_callback = False
        hook.user32.GetKeyboardLayout.return_value = mapper.layouts['en']
        source = replace(base, process_name='chrome.exe', language='en',
                         password_revision=7, hkl=mapper.layouts['en'])
        hook.context = lambda: source
        hook.password_guard = SimpleNamespace(
            snapshot=SimpleNamespace(element_id='uia:11,22', state='safe',
                                     addressable=True),
            revalidate=Mock(return_value=True),
            permit_revision=Mock(return_value=7))
        start = text.index('ch,')
        doc = Document(text, start+3, anchor=start)
        hook.user32.SendInput.side_effect = doc.send

        def layout(_hwnd, _message, _flags, hkl, *_args):
            hook.user32.GetKeyboardLayout.return_value = hkl
            return 1

        hook.user32.SendMessageTimeoutW.side_effect = layout
        keys = mapper.keys_for('ch,', 'en')
        selected = SelectedWord('ch,', tuple(keys), 'uia:11,22', 6,
                                hashlib.sha256('ch,'.encode('utf-16-le')).hexdigest(),
                                'a' * 32)
        return hook, source, doc, mapper, selected

    def test_exact_selection_replaced_without_any_backspace(self):
        hook, context, doc, mapper, selected = self.fixture()
        with patch('typebridge_app.read_selected_word', return_value=selected) as read, \
                patch('typebridge_app.verify_selected_word', return_value=True) as verify:
            hook._cycle_word(context)
        self.assertEqual(doc.text, 'PREFIX בית SUFFIX')
        self.assertEqual(hook.last_language, 'he')
        self.assertTrue(hook.token_blocked)
        self.assertEqual(doc.attempts, [6])  # 3 Unicode down/up pairs; no Backspace.
        self.assertTrue(read.called)
        self.assertTrue(verify.called)
        hook.password_guard.revalidate.assert_called()
        self.assertTrue(any(call.args[0] == 'info'
                            and 'не подтверждено' in call.args[1]
                            for call in hook.emit.call_args_list))

    def test_selection_moved_before_final_verification_never_mutates(self):
        hook, context, doc, _, selected = self.fixture()
        original = doc.text
        with patch('typebridge_app.read_selected_word', return_value=selected), \
                patch('typebridge_app.verify_selected_word', return_value=False):
            hook._cycle_word(context)
        self.assertEqual(doc.text, original)
        self.assertEqual(doc.attempts, [])
        self.assertFalse(getattr(hook, 'native_edit_disabled', False))

    def test_refused_selected_reader_never_falls_back_to_destructive_backspaces(self):
        hook, context, doc, _, _ = self.fixture()
        original = doc.text
        hook.last_word = None
        with patch('typebridge_app.read_selected_word', return_value=None), \
                patch('typebridge_app.read_caret_word', side_effect=doc.caret_word), \
                patch.object(hook, '_try_caret_recovery', return_value=False):
            hook._cycle_word(context)
        self.assertEqual(doc.text, original)
        self.assertEqual(doc.attempts, [])

    def test_selection_path_is_off_hook(self):
        hook, context, doc, _, _ = self.fixture()
        hook._in_keyboard_callback = True
        hook._schedule_keyboard = Mock(return_value=True)
        with patch('typebridge_app.read_selected_word') as read:
            hook._cycle_word(context)
        read.assert_not_called()
        hook._schedule_keyboard.assert_called_once()
        self.assertTrue(hook._schedule_keyboard.call_args.kwargs['drop_always'])
        self.assertTrue(hook._schedule_keyboard.call_args.kwargs['allow_paused'])
        self.assertEqual(doc.attempts, [])


if __name__ == '__main__':
    unittest.main()
