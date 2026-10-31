"""Early-correction protocol and keyboard ordering; no OS hooks or real input."""
from dataclasses import replace
from unittest.mock import patch
import unittest

from early_scintilla import replace_prefix
from incremental_recognition import prepare_early
from typebridge_app import FocusContext
from test_word_cycle import WordCycleTests


class ByteEditor:
    def __init__(self, text, caret=None):
        self.data = text.encode("utf-8")
        self.caret = len(self.data) if caret is None else caret
        self.timeout = self.write_started = False
        self.writes = 0
    def snapshot(self):
        return self.data, self.caret
    def prepare(self, start, end, target):
        pass
    def check_deadline(self):
        pass
    def replace(self, start, end, target):
        self.write_started = True
        self.writes += 1
        self.data = self.data[:start] + target + self.data[end:]
        self.caret = start + len(target)


class TransactionTests(unittest.TestCase):
    def test_utf8_mid_document_preserves_both_sides_and_caret(self):
        editor = ByteEditor("שלום рудд хвост", len("שלום рудд".encode("utf-8")))
        result = replace_prefix(editor, "рудд", "hello", lambda: True)
        self.assertEqual(result.state, "verified")
        self.assertEqual(editor.snapshot(), ("שלום hello хвост".encode(), len("שלום hello".encode())))

    def test_absent_or_nonindependent_source_cannot_be_removed(self):
        for text in ("руд", "prefixрудд"):
            editor = ByteEditor(text)
            result = replace_prefix(editor, "рудд", "hello", lambda: True)
            self.assertEqual(result.state, "refused")
            self.assertEqual(editor.writes, 0)

    def test_focus_change_immediately_before_write_prevents_it(self):
        editor = ByteEditor("рудд")
        valid = iter((True, False))
        result = replace_prefix(editor, "рудд", "hello", lambda: next(valid))
        self.assertEqual(result.state, "refused")
        self.assertEqual(editor.writes, 0)

    def test_failure_after_write_is_uncertain_without_rollback(self):
        editor = ByteEditor("рудд")
        original_replace = editor.replace
        def broken(*args):
            original_replace(*args)
            raise TimeoutError()
        editor.replace = broken
        self.assertEqual(replace_prefix(editor, "рудд", "hello", lambda: True).state, "uncertain")
        self.assertEqual((editor.data, editor.writes), (b"hello", 1))


class EarlyRuntimeTests(WordCycleTests):
    # Inherited state-machine regressions also run with early mode enabled.
    def setUp(self):
        super().setUp()
        self.hook.settings = replace(self.hook.settings, early_correction=True)
        prepare_early(self.engine)
        self.hook.context = lambda: FocusContext(90, self.focus, 1, self.language,
                                                "notepad++.exe", "test", 100)
        owner = self
        class Bridge:
            timeout = False
            write_started = False
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def snapshot(self): return owner.text.encode(), len(owner.text.encode())
            def prepare(self, *args): pass
            def check_deadline(self): pass
            def replace(self, start, end, target):
                self.write_started = True
                data = owner.text.encode()
                owner.text = (data[:start] + target + data[end:]).decode()
        self.bridge = Bridge()
        self.patch = patch("typebridge_app.ScintillaEarlyBridge", return_value=self.bridge)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_six_directions_finish_before_space_without_keyboard_injection(self):
        for source, physical, target, text in (
                ("ru", "hello", "en", "hello"), ("he", "hello", "en", "hello"),
                ("en", "ghbdtn", "ru", "привет"), ("he", "ghbdtn", "ru", "привет"),
                ("en", "akuo", "he", "שלום"), ("ru", "akuo", "he", "שלום")):
            self.text = ""; self.language = source
            self.hook.clear_token(); self.hook.last_language = source
            self.hook.user32.SendInput.reset_mock()
            self.type(physical)
            self.assertEqual((self.text, self.language), (text, target), (source, target))
            self.hook.user32.SendInput.assert_not_called()

    def test_continuation_space_and_next_word_are_not_duplicated(self):
        self.language = self.hook.last_language = "ru"
        self.type("hello ghbdtnf ")
        self.assertEqual(self.text, "hello привета ")

    def test_legitimate_source_word_is_not_changed_early(self):
        self.type("hello")
        self.assertEqual((self.text, self.language), ("hello", "en"))
        self.assertFalse(self.bridge.write_started)

    def test_undelivered_prefix_forwards_current_key_instead_of_deleting(self):
        self.language = self.hook.last_language = "ru"
        self.bridge.snapshot = lambda: (b"", 0)
        self.type("hello")
        self.assertEqual(self.text, "руддщ")
        self.assertFalse(self.bridge.write_started)

    def test_unknown_editor_keeps_existing_boundary_correction(self):
        self.language = self.hook.last_language = "ru"
        self.hook.context = lambda: FocusContext(90, 100, 1, self.language, "editor.exe", "test", 100)
        self.type("hello")
        self.assertEqual(self.text, "руддщ")
        self.type(" ")
        self.assertEqual(self.text, "hello ")


class KeyboardEarlyTests(unittest.TestCase):
    def test_oem_punctuation_key_can_be_a_letter_in_the_target_word(self):
        self.driver.type('ghj,ktvf')
        self.assertEqual((self.driver.text, self.driver.language), ('проблема', 'ru'))

    @classmethod
    def setUpClass(cls):
        from engine import LanguageEngine, source_data_dir
        cls.engine = LanguageEngine(source_data_dir())

    def setUp(self):
        self.driver = WordCycleTests()
        self.driver.engine = self.engine
        self.driver.setUp()
        self.hook = self.driver.hook
        self.hook.settings = replace(self.hook.settings, early_correction=True, early_native=True)
        prepare_early(self.engine)
        def name(hwnd, buffer, size):
            buffer.value = 'Chrome_RenderWidgetHostHWND'
            return len(buffer.value)
        self.hook.user32.GetClassNameW.side_effect = name

    def test_six_directions_before_space_through_one_keyboard_transport(self):
        for source, physical, target, expected in (
                ('ru','hello','en','hello'), ('he','hello','en','hello'),
                ('en','ghbdtn','ru','привет'), ('he','ghbdtn','ru','привет'),
                ('en','akuo','he','שלום'), ('ru','akuo','he','שלום')):
            self.driver.text = ''
            self.driver.language = self.hook.last_language = source
            self.hook.clear_token()
            self.driver.type(physical)
            self.assertEqual((self.driver.text,self.driver.language),(expected,target))

    def test_continuation_separator_and_next_word(self):
        self.driver.language = self.hook.last_language = 'ru'
        self.driver.type('hello ghbdtnf ')
        self.assertEqual(self.driver.text,'hello привета ')

    def test_zero_insertions_forward_held_character_without_layout_change(self):
        self.driver.language = self.hook.last_language = 'ru'
        self.hook.user32.SendInput.side_effect = lambda *args: 0
        self.driver.type('hello')
        self.assertEqual((self.driver.text,self.driver.language),('руддщ','ru'))
        self.assertFalse(getattr(self.hook,'keyboard_early_disabled',False))
        self.assertTrue(self.hook.token_blocked)

    def test_partial_insertions_do_not_retry_or_change_layout(self):
        self.driver.language = self.hook.last_language = 'ru'
        self.hook.user32.SendInput.side_effect = lambda *args: 1
        self.driver.type('hello')
        self.assertTrue(self.hook.keyboard_early_disabled)
        self.assertTrue(self.hook.token_blocked)
        self.assertEqual(self.hook.user32.SendInput.call_count,2)  # Batch + keyup cleanup only.
        self.hook.user32.SendMessageTimeoutW.assert_not_called()

    def test_changed_field_prevents_keyboard_submission(self):
        self.driver.language = self.hook.last_language = 'ru'
        self.hook.keys = [(ord(c.upper()),False) for c in 'hell']
        original = self.hook.context()
        self.driver.focus = 101
        self.assertFalse(self.hook._try_early(original))
        self.hook.user32.SendInput.assert_not_called()


if __name__ == "__main__":
    unittest.main()
