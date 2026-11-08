"""Browser target admission policy: mocked metadata, no hooks or real UIA."""
import json
import unittest
from unittest.mock import Mock, patch

from engine import LayoutMapper, Decision
from caret_word_recovery import parse_caret_word, read_caret_word
import test_field_states_20261106 as fields
from test_unified_manual_20261106 import apply_packet
from typebridge_app import Settings, staged_password_guard, manual_cycle_inputs, VK_SPACE
from word_cycle import remember_word
from test_app_controls import key


class UserFieldPolicyTests(unittest.TestCase):
    def test_helper_readiness_does_not_invalidate_a_word_but_field_changes_do(self):
        guard = staged_password_guard(Settings())
        revision = guard.permit_revision(10, 20)
        guard.ingest(fields.sample('uia:4,7') + '|0')
        self.assertEqual(guard.permit_revision(10, 20), revision)
        guard._update(0, 0, 'unknown', 'helper-timeout')
        self.assertEqual(guard.permit_revision(10, 20), revision)
        guard.ingest(fields.sample('uia:4,7') + '|1')
        self.assertEqual(guard.permit_revision(10, 20), revision)
        guard.ingest(fields.sample('uia:4,8') + '|1')
        self.assertNotEqual(guard.permit_revision(10, 20), revision)
        revision = guard.permit_revision(10, 20)
        guard.invalidate()
        self.assertNotEqual(guard.permit_revision(10, 20), revision)

    def test_browser_unknown_stale_and_unaddressable_refuse_both_options(self):
        for option in (False, True):
            hook, guard = fields.FieldStateMatrixTests().fixture(option, process='chrome.exe')
            self.assertTrue(guard.explicit_password_only)
            self.assertIsNone(hook.context())  # No metadata from native worker yet.
            for state, element in [('unknown', ''), ('safe', 'uia:4,7'), ('safe', 'win32:20')]:
                guard.ingest(fields.sample(element, state) + '|0')
                self.assertIsNone(hook.context())
            guard.ingest(fields.sample('uia:4,7') + '|1')
            context = hook.context()
            self.assertIsNotNone(context)
            self.assertTrue(guard.revalidate(10, 20, context.password_revision))
            guard._update(10, 20, 'unknown', 'helper-timeout')
            self.assertIsNone(hook.context())
            guard._update(10, 20, 'safe', element_id='uia:4,7', checked_at=guard.clock()-10)
            self.assertIsNone(hook.context())
            guard.ingest(fields.sample('uia:4,7', 'password'))
            self.assertIsNone(hook.context())
            self.assertIsNotNone(guard.permit_revision(10, 21))
            guard.invalidate()
            self.assertIsNone(hook.context())

    def test_actual_send_requires_fresh_addressable_browser_metadata(self):
        for manual in (False, True):
            hook, guard = fields.FieldStateMatrixTests().fixture(False, process='chrome.exe')
            self.assertIsNone(hook.context())
            guard.ingest(fields.sample('uia:4,7') + '|1')
            context = hook.context()
            self.assertIsNotNone(context)
            hook.engine.mapper = LayoutMapper()
            hook.engine.active_languages = ('en', 'ru', 'he')
            hook.engine.decide_token = Mock(return_value=Decision(
                'ghjdthrf', '\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430', 'ru', 'unique dictionary match'))
            keys = [(ord(c.upper()), False) for c in 'ghjdthrf']
            hook.keys = list(keys)
            hook.token_blocked = False
            hook._in_keyboard_callback = False
            hook._queue_browser_readback = Mock()
            hook._record_replay_layout = Mock(return_value=int(hook.engine.mapper.layouts['ru']))
            hook.user32.GetKeyboardLayout.return_value = int(hook.engine.mapper.layouts['en'])
            hook.user32.GetKeyState.return_value = 0
            context = hook.context()
            hook._tracked_password_revision = context.password_revision
            if manual:
                hook.paused = True
                hook.last_word = remember_word(keys, 'ghjdthrf', 'en', '', hook._word_scope(context))
                hook.last_focus, hook.last_language = context.focus, context.language
                from caret_word_recovery import CaretWord
                with patch('typebridge_app.read_caret_word', return_value=CaretWord(
                        'ghjdthrf', tuple(keys), '', None, 'uia:4,7')):
                    self.assertEqual(key(hook, 0x13), 1)
            else:
                hook._correct(context, VK_SPACE, '')
            hook.user32.SendInput.assert_called_once()
            # Positive password evidence still blocks the same real send path.
            guard.ingest(fields.sample('uia:4,7', 'password'))
            hook.user32.SendInput.reset_mock()
            sent, _ = hook._send_guarded_inputs(manual_cycle_inputs(
                remember_word(keys, 'ghjdthrf', 'en', '', ()), 'test'), lambda: hook.context() is not None)
            self.assertEqual(sent, 0)
            hook.user32.SendInput.assert_not_called()

    def test_oem_letter_is_part_of_whole_word_at_every_caret_position(self):
        mapper = LayoutMapper()
        for offset in range(5):
            word = parse_caret_word(dict(before='z,kj'[:offset], after='z,kj'[offset:]+' ',
                at_start=True, at_end=False, element_id='uia:4,7'), mapper, 'en', 'uia:4,7')
            self.assertIsNotNone(word)
            self.assertEqual(word.text, 'z,kj')
            recent = remember_word(word.keys, word.text, 'en', '', (), whole=True,
                                   caret_offset=word.caret_offset)
            language, replacement = recent.next_variant(mapper, ('en', 'ru', 'he'))
            self.assertEqual(language, 'ru')
            self.assertEqual(replacement, '\u044f\u0431\u043b\u043e')
            text, cursor = apply_packet('z,kj ', offset, manual_cycle_inputs(recent, replacement))
            self.assertEqual(text, replacement+' ')
            self.assertEqual(cursor, offset)

    def test_reader_captures_own_identity_when_metadata_is_missing(self):
        from typebridge_app import FocusContext
        result = dict(before='z,', after='kj ',
                      at_start=True, at_end=False, element_id='uia:4,7')
        with patch('uia_native_worker.get_uia_service') as getter:
            getter.return_value.request.return_value = result
            word = read_caret_word(FocusContext(10,20,1,'en','chrome.exe','test'),
                                   LayoutMapper(), '', lambda: True)
        self.assertEqual(word.text, 'z,kj')


if __name__ == '__main__':
    unittest.main()
