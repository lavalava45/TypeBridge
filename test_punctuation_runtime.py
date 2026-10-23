"""Deterministic editor model; no hooks or input are sent to Windows."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from engine import LanguageEngine, source_data_dir, LANGS, candidate_token_key, _valid_typed
from typebridge_app import FocusContext, TypeBridgeGui, VK_SHIFT, WM_KEYUP
from test_app_controls import make_hook, key


class PunctuationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = LanguageEngine(source_data_dir())
        # Test OEM-key transport, not the frequency rank of this corpus word.
        cls.engine.words['ru'].add('вьюга')
        cls.engine.confident['ru'].add('вьюга')
        cls.physical = {}
        keys = [0x20, *range(0x30, 0x3A), *range(0x41, 0x5B),
                0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE]
        for vk in keys:
            for shift in (False, True):
                cls.physical.setdefault(cls.engine.mapper.char(vk, "en", shift), (vk, shift))

    def type_text(self, physical, source="en", before_vk=None):
        hook = make_hook()
        hook.engine = self.engine
        hook.keys = []
        hook.token_blocked = False
        hook.last_language = source
        current = [source]
        shift = [False]
        document = []
        hook.context = lambda: FocusContext(90, 100, 1, current[0], "editor.exe", "test")
        hook.pressed = lambda vk: vk == VK_SHIFT and shift[0]
        hook.user32.GetKeyState.return_value = 0
        hook.user32.GetKeyboardLayout.side_effect = lambda _: LANGS[current[0]]

        def request_layout(hwnd, message, wparam, hkl, *args):
            current[0] = next(lang for lang, code in LANGS.items() if code == (hkl & 0xFFFF))
            return 1

        def send_input(count, batch, size):
            for event in batch:
                k = event.u.ki
                if k.dwFlags == 0 and k.wVk == 8:
                    if document:
                        document.pop()
                elif k.dwFlags == 4:
                    document.append(chr(k.wScan))
                elif k.dwFlags == 0 and k.wVk == 0x20:
                    document.append(" ")
            return count

        hook.user32.SendMessageTimeoutW.side_effect = request_layout
        hook.user32.SendInput.side_effect = send_input
        if before_vk is not None:
            key(hook, before_vk)
            key(hook, before_vk, WM_KEYUP)
        for char in physical:
            vk, shift[0] = self.physical[char]
            if key(hook, vk) == 0:
                document.append(self.engine.mapper.char(vk, current[0], shift[0]))
            key(hook, vk, WM_KEYUP)
        return "".join(document)

    def test_all_nonletter_keys_mapping_to_letters_are_collectible_with_and_without_shift(self):
        audited = 0
        hook = make_hook()
        hook.engine = self.engine
        for current in LANGS:
            for vk in (*range(0x30, 0x3A), 0xBA, 0xBB, 0xBC, 0xBD, 0xBE,
                       0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE):
                for shift in (False, True):
                    char = self.engine.mapper.char(vk, current, shift)
                    if len(char) != 1 or char.isalpha():
                        continue
                    if not any(self.engine.mapper.char(vk, lang, shift).isalpha()
                               for lang in LANGS if lang != current):
                        continue
                    audited += 1
                    with self.subTest(current=current, vk=vk, shift=shift):
                        self.assertTrue(candidate_token_key(self.engine.mapper, vk, shift, current, False))
                        self.assertTrue(_valid_typed([(vk, shift)], char, current))
                        for pending in ([], [(65, False)]):
                            hook.keys = pending
                            self.assertIsNone(hook._boundary(vk, current, shift))
        self.assertGreaterEqual(audited, 20)

    def test_capital_letters_on_punctuation_keys(self):
        for physical, expected in (("<tksq ", "Белый "), (">ysq ", "Юный "),
                                    (":tknsq ", "Желтый "), ("{jkjl ", "Холод "),
                                    ('"[j ', "Эхо ")):
            with self.subTest(physical=physical):
                self.assertEqual(self.type_text(physical), expected)

    def test_punctuation_keys_inside_russian_words(self):
        for physical, expected in ((",t;fnm ", "бежать "), ("gj;fkeqcnf ", "пожалуйста "),
                                    ("dm.uf ", "вьюга "), ("j,]zdktybt ", "объявление ")):
            with self.subTest(physical=physical):
                self.assertEqual(self.type_text(physical), expected)

    def test_questions_exclamations_commas_periods_and_colons(self):
        for suffix in ("?", "!", ".", ",", ";", ":", "?!", "...", ",,", "!!"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.type_text("ghbdtn" + suffix + " "), "привет" + suffix + " ")

    def test_brackets_and_quotes_survive_correction(self):
        for left, right in (("(", ")"), ("[", "]"), ("{", "}"), ('"', '"'),
                            ("'", "'"), ("((", "))"), ("[\"", '\"]')):
            with self.subTest(left=left, right=right):
                self.assertEqual(self.type_text(left + "ghbdtn" + right + " "),
                                 left + "привет" + right + " ")
        self.assertEqual(self.type_text("(ghbdtn)? "), "(привет)? ")
        self.assertEqual(self.type_text('"akuo" '), '"שלום" ')

    def test_correct_english_with_punctuation_stays_unchanged(self):
        for text in ("hello? ", "hello, ", "hello: ", "(hello) ", "[hello] ",
                     '"hello" ', "'hello' ", "<hello ", ",hello ", ".hello ",
                     "?! ", "() ", "[] ", "{} ", "... "):
            with self.subTest(text=text):
                self.assertEqual(self.type_text(text), text)

    def test_mail_urls_paths_digits_and_identifiers_do_not_restart_from_suffix(self):
        for text in ("test@ghbdtn ", "https://ghbdtn ", "C:\\ghbdtn ",
                     "id_ghbdtn ", "123ghbdtn ", "ghbdtn123 ", "x-ghbdtn "):
            with self.subTest(text=text):
                self.assertEqual(self.type_text(text), text)

    def test_nonprinting_keys_do_not_disable_the_next_word(self):
        for vk in (0x14, 0x25, 0x24):  # CapsLock, Left, Home.
            with self.subTest(vk=vk):
                self.assertEqual(self.type_text("ghbdtn ", before_vk=vk), "привет ")

    def test_ambiguity_and_personal_exclusions_survive_punctuation_fallback(self):
        from user_lexicon import UserLexicon
        self.assertEqual(self.type_text("(rfc) "), "(rfc) ")
        lexicon = UserLexicon()
        lexicon.add("exclusions", "ru", "привет")
        previous = self.engine.user_lexicon
        try:
            self.engine.user_lexicon = lexicon
            self.assertEqual(self.type_text("(ghbdtn) "), "(ghbdtn) ")
        finally:
            self.engine.user_lexicon = previous

    def test_flag_updates_without_hook_in_stopped_and_paused_modes(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.closing = False
        gui.caret_indicator = None
        gui.tray = Mock()
        gui.root = Mock()
        for hook in (None, SimpleNamespace(paused=True)):
            gui.hook = hook
            gui.last_tray_language = None
            with patch("typebridge_app.foreground_language", return_value="he"):
                gui._update_tray()
            gui.tray.set_language.assert_called_with("he")


if __name__ == "__main__":
    unittest.main()
