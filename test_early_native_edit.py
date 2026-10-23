"""Range proof and state-machine regressions, without OS hooks or live input."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from early_native_edit import utf16_position, utf8_position
from early_scintilla import replace_prefix
from test_early_runtime import ByteEditor
from test_app_controls import make_hook
from typebridge_app import FocusContext
from engine import Decision
from early_live_trial import TrialHook


class PositionTests(unittest.TestCase):
    def test_utf16_utf8_positions_with_emoji_hebrew_russian_and_crlf(self):
        text = '🙂 שלום\r\nпривет end'
        for i in range(len(text) + 1):
            prefix = text[:i]
            units, size = len(prefix.encode('utf-16-le')) // 2, len(prefix.encode('utf-8'))
            self.assertEqual(utf16_position(text, units), size)
            self.assertEqual(utf8_position(text, size), units)
        with self.assertRaises(UnicodeError):
            utf16_position(text, 1)  # Middle of a surrogate pair.
        with self.assertRaises(UnicodeError):
            utf8_position(text, 1)  # Middle of a UTF-8 sequence.

    def test_focus_change_after_selection_is_uncertain_not_replayed(self):
        editor = ByteEditor('рудд')
        def changed_selection(*args):
            editor.write_started = True
            raise ValueError('context-changed')
        editor.replace = changed_selection
        self.assertEqual(replace_prefix(editor, 'рудд', 'hello', lambda: True).state, 'uncertain')
        self.assertEqual(editor.data, 'рудд'.encode())


class TrialDeliveryTests(unittest.TestCase):
    def test_other_window_is_not_observed_or_intercepted(self):
        hook = TrialHook.__new__(TrialHook)
        hook.user32 = Mock()
        hook.trial_top, hook.trial_field = 90, 100
        hook.user32.GetForegroundWindow.return_value = 91
        self.assertIsNone(hook.context())
        hook.user32.GetGUIThreadInfo.assert_not_called()

    def test_other_field_inside_trial_is_not_observed_or_intercepted(self):
        import ctypes
        from typebridge_app import GuiThreadInfo
        hook = TrialHook.__new__(TrialHook)
        hook.user32 = Mock()
        hook.trial_top, hook.trial_field = 90, 100
        hook.user32.GetForegroundWindow.return_value = 90
        hook.user32.GetWindowThreadProcessId.return_value = 1
        def gui(thread, pointer):
            ctypes.cast(pointer, ctypes.POINTER(GuiThreadInfo)).contents.hwndFocus = 101
            return True
        hook.user32.GetGUIThreadInfo.side_effect = gui
        self.assertIsNone(hook.context())
        hook.user32.GetKeyboardLayout.assert_not_called()

    def fixture(self, source='en', typed='ghbdtn', target='ru', result='привет'):
        hook = TrialHook.__new__(TrialHook)
        hook.__dict__.update(make_hook().__dict__)
        language = [source]
        layouts = {'ru': 0x419, 'en': 0x409, 'he': 0x40D}
        hook.engine = SimpleNamespace(mapper=SimpleNamespace(layouts=layouts))
        hook.keys = [(0x41, False)] * len(typed)
        hook.context = lambda: FocusContext(90, 100, 1, language[0], 'trial', 'test', 100)
        hook.pressed = lambda vk: False
        hook.user32.GetKeyboardLayout.side_effect = lambda tid: layouts[language[0]]
        def switch(hwnd, msg, wp, hkl, *rest):
            language[0] = next(key for key, value in layouts.items() if hkl == value)
            return 1
        hook.user32.SendMessageTimeoutW.side_effect = switch
        class Editor(ByteEditor):
            budget_exceeded = False
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def check_deadline(self): pass
        editor = Editor(typed[:-1])
        return hook, editor, Decision(typed, result, target, 'test'), language

    def test_six_delivery_directions_before_space_without_sendinput(self):
        for source, typed, target, result in (
                ('en', 'ghbdtn', 'ru', 'привет'), ('he', 'עינגאמ', 'ru', 'привет'),
                ('ru', 'руддщ', 'en', 'hello'), ('he', 'יקךךם', 'en', 'hello'),
                ('en', 'akuo', 'he', 'שלום'), ('ru', 'флгщ', 'he', 'שלום')):
            hook, editor, decision, language = self.fixture(source, typed, target, result)
            with patch('early_live_trial.NativeEditBridge', return_value=editor), patch('early_live_trial.suggest_live_early', return_value=decision):
                self.assertTrue(hook._try_early(hook.context()))
            self.assertEqual(editor.data, result.encode())
            self.assertEqual(language[0], target)
            self.assertTrue(hook.early_done)
            hook.user32.SendInput.assert_not_called()

    def test_refusal_does_not_consume_held_key_or_change_layout(self):
        hook, editor, decision, language = self.fixture()
        editor.data = b'prefix-not-delivered'
        with patch('early_live_trial.NativeEditBridge', return_value=editor), patch('early_live_trial.suggest_live_early', return_value=decision):
            self.assertFalse(hook._try_early(hook.context()))
        self.assertEqual(language[0], 'en')
        self.assertFalse(editor.write_started)
        hook.user32.SendMessageTimeoutW.assert_not_called()

    def test_partial_write_disables_retry_and_consumes_current_key(self):
        hook, editor, decision, language = self.fixture()
        replace = editor.replace
        def uncertain(*args):
            replace(*args)
            raise TimeoutError('test')
        editor.replace = uncertain
        with patch('early_live_trial.NativeEditBridge', return_value=editor) as factory, patch('early_live_trial.suggest_live_early', return_value=decision):
            self.assertTrue(hook._try_early(hook.context()))
            self.assertTrue(hook.early_disabled)
            self.assertFalse(hook._try_early(hook.context()))
            factory.assert_called_once()
        self.assertTrue(hook.token_blocked)
        self.assertEqual(editor.writes, 1)
        hook.user32.SendInput.assert_not_called()

    def test_boundary_resets_trial_without_changing_completed_word(self):
        hook, editor, decision, language = self.fixture()
        hook.early_done = True
        self.assertFalse(hook._correct(hook.context(), 0x20, ' '))
        self.assertFalse(hook.early_done)
        self.assertEqual(hook.keys, [])
        hook.user32.SendInput.assert_not_called()


class TrialWindowTests(unittest.TestCase):
    def test_injected_input_is_opt_in_and_keeps_trial_scope(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch('early_live_trial.LiveHook.__init__', return_value=None) as initialize:
                hook = TrialHook(None, 90, 100, lambda *args: None, accept_injected=enabled)
                self.assertEqual(initialize.call_args.kwargs['accept_injected'], enabled)
                self.assertEqual((hook.trial_top, hook.trial_field), (90, 100))

    def test_opening_withdrawn_trial_does_not_create_or_start_hook(self):
        import tkinter as tk
        import early_live_trial
        root = tk.Tk()
        root.withdraw()
        try:
            with patch('early_live_trial.tk.Tk', return_value=root), patch.object(root, 'mainloop'), patch('early_live_trial.TrialHook') as constructor:
                early_live_trial.main()
            constructor.assert_not_called()
            self.assertFalse(root.winfo_ismapped())
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
