"""Accepted text != confirmed HKL. Pure model, no real keystrokes/layout change."""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from engine import Decision
import test_p0_five_findings as five
import test_r04_default_text_target as r04
import test_early_native_edit as native_fixtures
from typebridge_app import VK_SPACE


class UnconfirmedHklTests(unittest.TestCase):
    def test_generic_boundary_does_not_remember_unconfirmed_language(self):
        hook = r04.R04DefaultTargetTests.hooked_context()
        r04.metadata(hook.password_guard)
        source = hook.context()
        hook._remember, hook._queue_browser_readback = Mock(), Mock()
        hook.password_guard.revalidate = Mock(return_value=True)
        hook.engine.mapper.layouts = {'ru': 0x0419}
        hook.user32.SendInput.side_effect = lambda count, *args: count
        # Win32 accepts the HKL request but the layout owner stays EN.
        hook.user32.SendMessageTimeoutW.return_value = 1
        self.assertTrue(hook._correct_keyboard(
            source, Decision('ghbdtn', 'привет', 'ru', 'unique dictionary match'),
            [(ord(c.upper()), False) for c in 'ghbdtn'],
            VK_SPACE, ' ', True, ' ', False, False))
        hook.user32.SendMessageTimeoutW.assert_called_once()
        hook._remember.assert_not_called()
        self.assertTrue(hook.token_blocked)
        self.assertTrue(hook.keyboard_early_disabled)

    def test_native_boundary_does_not_remember_unconfirmed_language(self):
        hook, source = five.FiveFindingsRegressionTests().browser_fixture()
        hook._remember, hook._queue_browser_readback = Mock(), Mock()
        hook.engine.mapper.layouts = {'ru': 0x0419}
        bridge = MagicMock(write_started=True, failure='', trace=())
        bridge.__enter__.return_value = bridge
        bridge.__exit__.return_value = False
        outcome = SimpleNamespace(state='verified', reason='confirmed')
        with (patch('typebridge_app.NativeEditBridge', return_value=bridge),
              patch('typebridge_app.replace_prefix', return_value=outcome)):
            hook._correct_native(
                source, Decision('ghbdtn', 'привет', 'ru', 'unique dictionary match'),
                [(0x47, False)], VK_SPACE, '', True, ' ', False, False)
        hook.user32.SendMessageTimeoutW.assert_called_once()
        hook._remember.assert_not_called()
        self.assertTrue(hook.token_blocked)

    def test_generic_manual_cycle_does_not_advance_remembered_word(self):
        hook, editor, language = native_fixtures.TrialDeliveryTests().native_cycle_fixture()
        source = hook.context()
        word = hook.last_word
        original_text, original_language = word.text, word.language
        # Model a generic text recipient, not addressable NativeEdit.
        hook.user32.GetClassNameW.side_effect = lambda hwnd, buf, n: setattr(
            buf, 'value', 'Chrome_RenderWidgetHostHWND')
        # The strict generic route now requires a matching UIA caret witness.
        # Model the documented simple-mode generic editor path to reach the
        # independent post-send HKL confirmation check safely.
        hook.simple_mode = True
        hook._send_guarded_inputs = lambda events, valid, **kwargs: (len(events), 'complete')
        hook._postwrite_permitted = Mock(return_value=True)
        hook.user32.SendMessageTimeoutW.side_effect = lambda *args: 1
        hook._record_replay_layout = lambda context, hkl: 0x0409
        hook._cycle_word(source)
        hook.user32.SendMessageTimeoutW.assert_called_once()
        hook._postwrite_permitted.assert_called_once()
        self.assertEqual((word.text, word.language), (original_text, original_language))
        self.assertTrue(hook.token_blocked)

    def test_strict_generic_without_caret_proof_never_sends_or_switches_hkl(self):
        hook, _, _ = native_fixtures.TrialDeliveryTests().native_cycle_fixture()
        source = hook.context()
        word = hook.last_word
        original = (word.text, word.language)
        hook.user32.GetClassNameW.side_effect = lambda hwnd, buf, n: setattr(
            buf, 'value', 'Chrome_RenderWidgetHostHWND')
        hook._send_guarded_inputs = Mock()
        with patch('typebridge_app.read_caret_word', return_value=None) as reader:
            hook._cycle_word(source)
        reader.assert_called_once()
        hook._send_guarded_inputs.assert_not_called()
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.assertEqual((word.text, word.language), original)
        self.assertTrue(hook.token_blocked)


if __name__ == '__main__':
    unittest.main()
