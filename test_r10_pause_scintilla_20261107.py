"""Real Pause dispatch/worker with a model Scintilla transport; no live input."""
import json
import unittest
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from experiments.layout_quality_20261106 import FrozenMapper, KEYMAP
import test_hook_cycle_offhook as worker_tests
from test_scintilla_selection_20261107 import ModelTransport
from test_early_runtime import ByteEditor
from early_scintilla import ScintillaEarlyBridge
from test_app_controls import key
from typebridge_app import VK_PAUSE, WM_KEYUP


class PauseSelectionTests(unittest.TestCase):
    def fixture(self):
        hook, ctx = worker_tests.GenericCycleOffHookTests.fixture(self)
        ctx = replace(ctx, process_name='notepad++.exe')
        hook.context = lambda: replace(ctx, language=hook._language_from_id(
            hook.user32.GetKeyboardLayout.return_value & 0xFFFF))
        hook.last_word.scope = hook._word_scope(ctx)
        hook.user32.GetClassNameW.side_effect = (
            lambda hwnd, buf, size: setattr(buf, 'value', 'Scintilla'))
        hook.engine.mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
        hook.engine.mapper.layouts = {'en': 0x0409, 'ru': 0x0419, 'he': 0x040D}
        hook.engine.active_languages = ('en', 'ru', 'he')
        hook._postwrite_permitted = Mock(return_value=True)
        return hook, ModelTransport()

    def press(self, hook):
        self.assertEqual(key(hook, VK_PAUSE), 1)
        worker_tests.GenericCycleOffHookTests.release(self, hook)
        key(hook, VK_PAUSE, WM_KEYUP)

    def test_pause_prioritizes_selection_waits_offhook_and_cycles(self):
        hook, transport = self.fixture()
        original = transport.data
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)) as factory:
            self.assertEqual(key(hook, VK_PAUSE), 1)
            factory.assert_not_called()
            self.assertEqual(transport.writes, 0)
            worker_tests.GenericCycleOffHookTests.release(self, hook)
            key(hook, VK_PAUSE, WM_KEYUP)
            self.assertEqual(hook._selection_cycle.snapshot.text, 'привет\r\n')
            for _ in range(2):
                self.press(hook)
        self.assertEqual(transport.data, original)
        self.assertEqual(transport.writes, 3)
        hook.user32.SendInput.assert_not_called()
        self.assertIsNone(hook.last_word)

    def test_manual_only_paused_mode(self):
        hook, transport = self.fixture()
        hook.settings = replace(hook.settings, corrections_enabled=False)
        hook.paused = True
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            self.press(hook)
        self.assertEqual(transport.writes, 1)

    def test_pause_keyup_arriving_before_worker_finishes_does_not_block_second_cycle(self):
        hook, transport = self.fixture()
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            key(hook, VK_PAUSE)
            key(hook, VK_PAUSE, WM_KEYUP)
            worker_tests.GenericCycleOffHookTests.release(self, hook)
            self.assertIsNone(getattr(hook, 'native_pending_recovery', None))
            self.assertIsNone(hook._keyboard_job_release)
            self.press(hook)
        self.assertEqual(transport.writes, 2)

    def test_empty_range_alone_falls_through_to_existing_word(self):
        hook, transport = self.fixture()
        transport.anchor = transport.caret
        hook._cycle_edit_word = Mock()
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            self.press(hook)
        hook._cycle_edit_word.assert_called_once()
        self.assertEqual(transport.writes, 0)

    def test_empty_scintilla_word_after_space_recovers_without_hook_memory(self):
        # A valid dictionary word is not a reason for Pause to refuse.
        # Track no physical keys at all: a native caret snapshot must recover
        # the exact RU source, trailing spaces and bounded word span.
        hook, range_transport = self.fixture()
        mapper = hook.engine.mapper
        keys = mapper.keys_for('правильно', 'ru')
        self.assertIsNotNone(keys)
        self.assertEqual(mapper.word(keys, 'ru'), 'правильно')
        hook.user32.GetKeyboardLayout.return_value = 0x0419
        hook.last_language = 'ru'
        hook.last_word = None
        hook.keys = []

        class Editor(ByteEditor):
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        text = 'PREFIX правильно  SUFFIX'
        caret = len('PREFIX правильно  '.encode('utf-8'))
        editor = Editor(text, caret)
        range_transport.doc = 456
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda _ctx: nullcontext(range_transport)), \
                patch('typebridge_app.ScintillaEarlyBridge', side_effect=lambda _ctx: editor), \
                patch('typebridge_app.read_caret_word') as uia:
            for index, language in enumerate(('he', 'en', 'ru'), 1):
                range_transport.data = editor.data
                range_transport.anchor = range_transport.caret = editor.caret
                self.press(hook)
                expected = 'PREFIX ' + mapper.word(keys, language) + '  SUFFIX'
                self.assertEqual(editor.data.decode('utf-8'), expected)
                self.assertEqual(editor.caret, len(('PREFIX ' + mapper.word(keys, language) + '  ').encode()))
                self.assertEqual(editor.writes, index)
                self.assertEqual(hook.last_word.language, language)
                self.assertEqual(hook.last_word.suffix, '  ')
        uia.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_missing_word_midcaret_does_not_edit_a_fragment(self):
        hook, range_transport = self.fixture()
        hook.user32.GetKeyboardLayout.return_value = 0x0419
        hook.last_language = 'ru'
        hook.last_word = None
        hook.keys = []

        class Editor(ByteEditor):
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        source = 'PREFIX правильно  SUFFIX'
        editor = Editor(source, len('PREFIX прав'.encode('utf-8')))
        range_transport.data = editor.data
        range_transport.anchor = range_transport.caret = editor.caret
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda _ctx: nullcontext(range_transport)), \
                patch('typebridge_app.ScintillaEarlyBridge', side_effect=lambda _ctx: editor), \
                patch('typebridge_app.read_caret_word') as uia:
            self.press(hook)
        self.assertEqual(editor.data.decode('utf-8'), source)
        self.assertEqual(editor.writes, 0)
        hook.user32.SendInput.assert_not_called()
        uia.assert_not_called()

    def test_worker_scintilla_budget_is_bounded_without_hook_latency_regression(self):
        bridge = ScintillaEarlyBridge.__new__(ScintillaEarlyBridge)
        bridge.started = 0
        bridge.write_started = False
        with patch('early_scintilla.perf_counter', return_value=0.12):
            bridge.off_hook = False
            with self.assertRaises(TimeoutError):
                bridge.check_deadline()
            bridge.off_hook = True
            bridge.check_deadline()
        with patch('early_scintilla.perf_counter', return_value=0.26):
            with self.assertRaises(TimeoutError):
                bridge.check_deadline()
        bridge.write_started = True
        bridge.budget_exceeded = False
        bridge.context = SimpleNamespace(focus=100)
        bridge.u = SimpleNamespace(SendMessageTimeoutW=Mock(return_value=1))
        with patch('early_scintilla.perf_counter', return_value=0.40):
            bridge.msg(2008)
        self.assertFalse(bridge.budget_exceeded)
        with patch('early_scintilla.perf_counter', return_value=0.46):
            with self.assertRaises(TimeoutError):
                bridge.msg(2008)
        self.assertTrue(bridge.budget_exceeded)

    def test_read_failure_never_uses_word_or_backspace(self):
        hook, transport = self.fixture()
        transport.read_range = Mock(side_effect=TimeoutError)
        hook._cycle_edit_word = Mock()
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            self.press(hook)
        hook._cycle_edit_word.assert_not_called()
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(transport.writes, 0)

    def test_partial_write_never_falls_back_and_closes_cycle(self):
        hook, transport = self.fixture()
        native_msg = transport.msg
        def lost_reply(command, *args):
            result = native_msg(command, *args)
            if command == 2194:
                raise TimeoutError
            return result
        transport.msg = lost_reply
        hook._cycle_edit_word = Mock()
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            self.press(hook)
        self.assertEqual(transport.writes, 1)
        hook._cycle_edit_word.assert_not_called()
        hook.user32.SendInput.assert_not_called()
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertIsNone(hook._selection_cycle)

    def test_selection_change_starts_fresh_cycle(self):
        hook, transport = self.fixture()
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            self.press(hook)
            old = hook._selection_cycle
            transport.caret -= 2  # Remove CRLF from the selected range.
            self.press(hook)
        self.assertIsNot(hook._selection_cycle, old)
        self.assertEqual(transport.data[-len(b' SUFFIX'):], b' SUFFIX')

    def test_navigation_and_clear_reset_selection_state(self):
        hook, transport = self.fixture()
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            self.press(hook)
        epoch = hook._selection_epoch if hasattr(hook, '_selection_epoch') else 0
        key(hook, 0x25)  # Left arrow, without sending anything to Windows.
        self.assertIsNone(hook._selection_cycle)
        self.assertGreater(hook._selection_epoch, epoch)
        hook._selection_cycle = object()
        hook.clear_token()
        self.assertIsNone(hook._selection_cycle)

    def test_failed_schedule_never_reads_editor(self):
        hook, transport = self.fixture()
        hook._schedule_keyboard = Mock(return_value=False)
        with patch('typebridge_app.ScintillaRangeTransport') as factory:
            hook._cycle_word(hook.context())
        factory.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_focus_change_before_worker_release_does_not_read_selection(self):
        hook, transport = self.fixture()
        with patch('typebridge_app.ScintillaRangeTransport') as factory:
            key(hook, VK_PAUSE)
            old_context = hook.context()
            hook.context = lambda: replace(old_context, focus=999)
            worker_tests.GenericCycleOffHookTests.release(self, hook)
        factory.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_document_swap_restarts_cycle_without_reusing_original_atoms(self):
        hook, transport = self.fixture()
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            self.press(hook)
            old = hook._selection_cycle
            transport.doc += 1
            self.press(hook)
        self.assertIsNot(old, hook._selection_cycle)
        self.assertNotEqual(old.snapshot.scope, hook._selection_cycle.snapshot.scope)

    def test_unconfirmed_layout_clears_cycle_without_repeating_text(self):
        hook, transport = self.fixture()
        hook.user32.SendMessageTimeoutW.side_effect = None
        hook.user32.SendMessageTimeoutW.return_value = 0
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            self.press(hook)
        self.assertEqual(transport.writes, 1)
        self.assertIsNone(hook._selection_cycle)
        hook.user32.SendInput.assert_not_called()

    def test_reset_during_prepare_cancels_before_write(self):
        hook, transport = self.fixture()
        prepare = transport.prepare_text
        def reset(text):
            prepare(text)
            hook.clear_token()
        transport.prepare_text = reset
        with patch('typebridge_app.ScintillaRangeTransport',
                   side_effect=lambda ctx: nullcontext(transport)):
            self.press(hook)
        self.assertEqual(transport.writes, 0)
        hook.user32.SendInput.assert_not_called()
        self.assertIsNone(hook._selection_cycle)


if __name__ == '__main__':
    unittest.main()
