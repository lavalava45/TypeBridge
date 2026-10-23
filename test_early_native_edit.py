"""Range proof and state-machine regressions, without OS hooks or live input."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from early_native_edit import NativeEditBridge, utf16_position, utf8_position
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
    def test_native_path_is_disabled_by_default_without_reading_other_fields(self):
        hook, editor, decision, language = self.fixture()
        hook.settings = replace(hook.settings, early_native=False)
        with patch('typebridge_app.NativeEditBridge') as factory:
            self.assertFalse(hook._try_early(hook.context()))
        factory.assert_not_called()
        hook.user32.GetClassNameW.assert_not_called()

    def test_master_switch_disables_native_path(self):
        hook, editor, decision, language = self.fixture()
        hook.settings = replace(hook.settings, early_correction=False)
        with patch('typebridge_app.NativeEditBridge') as factory:
            self.assertFalse(hook._try_early(hook.context()))
        factory.assert_not_called()

    def test_unknown_field_uses_keyboard_transport_not_native_messages(self):
        hook, editor, decision, language = self.fixture()
        hook.user32.GetClassNameW.side_effect = lambda hwnd, buffer, size: setattr(buffer, 'value', 'Chrome_RenderWidgetHostHWND')
        with patch('typebridge_app.NativeEditBridge') as factory, patch('typebridge_app.suggest_live_early', return_value=decision):
            self.assertTrue(hook._try_early(hook.context()))
        factory.assert_not_called()
        self.assertFalse(getattr(hook, 'early_disabled', False))
        hook.user32.SendInput.assert_called_once()

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
        hook.settings = replace(hook.settings, early_correction=True, early_native=True)
        def class_name(hwnd, buffer, size):
            buffer.value = 'RichEdit50W'
            return len(buffer.value)
        hook.user32.GetClassNameW.side_effect = class_name
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
            with patch('typebridge_app.NativeEditBridge', return_value=editor), patch('typebridge_app.suggest_live_early', return_value=decision):
                self.assertTrue(hook._try_early(hook.context()))
            self.assertEqual(editor.data, result.encode())
            self.assertEqual(language[0], target)
            self.assertTrue(hook.early_done)
            hook.user32.SendInput.assert_not_called()

    def test_refusal_does_not_consume_held_key_or_change_layout(self):
        hook, editor, decision, language = self.fixture()
        editor.data = b'prefix-not-delivered'
        with patch('typebridge_app.NativeEditBridge', return_value=editor), patch('typebridge_app.suggest_live_early', return_value=decision):
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
        with patch('typebridge_app.NativeEditBridge', return_value=editor) as factory, patch('typebridge_app.suggest_live_early', return_value=decision):
            self.assertTrue(hook._try_early(hook.context()))
            self.assertTrue(hook.native_early_disabled)
            self.assertFalse(hook._try_early(hook.context()))
            factory.assert_called_once()
        self.assertTrue(hook.token_blocked)
        self.assertEqual(editor.writes, 1)
        self.assertFalse(getattr(hook, 'early_disabled', False))
        hook.user32.SendInput.assert_not_called()

    def test_boundary_resets_trial_without_changing_completed_word(self):
        hook, editor, decision, language = self.fixture()
        hook.early_done = True
        self.assertFalse(hook._correct(hook.context(), 0x20, ' '))
        self.assertFalse(hook.early_done)
        self.assertEqual(hook.keys, [])
        hook.user32.SendInput.assert_not_called()


class NativeTypingTests(unittest.TestCase):
    def fixture(self):
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.pending_char = 'щ'
        bridge.prepared = (0, 3, 'שלום', 'флгщ'.encode(), 8)
        bridge.still_valid = lambda: True
        bridge.check_identity = Mock()
        bridge.check_deadline = Mock()
        bridge.selection = Mock(side_effect=[(3, 3), (0, 4)])
        bridge.snapshot = Mock(return_value=('флгщ'.encode(), 8))
        bridge.timeout = bridge.budget_exceeded = bridge.write_started = False
        bridge.msg = Mock()
        return bridge

    def test_hebrew_uses_native_typing_group_with_undo_boundaries(self):
        bridge = self.fixture()
        bridge.replace(0, 6, 'שלום'.encode())
        self.assertEqual([call.args for call in bridge.msg.call_args_list], [
            (0x102, ord('щ'), 1), (0x458,), (0xB1, 0, 4),
            *[(0x102, ord(char), 1) for char in 'שלום'], (0x458,)])
        self.assertTrue(bridge.write_started)

    def test_partial_character_timeout_is_uncertain_and_never_replayed(self):
        bridge = self.fixture()
        def send(command, *args):
            if command == 0x102 and args[0] == ord('ל'):
                bridge.timeout = True
                raise TimeoutError('native-message')
        bridge.msg.side_effect = send
        with self.assertRaises(TimeoutError):
            bridge.replace(0, 6, 'שלום'.encode())
        characters = [call.args[1] for call in bridge.msg.call_args_list if call.args[0] == 0x102]
        self.assertEqual(characters, [ord('щ'), ord('ש'), ord('ל')])
        self.assertTrue(bridge.write_started)


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
