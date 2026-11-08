"""Deterministic browser-hook fast-path contracts; no real hooks or desktop IO."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from typebridge_app import (FocusContext, LiveHook, Settings, VK_SPACE,
                            pause_toggle_binding)
from test_app_controls import key
from engine import Decision


BROWSER = FocusContext(90, 100, 1, 'en', 'chrome.exe', 'synthetic-test')


def browser_key_fixture():
    hook = LiveHook.__new__(LiveHook)
    hook.settings = Settings()
    mapper = SimpleNamespace(char=Mock(return_value='a'), layouts={'en': 0x0409})
    hook.engine = SimpleNamespace(mapper=mapper, active_languages=('en', 'ru', 'he'))
    hook.user32 = Mock()
    hook.user32.GetKeyState.return_value = 0
    hook.user32.CallNextHookEx.return_value = 0
    hook.context = lambda: BROWSER
    hook.pressed = lambda _key: False
    hook.keys = []
    hook.last_word = None
    hook.last_focus = 100
    hook.last_language = 'en'
    hook.native_queue = None
    hook.stopping = False
    hook.paused = False
    hook.accept_injected = False
    hook.keyboard_hook = None
    hook.suppressed_keyups = set()
    hook._direct_bindings = {}
    hook._binding_settings = hook.settings
    hook._pause_toggle_binding = pause_toggle_binding(hook.settings)
    hook._trace_browser_key = Mock()
    hook.snippets = None
    hook.pipeline_debug = False
    hook._try_early = Mock(return_value=False)
    hook._selection_cycle = None
    hook._selection_epoch = 0
    hook._browser_field_revision = 0
    hook._try_snippet = Mock(return_value=(False, False))
    return hook


class BrowserHotpathTests(unittest.TestCase):
    def test_first_three_early_keys_never_read_class_or_run_dictionary(self):
        hook = LiveHook.__new__(LiveHook)
        hook.settings = Settings(early_correction=True, early_native=True)
        hook.simple_mode = True
        hook.paused = False
        hook.early_done = False
        hook.engine = Mock()
        hook.user32 = Mock()
        with patch('typebridge_app.suggest_live_early', return_value=None) as choose:
            for count in (1, 2, 3):
                hook.keys = [(0x41, False)] * count
                hook.native_early_keyup_pending = ('stale',)
                self.assertFalse(hook._try_early(BROWSER, await_native_keyup=True))
                self.assertIsNone(hook.native_early_keyup_pending)
            choose.assert_not_called()
            hook.user32.GetClassNameW.assert_not_called()
            # A four-key confident match is still eligible for normal policy.
            hook.keys = [(0x41, False)] * 4
            self.assertFalse(hook._try_early(BROWSER, await_native_keyup=True))
            choose.assert_called_once_with(hook.engine, hook.keys, 'en')
            hook.user32.GetClassNameW.assert_not_called()

    def test_strict_mode_keeps_native_class_transport_decision_at_four_keys(self):
        hook = LiveHook.__new__(LiveHook)
        hook.settings = Settings(early_correction=True, early_native=True)
        hook.simple_mode = False
        hook.paused = False
        hook.early_done = False
        hook.keys = [(0x41, False)] * 4
        hook.user32 = Mock()
        hook.engine = Mock()
        hook._use_native_text_transport = Mock(return_value=False)
        with patch('typebridge_app.suggest_live_early', return_value=None) as choose:
            self.assertFalse(hook._try_early(BROWSER, await_native_keyup=True))
            hook.user32.GetClassNameW.assert_called_once()
            hook._use_native_text_transport.assert_called_once()
            choose.assert_called_once()

    def test_one_candidate_check_per_browser_letter_and_oem_key(self):
        for vk in (0x41, 0xBC):
            with self.subTest(vk=vk):
                hook = browser_key_fixture()
                with patch('typebridge_app.candidate_token_key', return_value=True) as candidate:
                    self.assertEqual(key(hook, vk), 0)
                    candidate.assert_called_once()
                    self.assertEqual(hook.keys, [(vk, False)])
                    hook._try_early.assert_called_once()

    def test_space_is_a_boundary_without_letter_classification(self):
        hook = browser_key_fixture()
        hook.keys = [(0x41, False)]
        hook._correct = Mock(return_value=False)
        with patch('typebridge_app.candidate_token_key') as candidate:
            self.assertEqual(key(hook, VK_SPACE), 0)
            candidate.assert_not_called()
            hook._correct.assert_called_once_with(BROWSER, VK_SPACE, '')

    def test_no_negative_diagnostic_lookup_without_debug(self):
        hook = LiveHook.__new__(LiveHook)
        hook.settings = Settings()
        hook.paused = False
        hook.early_done = False
        hook.pipeline_debug = False
        hook.token_blocked = False
        hook.keys = [(0x41, False)]
        hook.last_word = None
        hook.clear_token = Mock()
        hook._remember = Mock()
        hook._physical_keys_form_word = Mock(return_value=True)
        hook.engine = SimpleNamespace(
            decide_token=Mock(return_value=None),
            mapper=SimpleNamespace(word=Mock(return_value='a')))
        with patch('pipeline_probe.decision_failure', side_effect=RuntimeError('should not run')) as diagnostic:
            self.assertFalse(hook._correct(BROWSER, None, ''))
            diagnostic.assert_not_called()
            hook._remember.assert_called_once()

    def test_debug_mode_skips_negative_classifier_in_keyboard_hook(self):
        hook = LiveHook.__new__(LiveHook)
        hook.settings = Settings()
        hook.paused = False
        hook.early_done = False
        hook.pipeline_debug = True
        hook.token_blocked = False
        hook.keys = [(0x41, False)]
        hook.last_word = None
        hook.clear_token = Mock()
        hook._remember = Mock()
        hook._physical_keys_form_word = Mock(return_value=True)
        hook.engine = SimpleNamespace(
            decide_token=Mock(return_value=None),
            mapper=SimpleNamespace(word=Mock(return_value='a')))
        with patch('pipeline_probe.decision_failure', side_effect=RuntimeError('should not run')) as diagnostic, \
                patch('typebridge_app.emit_pipeline') as emitted:
            self.assertFalse(hook._correct(BROWSER, None, ''))
            diagnostic.assert_not_called()
            self.assertEqual(emitted.call_args.kwargs.get('reason'), 'policy-not-classified')

    def test_transport_latency_probes_are_metadata_only_and_debug_opt_in(self):
        hook = LiveHook.__new__(LiveHook)
        hook.settings = Settings()
        hook.engine = SimpleNamespace(mapper=SimpleNamespace(layouts={'he': 0x040D}))
        hook.user32 = Mock()
        hook.suppressed_keyups = set()
        hook._send_guarded_inputs = Mock(return_value=(2, 'complete'))
        hook._confirm_keyboard_trigger_acceptance = Mock()
        hook._mark_keyboard_trigger_mutation = Mock()
        hook._postwrite_permitted = Mock(return_value=True)
        hook._browser_field_changed_after_submission = Mock(return_value=False)
        hook._record_replay_layout = Mock(return_value=0x040D)
        hook._language_from_id = Mock(return_value='he')
        hook._remember = Mock()
        hook._trace_word = Mock()
        hook._queue_browser_readback = Mock()
        hook.emit = Mock()
        hook.last_language = 'en'
        hook._browser_field_revision = 0
        decision = Decision('akuo', 'שלום', 'he', 'unique dictionary match')
        for debug in (False, True):
            with self.subTest(debug=debug), \
                    patch('typebridge_app.correction_inputs', return_value=[1, 2]), \
                    patch('typebridge_app.emit_pipeline') as pipeline:
                hook.pipeline_debug = debug
                self.assertTrue(hook._correct_keyboard(
                    BROWSER, decision, [(0x41, False)] * 4,
                    VK_SPACE, '', False, ' ', False, False))
                transport = [call.kwargs for call in pipeline.call_args_list
                             if call.args[1] == 'transport' and call.kwargs.get('method') == 'sendinput']
                layout = [call.kwargs for call in pipeline.call_args_list
                          if call.args[1] == 'layout-result']
                self.assertEqual((len(transport), len(layout)), (1, 1))
                self.assertEqual('elapsed_ms' in transport[0], debug)
                self.assertEqual('elapsed_ms' in layout[0], debug)
                self.assertNotIn('akuo', str((transport, layout)))
                self.assertNotIn('שלום', str((transport, layout)))


if __name__ == '__main__':
    unittest.main()
