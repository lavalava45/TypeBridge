"""Single manual command: pure packet/model checks, no foreground or global hook."""
from dataclasses import replace
import unittest

from caret_word_recovery import parse_caret_word
from direct_hotkeys import compile_bindings
from engine import LayoutMapper
from typebridge_app import (Settings, VK_BACK, VK_DELETE, VK_LEFT,
                            manual_cycle_inputs, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE)
from word_cycle import effective_word_key, remember_word


def apply_packet(source, cursor, packet):
    """Tiny document model; does not claim a real editor accepted SendInput."""
    content = source
    for item in packet:
        stroke = item.u.ki
        if stroke.dwFlags & KEYEVENTF_KEYUP:
            continue
        if stroke.dwFlags & KEYEVENTF_UNICODE:
            content = content[:cursor] + chr(stroke.wScan) + content[cursor:]
            cursor += 1
        elif stroke.wVk == VK_BACK:
            if cursor:
                content = content[:cursor-1] + content[cursor:]
                cursor -= 1
        elif stroke.wVk == VK_DELETE:
            content = content[:cursor] + content[cursor+1:]
        elif stroke.wVk == VK_LEFT:
            cursor = max(0, cursor-1)
        else:
            raise AssertionError(f'unexpected injected virtual key {stroke.wVk}')
    return content, cursor


class SingleManualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapper = LayoutMapper()

    def test_migration_is_explicit_and_new_selection_wins(self):
        cases = (
            (Settings(), 'Pause'),
            (replace(Settings(), manual_key='F8'), 'F8'),
            (replace(Settings(), manual_key='F8', cycle_key='F9'), 'F9'),
            (replace(Settings(), cycle_key='F9'), 'F9'),
            (replace(Settings(), cycle_key='Отключено'), 'Scroll Lock'),
            (replace(Settings(), cycle_key='Отключено', manual_f8=False), 'Отключено'),
            (replace(Settings(), manual_f8=False), 'Pause'),
            (replace(Settings(), manual_key='F8', cycle_key='F9', word_key='F10'), 'F10'),
        )
        for profile, expected in cases:
            with self.subTest(profile=profile):
                self.assertEqual(effective_word_key(profile), expected)
                compile_bindings(profile)
        with self.assertRaises(ValueError):
            compile_bindings(replace(Settings(), word_key='Unknown'))

    def test_middle_of_unknown_word_recovery_reads_both_halves(self):
        sample = {'before':'prefix gh', 'after':'bdtn ', 'at_start':False,
                  'at_end':False, 'element_id':'uia:4,7'}
        word = parse_caret_word(sample, self.mapper, 'en', 'uia:4,7')
        self.assertIsNotNone(word)
        self.assertEqual((word.text, word.suffix, word.caret_offset), ('ghbdtn', '', 2))
        recent = remember_word(word.keys, word.text, 'en', word.suffix, (1, 2),
                               whole=True, caret_offset=word.caret_offset)
        self.assertIsNotNone(recent)
        language, replacement = recent.next_variant(self.mapper, ('en', 'ru', 'he'))
        self.assertEqual((language, replacement), ('ru', 'привет'))
        updated, cursor = apply_packet('prefix ghbdtn tail', len('prefix gh'),
                                       manual_cycle_inputs(recent, replacement))
        self.assertEqual((updated, cursor), ('prefix привет tail', len('prefix пр')))
        recent.text, recent.language = replacement, language
        _, next_replacement = recent.next_variant(self.mapper, ('en', 'ru', 'he'))
        updated, cursor = apply_packet(updated, cursor,
                                       manual_cycle_inputs(recent, next_replacement))
        self.assertEqual(cursor, len('prefix ') + 2)
        self.assertEqual(updated, 'prefix ' + next_replacement + ' tail')

    def test_spaces_are_preserved_and_no_dictionary_is_consulted(self):
        keys = tuple((ord(ch.upper()), False) for ch in 'xyzzy')
        word = remember_word(keys, 'xyzzy', 'en', '   ', (1, 2))
        self.assertIsNotNone(word)
        target_language, replacement = word.next_variant(self.mapper, ('en', 'ru', 'he'))
        self.assertEqual(target_language, 'ru')
        updated, caret = apply_packet('prefix xyzzy   ', len('prefix xyzzy   '),
                                      manual_cycle_inputs(word, replacement))
        self.assertEqual(updated, 'prefix ' + replacement + '   ')
        self.assertEqual(caret, len(updated))

    def test_unbounded_or_changed_middle_is_rejected(self):
        sample = {'before':'gh', 'after':'bdtn', 'at_start':True,
                  'at_end':False, 'element_id':'uia:4,7'}
        self.assertIsNone(parse_caret_word(sample, self.mapper, 'en', 'uia:4,7'))
        sample['at_end'] = True
        self.assertIsNotNone(parse_caret_word(sample, self.mapper, 'en', 'uia:4,7'))
        sample['before'] = 'gh '
        self.assertIsNone(parse_caret_word(sample, self.mapper, 'en', 'uia:4,7'))
        sample['before'] = 'gh'; sample['element_id'] = 'uia:other'
        self.assertIsNone(parse_caret_word(sample, self.mapper, 'en', 'uia:4,7'))


if __name__ == '__main__':
    unittest.main()
