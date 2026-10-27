"""Temporary opt-in word diagnostics: no hooks or physical typing."""
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from word_trace import safe_word_event, record_word_event
from test_early_native_edit import TrialDeliveryTests
from typebridge_app import TypeBridgeGui


class WordTraceTests(unittest.TestCase):
    def test_browsers_require_word_debug_opt_in(self):
        hook, _, _, _ = TrialDeliveryTests().fixture()
        chrome = replace(hook.context(), process_name='chrome.exe')
        hook.debug_words = False
        hook._trace_word('early-decision', chrome, source='apple')
        hook.emit.assert_not_called()
        hook.debug_words = True
        from word_trace import BROWSER_APPS
        for app in BROWSER_APPS:
            hook._trace_word('early-decision', replace(chrome, process_name=app), source='apple')
            self.assertEqual(hook.emit.call_args.args[1]['source'], 'apple')
            self.assertEqual(hook.emit.call_args.args[1]['app'], app)
        hook.emit.reset_mock()
        hook._trace_word('early-decision', replace(chrome, process_name='keepass.exe'), source='secret')
        hook.emit.assert_not_called()

    def test_blocked_browser_input_and_backspace_still_reach_word_trace(self):
        from test_app_controls import make_hook, key
        from typebridge_app import VK_BACK
        hook = make_hook()
        context = replace(hook.context(), process_name='chrome.exe')
        hook.context = lambda: context
        hook.debug_words = True
        hook.keys = []
        hook.token_blocked = True
        hook.user32.GetKeyState.return_value = 0
        hook.engine.mapper.char = lambda *_: 'a'
        self.assertEqual(key(hook, 0x41), 0)
        self.assertEqual(key(hook, VK_BACK), 0)
        events = [c.args[1] for c in hook.emit.call_args_list if c.args[0] == 'word-trace']
        self.assertEqual(events[0]['event'], 'typed-character')
        self.assertEqual(events[0]['source'], 'a')
        self.assertEqual(events[0]['reason'], 'token-blocked')
        self.assertEqual(events[1]['state'], 'backspace')
        hook.user32.SendInput.assert_not_called()

    def test_browser_keyboard_trace_never_maps_characters_without_opt_in(self):
        from test_app_controls import make_hook
        hook = make_hook()
        hook.debug_words = False
        context = replace(hook.context(), process_name='chrome.exe')
        with patch.object(hook.engine.mapper, 'char', create=True) as char:
            hook._trace_browser_key(context, 0x41, False)
        char.assert_not_called()
        hook.emit.assert_not_called()

    def test_generic_early_log_labels_submission_not_document_verification(self):
        from test_keyboard_early_transport import KeyboardEarlyTransportTests
        hook, decision = KeyboardEarlyTransportTests().fixture()
        hook.debug_words = True
        chrome = replace(hook.context(), process_name='chrome.exe')
        self.assertTrue(hook._try_keyboard_early(chrome, decision, lambda: True))
        payload = [c.args[1] for c in hook.emit.call_args_list if c.args[0] == 'word-trace'][-1]
        self.assertEqual(payload['event'], 'early-result')
        self.assertEqual(payload['source'], 'ghb')
        self.assertEqual(payload['replacement'], 'при')
        self.assertEqual(payload['state'], 'submitted-unverified')
        self.assertEqual(payload['sent'], payload['total'])
        self.assertEqual(payload['removed'], '2')
        self.assertNotIn('observed', payload)
        self.assertEqual(json.loads(safe_word_event(payload))['transport'], 'sendinput')

    def test_word_content_requires_explicit_hook_opt_in_and_test_editor(self):
        hook, _, _, _ = TrialDeliveryTests().fixture()
        hook.debug_words = False
        hook._trace_word('early-decision', hook.context(), source='private-input')
        self.assertFalse(any(call.args[0] == 'word-trace' for call in hook.emit.call_args_list))
        hook.debug_words = True
        hook._trace_word('early-decision', hook.context(), source='private-input')
        self.assertFalse(any(call.args[0] == 'word-trace' for call in hook.emit.call_args_list))
        notepad = replace(hook.context(), process_name='notepad.exe')
        hook._trace_word('early-decision', notepad, source='lbdthcbz',
                         replacement='диверсия')
        hook.emit.assert_called_with('word-trace', {
            'event': 'early-decision', 'app': 'notepad.exe', 'language': 'en',
            'source': 'lbdthcbz', 'replacement': 'диверсия'
        })

    def test_temporary_log_escapes_controls_and_restricts_fields(self):
        details = {'event': 'boundary-decision', 'source': 'lbdthcbz',
                   'replacement': 'диверсия', 'observed': 'диверсиия\nprivate\r',
                   'document': 'this whole document must never be logged'}
        value = safe_word_event(details)
        decoded = json.loads(value)
        self.assertEqual(decoded['replacement'], 'диверсия')
        self.assertEqual(decoded['observed'], 'диверсиия\nprivate\r')
        self.assertNotIn('document', decoded)
        with tempfile.TemporaryDirectory() as folder:
            record_word_event(folder, details)
            text = (Path(folder) / 'debug-words.log').read_text(encoding='utf-8')
            self.assertIn('диверсиия\\nprivate\\r', text)
            self.assertNotIn('whole document', text)
            self.assertEqual(len(text.splitlines()), 1)

    def test_invalid_events_do_not_create_word_log(self):
        with tempfile.TemporaryDirectory() as folder:
            record_word_event(folder, {'event': 'clipboard', 'source': 'secret'})
            self.assertFalse((Path(folder) / 'debug-words.log').exists())

    def test_post_early_readback_runs_after_hook_and_can_reveal_duplicate(self):
        hook, _, _, _ = TrialDeliveryTests().fixture()
        hook.debug_words, hook.early_done = True, True
        notepad = replace(hook.context(), process_name='notepad.exe')
        hook.context = lambda: notepad
        reader = MagicMock()
        reader.last_snapshot = ('приввет'.encode(), len('приввет'.encode()))
        with patch('typebridge_app.NativeEditBridge', return_value=reader) as factory:
            hook._trace_native_before_boundary(notepad)
            factory.assert_not_called()
            hook.emit.assert_called_with('deferred-native-read', (hook, notepad))
            hook._trace_native_after_boundary(notepad)  # Simulates GUI queue drain.
        factory.assert_called_once()
        reader.snapshot.assert_called_once()
        self.assertEqual(hook.emit.call_args.args[0], 'word-trace')
        payload = hook.emit.call_args.args[1]
        self.assertEqual(payload['event'], 'post-early-deferred')
        self.assertEqual(payload['state'], 'readback-after-boundary')
        self.assertEqual(payload['observed'], 'приввет')

    def test_deferred_read_skips_focus_changed_without_touching_document(self):
        hook, _, _, _ = TrialDeliveryTests().fixture()
        hook.debug_words, hook.early_done = True, True
        original = replace(hook.context(), process_name='notepad.exe')
        hook.context = lambda: None
        with patch('typebridge_app.NativeEditBridge') as factory:
            hook._trace_native_before_boundary(original)
            hook._trace_native_after_boundary(original)
        factory.assert_not_called()
        self.assertEqual(hook.emit.call_args.args[0], 'word-trace')
        self.assertEqual(hook.emit.call_args.args[1]['state'], 'read-skipped')

    def test_gui_drain_reads_in_gui_thread_only_for_active_debug_hook(self):
        hook, _, _, _ = TrialDeliveryTests().fixture()
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.hook = hook
        import queue
        gui.events = queue.Queue()
        gui.events.put(('deferred-native-read', (hook, hook.context())))
        gui.closing = True
        gui.worker = None
        gui.root = MagicMock()
        hook._trace_native_after_boundary = MagicMock()
        with patch('typebridge_app.DEBUG_WORDS_ENABLED', True):
            gui._drain()
        hook._trace_native_after_boundary.assert_called_once()

    def test_readback_is_not_attempted_without_word_flag(self):
        hook, _, _, _ = TrialDeliveryTests().fixture()
        hook.early_done, hook.debug_words = True, False
        notepad = replace(hook.context(), process_name='notepad.exe')
        with patch('typebridge_app.NativeEditBridge') as factory:
            hook._trace_native_before_boundary(notepad)
        factory.assert_not_called()
        self.assertFalse(any(c.args[0] == 'deferred-native-read'
                             for c in hook.emit.call_args_list))


class DiversiyaDictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from engine import LanguageEngine, source_data_dir
        cls.engine = LanguageEngine(source_data_dir(), conservative=True)

    def test_lbdthcbz_rare_checkbox_controls_boundary_and_early_policy(self):
        from incremental_recognition import prepare_early, suggest_live_early
        keys = [(ord(ch.upper()), False) for ch in 'lbdthcbz']
        engine = self.engine
        self.assertFalse(engine._confident('ru', 'диверсия'))
        engine.conservative = True
        self.assertIsNone(engine.decide_token(keys, 'en'))
        engine.conservative = False
        decision = engine.decide_token(keys, 'en')
        self.assertIsNotNone(decision)
        self.assertEqual((decision.typed, decision.replacement, decision.language),
                         ('lbdthcbz', 'диверсия', 'ru'))
        prepare_early(engine)
        self.assertIsNone(suggest_live_early(engine, keys[:6], 'en'))
        early = suggest_live_early(engine, keys, 'en')
        self.assertIsNotNone(early)
        self.assertEqual((early.typed, early.replacement, early.language),
                         ('lbdthcbz', 'диверсия', 'ru'))

