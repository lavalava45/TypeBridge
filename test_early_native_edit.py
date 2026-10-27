"""Range proof and state-machine regressions, without OS hooks or live input."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from early_native_edit import NativeEditBridge, utf16_position, utf8_position, supports_native_edit
from early_scintilla import replace_prefix
from test_early_runtime import ByteEditor
from test_app_controls import make_hook
from typebridge_app import FocusContext, LiveHook
from engine import Decision
from early_live_trial import TrialHook
from word_cycle import RecentWord


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
    def test_notepad_native_early_waits_for_physical_char_and_does_not_replay_it(self):
        from test_app_controls import key
        from typebridge_app import WM_KEYUP
        hook, editor, _, language = self.fixture(source='en', typed='ghbdt',
                                                 target='ru', result='приве')
        editor.data, editor.caret = b'ghbd', 4
        hook.keys = [(0x41, False)] * 4
        hook.last_language, hook.last_focus = 'en', 100
        hook.user32.GetKeyState.return_value = 0
        hook.token_blocked = False
        hook.engine.mapper.char = lambda vk, lang, shift: 't'
        decision = Decision('ghbdt', 'приве', 'ru', 'unique dictionary match')
        with patch('typebridge_app.candidate_token_key', return_value=True), \
                patch('typebridge_app.suggest_live_early', return_value=decision), \
                patch('typebridge_app.NativeEditBridge', return_value=editor) as factory:
            self.assertEqual(key(hook, 0x54), 0)  # Physical KEYDOWN is forwarded.
            self.assertEqual(editor.data, b'ghbd')
            factory.assert_not_called()
            # The real editor accepts the physical fifth character once.
            editor.data += b't'
            editor.caret += 1
            self.assertEqual(key(hook, 0x54, WM_KEYUP), 0)
        self.assertEqual(editor.data, 'приве'.encode())
        self.assertEqual(language[0], 'ru')
        self.assertTrue(hook.early_done)
        self.assertEqual(editor.writes, 1)
        self.assertEqual(factory.call_args.kwargs['pending_char'], '')
        hook.user32.SendInput.assert_not_called()
        self.assertNotIn(0x54, hook.suppressed_keyups)

    def test_native_keyup_early_refuses_when_source_not_delivered_yet(self):
        from test_app_controls import key
        from typebridge_app import WM_KEYUP
        hook, editor, _, language = self.fixture(source='en', typed='ghbdt',
                                                 target='ru', result='приве')
        editor.data, editor.caret = b'ghbd', 4
        hook.keys = [(0x41, False)] * 4
        hook.engine.mapper.char = lambda vk, lang, shift: 't'
        hook.user32.GetKeyState.return_value = 0
        hook.token_blocked = False
        decision = Decision('ghbdt', 'приве', 'ru', 'unique dictionary match')
        with patch('typebridge_app.candidate_token_key', return_value=True), \
                patch('typebridge_app.suggest_live_early', return_value=decision), \
                patch('typebridge_app.NativeEditBridge', return_value=editor):
            self.assertEqual(key(hook, 0x54), 0)
            self.assertEqual(key(hook, 0x54, WM_KEYUP), 0)
        self.assertEqual(editor.data, b'ghbd')
        self.assertEqual(editor.writes, 0)
        self.assertEqual(language[0], 'en')
        self.assertFalse(getattr(hook, 'early_done', False))
        hook.user32.SendInput.assert_not_called()

    def native_cycle_fixture(self, *, text='tree ', source='en', suffix=' '):
        hook, editor, _, language = self.fixture(source=source, typed='tree', target='he', result='ארקק')
        forms = {'en': 'tree', 'he': 'ארקק', 'ru': 'екуу'}
        keys = tuple((ord(letter.upper()), False) for letter in 'tree')
        hook.engine.mapper.word = lambda physical, lang: forms[lang]
        hook.context = lambda: FocusContext(90, 100, 1, language[0], 'notepad.exe', 'test', 100)
        editor.data = text.encode('utf-8')
        editor.caret = len(editor.data)
        hook.keys.clear()
        hook.last_word = RecentWord(keys, forms[source], source, suffix,
                                    hook._word_scope(hook.context()))
        return hook, editor, language

    def test_repeated_pause_in_notepad_verifies_each_native_word_before_replacing(self):
        hook, editor, language = self.native_cycle_fixture()
        with patch('typebridge_app.NativeEditBridge', return_value=editor) as factory:
            for expected, new_lang in (('ארקק ', 'he'), ('екуу ', 'ru'), ('tree ', 'en')):
                hook._cycle_word(hook.context())
                self.assertEqual(editor.data.decode('utf-8'), expected)
                self.assertEqual(language[0], new_lang)
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(editor.writes, 3)
        hook.user32.SendInput.assert_not_called()

    def test_pause_refuses_stale_notepad_buffer_instead_of_appending_variants(self):
        hook, editor, language = self.native_cycle_fixture(text='tree extra ')
        with patch('typebridge_app.NativeEditBridge', return_value=editor):
            hook._cycle_word(hook.context())
        self.assertEqual(editor.data.decode(), 'tree extra ')
        self.assertEqual(editor.writes, 0)
        self.assertEqual(language[0], 'en')
        hook.user32.SendInput.assert_not_called()

    def test_uncertain_native_pause_quarantines_following_cycle_presses(self):
        hook, editor, language = self.native_cycle_fixture()
        old_replace = editor.replace
        def uncertain(*args):
            old_replace(*args)
            raise TimeoutError('write-failed-after-mutation')
        editor.replace = uncertain
        with patch('typebridge_app.NativeEditBridge', return_value=editor) as factory:
            hook._cycle_word(hook.context())
            self.assertTrue(hook.native_edit_disabled)
            hook._cycle_word(hook.context())
        self.assertEqual(editor.writes, 1)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(language[0], 'en')
        hook.user32.SendInput.assert_not_called()

    def test_native_pause_key_repeat_only_applies_one_verified_cycle_per_press(self):
        from test_app_controls import key
        from typebridge_app import VK_PAUSE, WM_KEYUP
        hook, editor, language = self.native_cycle_fixture()
        hook.last_focus = hook.context().focus
        hook.last_language = language[0]
        with patch('typebridge_app.NativeEditBridge', return_value=editor):
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.assertEqual(editor.writes, 1)
            self.assertEqual(editor.data.decode(), 'ארקק ')
            self.assertEqual(key(hook, VK_PAUSE, WM_KEYUP), 1)
            self.assertEqual(key(hook, VK_PAUSE), 1)
            self.assertEqual(editor.writes, 2)
            self.assertEqual(editor.data.decode(), 'екуу ')
        hook.user32.SendInput.assert_not_called()

    def test_notepad_plus_plus_manual_cycle_uses_scintilla_not_unverified_input(self):
        hook, editor, language = self.native_cycle_fixture()
        hook.context = lambda: FocusContext(90, 100, 1, language[0],
                                            'notepad++.exe', 'test', 100)
        hook.last_word.scope = hook._word_scope(hook.context())
        hook.user32.GetClassNameW.side_effect = lambda hwnd, buf, cap: setattr(buf, 'value', 'Scintilla')
        with patch('typebridge_app.ScintillaEarlyBridge', return_value=editor) as adapter:
            hook._cycle_word(hook.context())
        adapter.assert_called_once()
        self.assertEqual(editor.data.decode(), 'ארקק ')
        hook.user32.SendInput.assert_not_called()

    def test_boundary_uses_native_word_replacement_and_forwards_space(self):
        hook, editor, decision, language = self.fixture()
        editor.data = decision.typed.encode()
        editor.caret = len(editor.data)
        hook.engine.decide_token = Mock(return_value=decision)
        with patch('typebridge_app.NativeEditBridge', return_value=editor) as factory:
            self.assertFalse(LiveHook._correct(hook, hook.context(), 0x20, ''))
        self.assertEqual(editor.data, 'привет'.encode())
        self.assertEqual(language[0], 'ru')
        self.assertEqual(factory.call_args.kwargs['pending_char'], '')
        hook.user32.SendInput.assert_not_called()

    def test_boundary_refusal_never_falls_back_to_blind_backspaces(self):
        hook, editor, decision, language = self.fixture()
        editor.data = b'ghb'
        editor.caret = len(editor.data)
        hook.engine.decide_token = Mock(return_value=decision)
        with patch('typebridge_app.NativeEditBridge', return_value=editor):
            self.assertFalse(LiveHook._correct(hook, hook.context(), 0x20, ''))
        self.assertEqual(editor.data, b'ghb')
        self.assertFalse(editor.write_started)
        self.assertEqual(language[0], 'en')
        hook.user32.SendInput.assert_not_called()
        hook.emit.assert_called_with('diagnostic', 'boundary-refused:prefix-not-delivered')

    def test_modern_richedit_uses_verified_replacement_without_backspace_batch(self):
        hook, editor, decision, language = self.fixture()
        hook.user32.GetClassNameW.side_effect = lambda hwnd, buffer, size: setattr(buffer, 'value', 'RichEditD2DPT')
        with patch('typebridge_app.NativeEditBridge', return_value=editor), patch('typebridge_app.suggest_live_early', return_value=decision):
            self.assertTrue(hook._try_early(hook.context()))
        self.assertEqual(editor.data, 'привет'.encode())
        self.assertEqual(language[0], 'ru')
        hook.user32.SendInput.assert_not_called()
        self.assertTrue(supports_native_edit('RichEditD2DPT'))
        self.assertTrue(supports_native_edit('RICHEDIT50W'))
        self.assertFalse(supports_native_edit('RichEdit20A'))
        self.assertFalse(supports_native_edit('Chrome_RenderWidgetHostHWND'))

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

    def test_rare_checkbox_routes_full_word_into_native_early_transport(self):
        from incremental_recognition import prepare_early
        source, typed, target, result = 'en', 'lbdthcbz', 'ru', 'диверсия'
        hook, editor, _, language = self.fixture(source, typed, target, result)
        forms = {'en': typed, 'ru': result, 'he': 'ךנגאירנז'}
        hook.engine.mapper.word = lambda keys, lang: forms[lang][:len(keys)]
        hook.engine.words = {'en': set(), 'ru': {result}, 'he': set()}
        hook.engine.confident = {'en': set(), 'ru': set(), 'he': set()}
        hook.engine.exclusions = set()
        hook.engine.conservative = False
        prepare_early(hook.engine)
        with patch('typebridge_app.NativeEditBridge', return_value=editor) as bridge:
            self.assertTrue(hook._try_early(hook.context()))
            bridge.assert_called_once()
        self.assertEqual(editor.data, result.encode())
        self.assertEqual(language[0], 'ru')
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

    def test_read_timeout_defers_only_this_word_then_allows_fresh_early_attempt(self):
        hook, editor, decision, language = self.fixture()
        snapshot = editor.snapshot
        editor.snapshot = Mock(side_effect=TimeoutError('read-only'))
        editor.timeout = True
        editor.failure = 'native:preflight:message-d:error-1460'
        with patch('typebridge_app.NativeEditBridge', return_value=editor) as factory, patch('typebridge_app.suggest_live_early', return_value=decision):
            self.assertFalse(hook._try_early(hook.context()))
            self.assertFalse(getattr(hook, 'native_early_disabled', False))
            self.assertTrue(hook.native_early_deferred)
            self.assertFalse(editor.write_started)
            self.assertFalse(hook._try_early(hook.context()))
            factory.assert_called_once()
            keys = hook.keys.copy()
            hook.clear_token()
            hook.keys = keys
            editor.snapshot = snapshot
            editor.timeout = False
            self.assertTrue(hook._try_early(hook.context()))
        self.assertEqual(editor.data, 'привет'.encode())
        self.assertEqual(language[0], 'ru')
        hook.user32.SendInput.assert_not_called()

    def test_snapshot_changed_defers_next_letter_without_disabling_next_word(self):
        hook, editor, decision, language = self.fixture()
        original = editor.snapshot
        editor.snapshot = Mock(side_effect=ValueError('snapshot-changed'))
        with patch('typebridge_app.NativeEditBridge', return_value=editor) as factory, \
                patch('typebridge_app.suggest_live_early', return_value=decision):
            self.assertFalse(hook._try_early(hook.context()))
            self.assertTrue(hook.native_early_deferred)
            self.assertFalse(getattr(hook, 'native_edit_disabled', False))
            self.assertFalse(hook._try_early(hook.context()))
            factory.assert_called_once()
            hook.clear_token()
            hook.keys = [(0x41, False)] * len(decision.typed)
            editor.snapshot = original
            self.assertTrue(hook._try_early(hook.context()))
        self.assertEqual(editor.data, 'привет'.encode())
        self.assertEqual(language[0], 'ru')

    def test_uncertain_early_write_blocks_boundary_and_manual_cycle_even_after_space(self):
        hook, editor, decision, language = self.fixture()
        original_replace = editor.replace
        def partial(*args):
            original_replace(*args)
            raise TimeoutError('native-edit-budget')
        editor.replace = partial
        hook.engine.decide_token = Mock(return_value=decision)
        with patch('typebridge_app.NativeEditBridge', return_value=editor) as factory, \
                patch('typebridge_app.suggest_live_early', return_value=decision):
            self.assertTrue(hook._try_early(hook.context()))
            self.assertTrue(hook.native_edit_disabled)
            hook.clear_token()
            hook.keys = [(0x41, False)] * len(decision.typed)
            self.assertFalse(hook._correct(hook.context(), 0x20, ''))
            hook.keys = [(0x41, False)] * len(decision.typed)
            self.assertFalse(hook._correct(hook.context(), None, '', manual=True))
            hook.last_word = SimpleNamespace(
                scope=hook._word_scope(hook.context()), text='ghbdtn', suffix='',
                next_variant=Mock(return_value=('ru', 'привет')))
            hook._cycle_word(hook.context())
            factory.assert_called_once()
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(editor.writes, 1)
        self.assertEqual(language[0], 'en')

    def test_enter_read_timeout_does_not_disable_native_early(self):
        hook, editor, decision, language = self.fixture()
        editor.__class__.__enter__ = Mock(side_effect=TimeoutError('preflight'))
        try:
            with patch('typebridge_app.NativeEditBridge', return_value=editor), patch('typebridge_app.suggest_live_early', return_value=decision):
                self.assertFalse(hook._try_early(hook.context()))
            self.assertFalse(getattr(hook, 'native_early_disabled', False))
            self.assertEqual(language[0], 'en')
        finally:
            del editor.__class__.__enter__

    def test_debug_steps_are_queued_without_typed_text(self):
        hook, editor, decision, language = self.fixture()
        hook.pipeline_debug = True
        editor.trace = ['native-step:phase=preflight:msg=d:ms=1.234:ok=1:error=0:write=0']
        hook._trace_native(editor)
        hook.emit.assert_called_with('native-trace', tuple(editor.trace))

    def test_boundary_resets_trial_without_changing_completed_word(self):
        hook, editor, decision, language = self.fixture()
        hook.early_done = True
        self.assertFalse(hook._correct(hook.context(), 0x20, ' '))
        self.assertFalse(hook.early_done)
        self.assertEqual(hook.keys, [])
        hook.user32.SendInput.assert_not_called()


class NativeTypingTests(unittest.TestCase):
    def test_complete_word_does_not_materialize_an_extra_held_character(self):
        bridge = self.fixture()
        bridge.pending_char = ''
        bridge.prepared = (0, 6, 'привет', b'ghbdtn', 6)
        bridge.selection = Mock(return_value=(0, 6))
        bridge.snapshot = Mock(return_value=(b'ghbdtn', 6))
        bridge.replace(0, 6, 'привет'.encode())
        self.assertEqual([call.args for call in bridge.msg.call_args_list], [
            (0x458,), (0xB1, 0, 6),
            *[(0x102, ord(char), 1) for char in 'привет'], (0x458,)])

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
        def send(command, *args):
            if command in (0x102, 0xB1):
                bridge.write_started = True
        bridge.msg = Mock(side_effect=send)
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
            if command in (0x102, 0xB1):
                bridge.write_started = True
            if command == 0x102 and args[0] == ord('ל'):
                bridge.timeout = True
                raise TimeoutError('native-message')
        bridge.msg.side_effect = send
        with self.assertRaises(TimeoutError):
            bridge.replace(0, 6, 'שלום'.encode())
        characters = [call.args[1] for call in bridge.msg.call_args_list if call.args[0] == 0x102]
        self.assertEqual(characters, [ord('щ'), ord('ש'), ord('ל')])
        self.assertTrue(bridge.write_started)

    def test_prepare_reuses_snapshot_but_protocol_rechecks_before_write(self):
        bridge = self.fixture()
        bridge.last_snapshot = ('флг'.encode(), 6)
        bridge.prepare(0, 6, 'שלום'.encode())
        bridge.snapshot.assert_not_called()
        self.assertEqual(bridge.prepared, (0, 3, 'שלום', 'флгщ'.encode(), 8))

    def test_boundary_read_failure_is_refused_before_any_mutation(self):
        bridge = self.fixture()
        bridge.pending_char = ''
        bridge.prepared = (0, 6, 'привет', b'ghbdtn', 6)
        bridge.snapshot.side_effect = TimeoutError('read-only')
        # Exercise the shared protocol with a real native replace method.
        snapshots = [(b'ghbdtn', 6), (b'ghbdtn', 6)]
        bridge.snapshot.side_effect = snapshots + [TimeoutError('read-only')]
        bridge.prepare = Mock()
        outcome = replace_prefix(bridge, 'ghbdtn', 'привет', lambda: True)
        self.assertEqual(outcome.state, 'refused')
        self.assertFalse(bridge.write_started)
        bridge.msg.assert_not_called()

    def test_selection_timeout_is_uncertain_even_before_text_write(self):
        bridge = self.fixture()
        bridge.pending_char = ''
        bridge.prepared = (0, 6, 'привет', b'ghbdtn', 6)
        bridge.snapshot.return_value = (b'ghbdtn', 6)
        bridge.prepare = Mock()
        def send(command, *args):
            if command == 0xB1:
                bridge.write_started = True
                raise TimeoutError('selection')
        bridge.msg.side_effect = send
        outcome = replace_prefix(bridge, 'ghbdtn', 'привет', lambda: True)
        self.assertEqual(outcome.state, 'uncertain')
        self.assertTrue(bridge.write_started)


class NativeTimingTests(unittest.TestCase):
    def test_long_replacement_refused_before_any_native_mutation(self):
        from early_native_edit import NATIVE_MAX_INLINE_CHARS
        from early_scintilla import replace_prefix
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.context = SimpleNamespace(focus=100)
        bridge.u = SimpleNamespace(SendMessageTimeoutW=Mock())
        bridge.pending_char = ''
        bridge.phase = 'preflight'
        bridge.failure = ''
        bridge.write_started = False
        bridge.last_snapshot = (b'source', 6)
        bridge.snapshot = Mock(return_value=(b'source', 6))
        result = replace_prefix(bridge, 'source', 'a' * (NATIVE_MAX_INLINE_CHARS + 1), lambda: True)
        self.assertEqual(result.state, 'refused')
        self.assertEqual(result.reason, 'ValueError')
        self.assertEqual(bridge.failure, 'native:prepare:callback-admission-too-large')
        self.assertFalse(bridge.write_started)
        bridge.u.SendMessageTimeoutW.assert_not_called()

    def test_short_replacement_admitted_by_callback_budget(self):
        from early_native_edit import NATIVE_MAX_INLINE_CHARS
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.pending_char, bridge.phase = '', 'preflight'
        bridge.last_snapshot = (b'source', 6)
        bridge.prepare(0, 6, ('a' * NATIVE_MAX_INLINE_CHARS).encode())
        self.assertEqual(bridge.prepared[2], 'a' * NATIVE_MAX_INLINE_CHARS)

    def test_native_messages_have_distinct_bounded_read_and_commit_timeouts(self):
        # The physical Notepad trace contained a WM_CHAR timing out at 10.155 ms
        # after a successfully delivered earlier character. Preflight should
        # remain short, but irreversible writes need a bounded scheduling margin.
        from early_native_edit import (NATIVE_READ_TIMEOUT_MS,
                                       NATIVE_MUTATION_TIMEOUT_MS)
        self.assertEqual(NATIVE_READ_TIMEOUT_MS, 10)
        self.assertEqual(NATIVE_MUTATION_TIMEOUT_MS, 35)
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.context = SimpleNamespace(focus=100)
        bridge.u = SimpleNamespace(SendMessageTimeoutW=Mock(return_value=1))
        bridge.check_deadline = Mock()
        bridge.trace, bridge.phase, bridge.write_started = [], 'preflight', False
        bridge.msg(0xB0)  # EM_GETSEL; must not increase read risk.
        bridge.phase = 'deliver-held'
        bridge.msg(0x102, ord('у'), 1)  # Potentially irreversible.
        bridge.phase = 'select-range'
        bridge.msg(0xB1, 0, 5)  # Selection is also state-changing.
        bridge.phase = 'verify-result'
        bridge.msg(0xD)  # Reads after mutation are still bounded as reads.
        calls = bridge.u.SendMessageTimeoutW.call_args_list
        self.assertEqual([call.args[5] for call in calls], [10, 35, 35, 10])
        self.assertTrue(bridge.write_started)

    def test_native_commit_timeout_remains_uncertain_never_retried(self):
        from early_native_edit import NATIVE_MUTATION_TIMEOUT_MS
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.context = SimpleNamespace(focus=100)
        bridge.u = SimpleNamespace(SendMessageTimeoutW=Mock(return_value=0))
        bridge.check_deadline = Mock()
        bridge.phase, bridge.write_started, bridge.trace = 'write-replacement', True, []
        with patch('early_native_edit.ctypes.get_last_error', return_value=1460):
            with self.assertRaises(TimeoutError):
                bridge.msg(0x102, ord('е'), 1)
        self.assertEqual(bridge.u.SendMessageTimeoutW.call_args[0][5],
                         NATIVE_MUTATION_TIMEOUT_MS)
        self.assertTrue(bridge.timeout)
        self.assertEqual(bridge.u.SendMessageTimeoutW.call_count, 1)
        self.assertIn('message-102:error-1460', bridge.failure)

    def test_budget_never_cuts_a_started_native_typing_group(self):
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.context = SimpleNamespace(focus=100)
        bridge.u = SimpleNamespace(SendMessageTimeoutW=Mock(return_value=1))
        bridge.started, bridge.phase = 0, 'write-replacement'
        bridge.write_started, bridge.timeout, bridge.budget_exceeded = True, False, False
        bridge.trace = []
        with patch('early_native_edit.perf_counter', return_value=.070):
            bridge.msg(0x102, ord('в'), 1)
            bridge.msg(0x458)
        self.assertEqual(bridge.u.SendMessageTimeoutW.call_count, 2)
        self.assertFalse(bridge.budget_exceeded)

    def test_deadline_before_write_message_does_not_mark_mutation(self):
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.u = SimpleNamespace(SendMessageTimeoutW=Mock())
        bridge.write_started, bridge.trace, bridge.phase = False, [], 'select-range'
        bridge.check_deadline = Mock(side_effect=TimeoutError('deadline-before-send'))
        with self.assertRaises(TimeoutError):
            bridge.msg(0xB1, 0, 3)
        self.assertFalse(bridge.write_started)
        bridge.u.SendMessageTimeoutW.assert_not_called()

    def test_budget_failure_identifies_phase_without_sending(self):
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.started, bridge.phase = 0, 'verify-source'
        with patch('early_native_edit.perf_counter', return_value=.046):
            with self.assertRaises(TimeoutError):
                bridge.check_deadline()
        self.assertEqual(bridge.failure, 'native:verify-source:budget-45ms')

    def test_message_failure_records_win32_error_and_phase(self):
        bridge = NativeEditBridge.__new__(NativeEditBridge)
        bridge.context = SimpleNamespace(focus=100)
        bridge.u = SimpleNamespace(SendMessageTimeoutW=Mock(return_value=0))
        bridge.check_deadline = Mock()
        bridge.trace, bridge.phase, bridge.write_started = [], 'verify-result', True
        with patch('early_native_edit.ctypes.get_last_error', return_value=1460):
            with self.assertRaises(TimeoutError):
                bridge.msg(0xD)
        self.assertEqual(bridge.failure, 'native:verify-result:message-d:error-1460')
        self.assertIn(':write=1', bridge.trace[0])
        self.assertIn(':error=1460', bridge.trace[0])


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
