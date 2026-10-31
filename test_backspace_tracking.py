"""Known-word deletion and retyping; synthetic hook calls, no OS input."""
import unittest
from dataclasses import replace
from types import SimpleNamespace

from test_app_controls import make_hook, key
from typebridge_app import VK_BACK, VK_SPACE


class BackspaceTrackingTests(unittest.TestCase):
    def fixture(self):
        hook = make_hook()
        hook.settings = replace(hook.settings, early_correction=False)
        hook.user32.GetKeyState.return_value = 0
        hook.mouse_hook = None
        hook.engine.mapper = SimpleNamespace(
            char=lambda vk, *_: chr(vk).lower() if 65 <= vk <= 90 else ' ' if vk == VK_SPACE else '',
            word=lambda keys, lang: ''.join(chr(vk).lower() for vk, _ in keys),
            layouts={'en': 0x0409, 'ru': 0x0419, 'he': 0x040D})
        hook.clear_token()
        return hook

    def remember(self, hook):
        hook._remember(hook.context(), [(ord(c.upper()), False) for c in 'testing'],
                       'testing', 'en', ' ')

    def test_deleting_known_word_and_space_allows_next_word_correction(self):
        hook = self.fixture()
        self.remember(hook)
        for _ in range(8):
            self.assertEqual(key(hook, VK_BACK), 0)
        self.assertFalse(hook.token_blocked)
        for c in 'akuo':
            key(hook, ord(c.upper()))
        self.assertEqual(key(hook, VK_SPACE), 1)
        hook.user32.SendInput.assert_called_once()

    def test_typing_after_known_separator_deletion_tracks_the_whole_word(self):
        hook = self.fixture()
        self.remember(hook)
        key(hook, VK_BACK)
        key(hook, ord('X'))
        self.assertFalse(hook.token_blocked)
        self.assertEqual(hook.keys, [(ord(c.upper()), False) for c in 'testingx'])
        hook.user32.SendInput.assert_not_called()

    def test_deleting_retyped_known_prefix_keeps_exact_remaining_key(self):
        hook = self.fixture()
        self.remember(hook)
        key(hook, VK_BACK)
        key(hook, ord('X'))
        for _ in range(7):
            key(hook, VK_BACK)
        self.assertFalse(hook.token_blocked)
        self.assertEqual(hook.keys, [(ord('T'), False)])

    def test_removing_known_separator_restores_complete_tracked_word(self):
        hook = self.fixture()
        self.remember(hook)
        key(hook, VK_BACK)
        self.assertEqual(hook.keys, [(ord(c.upper()), False) for c in 'testing'])
        self.assertFalse(hook.token_blocked)
        key(hook, ord('X'))
        self.assertEqual(len(hook.keys), 8)
        self.assertEqual(key(hook, VK_SPACE), 1)
        packet = hook.user32.SendInput.call_args.args[1]
        deletes = [e for e in packet if e.u.ki.wVk == VK_BACK and not e.u.ki.dwFlags & 2]
        self.assertEqual(len(deletes), 8)  # no trailing-only replacement

    def test_remaining_known_separator_starts_independent_word(self):
        hook = self.fixture()
        self.remember(hook)
        hook.last_word.suffix = '  '
        key(hook, VK_BACK)
        self.assertFalse(hook.token_blocked)
        self.assertEqual(hook.last_word.suffix, ' ')
        key(hook, ord('X'))
        self.assertEqual(hook.keys, [(ord('X'), False)])

    def test_mapping_mismatch_does_not_restore_known_prefix(self):
        hook = self.fixture()
        self.remember(hook)
        hook.engine.mapper.word = lambda *_: 'different'
        key(hook, VK_BACK)
        self.assertTrue(hook.token_blocked)
        self.assertEqual(hook.keys, [])

    def test_foreign_word_scope_never_restores_keys(self):
        hook = self.fixture()
        self.remember(hook)
        hook.last_word.scope = ('another-field',)
        key(hook, VK_BACK)
        self.assertTrue(hook.token_blocked)
        self.assertEqual(hook.keys, [])

    def test_shift_backspace_does_not_guess_selection_behavior(self):
        from typebridge_app import VK_SHIFT
        hook = self.fixture()
        self.remember(hook)
        hook.pressed = lambda vk: vk == VK_SHIFT
        key(hook, VK_BACK)
        self.assertTrue(hook.token_blocked)
        self.assertEqual(hook.keys, [])

    def test_known_unfinished_word_restores_only_its_surviving_prefix(self):
        hook = self.fixture()
        self.remember(hook)
        hook.last_word.suffix = ''
        key(hook, VK_BACK)
        self.assertEqual(hook.keys, [(ord(c.upper()), False) for c in 'testin'])
        self.assertFalse(hook.token_blocked)
        hook.user32.SendInput.assert_not_called()

    def test_backspace_into_unknown_text_keeps_existing_guard(self):
        hook = self.fixture()
        for _ in range(8):
            key(hook, VK_BACK)
        self.assertTrue(hook.token_blocked)

    def test_deleting_all_tracked_letters_clears_early_done(self):
        hook = self.fixture()
        hook.keys = [(ord(c.upper()), False) for c in 'akuo']
        hook.early_done = True
        for _ in range(4):
            key(hook, VK_BACK)
        self.assertFalse(hook.early_done)
        self.assertFalse(hook.token_blocked)

    def test_extra_backspace_past_known_word_reenables_guard(self):
        hook = self.fixture()
        self.remember(hook)
        for _ in range(9):
            key(hook, VK_BACK)
        self.assertTrue(hook.token_blocked)

    def test_mouse_movement_to_another_caret_discards_delete_count(self):
        hook = self.fixture()
        self.remember(hook)
        key(hook, VK_BACK)
        hook.handle_mouse(0, 0x0201, 0)
        for _ in range(7):
            key(hook, VK_BACK)
        self.assertTrue(hook.token_blocked)

    def test_known_word_with_punctuation_is_not_assumed_one_character_per_delete(self):
        hook = self.fixture()
        hook._remember(hook.context(), [(ord(c.upper()), False) for c in 'testing'],
                       'testing', 'en', '. ')
        for _ in range(9):
            key(hook, VK_BACK)
        self.assertTrue(hook.token_blocked)


if __name__ == '__main__':
    unittest.main()
