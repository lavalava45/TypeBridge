"""Off-hook, opt-in dictionary explanations; model only, no physical hooks."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipeline_probe import DeferredDictionaryDiagnostics
from test_app_controls import make_hook


KEYS = ((65, False), (66, False), (67, False), (68, False))


class DeferredDiagnosticsTests(unittest.TestCase):
    def test_classifies_on_independent_thread_with_only_safe_reason(self):
        done = threading.Event()
        observed = []
        engine = SimpleNamespace(
            words={'en': set(), 'ru': set()},
            mapper=SimpleNamespace(word=lambda keys, lang: 'pwrd'),
            _known=lambda *_: False, _excluded=lambda *_: False,
            conservative=True)

        def result(reason, language, length, manual):
            observed.append((reason, language, length, manual,
                             threading.current_thread().name))
            done.set()

        worker = DeferredDictionaryDiagnostics(engine, result)
        try:
            self.assertTrue(worker.submit(KEYS, 'en'))
            self.assertTrue(done.wait(2), 'worker must consume the queued test job')
            self.assertEqual(observed, [
                ('no-target-dictionary-match', 'en', 4, False,
                 'TypeBridgeDictionaryDiagnostic')])
            self.assertNotIn('pwrd', str(observed))
        finally:
            worker.close()

    def test_untrusted_classifier_result_is_not_logged(self):
        done = threading.Event()
        received = []
        with patch('pipeline_probe.decision_failure', return_value='secret-user-text'):
            worker = DeferredDictionaryDiagnostics(object(),
                lambda *args: (received.append(args), done.set()))
            try:
                self.assertTrue(worker.submit(KEYS, 'en', allow_rare=True))
                self.assertTrue(done.wait(2))
                self.assertEqual(received[0][0], 'diagnostic-classifier-unexpected')
                self.assertTrue(received[0][3])
                self.assertNotIn('secret', str(received))
            finally:
                worker.close()

    def test_bounded_queue_never_waits_for_busy_classifier(self):
        started = threading.Event()
        release = threading.Event()

        def blocked(*_args, **_kwargs):
            started.set()
            release.wait(2)
            return 'no-target-dictionary-match'

        with patch('pipeline_probe.decision_failure', side_effect=blocked):
            worker = DeferredDictionaryDiagnostics(object(), lambda *_: None, capacity=1)
            try:
                self.assertTrue(worker.submit(KEYS, 'en'))
                self.assertTrue(started.wait(2))
                self.assertTrue(worker.submit(KEYS, 'en'))
                self.assertFalse(worker.submit(KEYS, 'en'))
            finally:
                release.set()
                worker.close()

    def test_rejects_invalid_input_and_does_not_accept_after_close(self):
        worker = DeferredDictionaryDiagnostics(object(), lambda *_: None)
        try:
            self.assertFalse(worker.submit((('plain text', False),), 'en'))
            self.assertFalse(worker.submit(KEYS * 11, 'en'))
        finally:
            worker.close()
        self.assertFalse(worker.submit(KEYS, 'en'))

    def _hook(self, *, diagnostic=None):
        hook = make_hook()
        hook.pipeline_debug = True
        hook.engine.decide_token = Mock(return_value=None)
        hook.engine.mapper.word = lambda keys, lang: 'x' * len(keys)
        hook._physical_keys_form_word = Mock(return_value=True)
        hook._remember = Mock()
        hook._decision_diagnostics = diagnostic
        return hook

    def test_normal_debug_skips_expensive_classifier_in_hook(self):
        hook = self._hook()
        with patch('pipeline_probe.decision_failure',
                   side_effect=AssertionError('must not classify in hook')):
            self.assertFalse(hook._correct(hook.context(), None, '!'))
        probes = [call.args[1] for call in hook.emit.call_args_list
                  if call.args[0] == 'probe']
        self.assertTrue(any('decision-none:reason=policy-not-classified'
                            in event for event in probes))

    def test_opt_in_enqueues_snapshot_instead_of_classifying_in_hook(self):
        diagnostic = Mock()
        diagnostic.submit.return_value = True
        hook = self._hook(diagnostic=diagnostic)
        original = tuple(hook.keys)
        with patch('pipeline_probe.decision_failure',
                   side_effect=AssertionError('must not classify in hook')):
            self.assertFalse(hook._correct(hook.context(), None, '!'))
        diagnostic.submit.assert_called_once_with(
            list(original), 'en', allow_rare=False)
        probes = [call.args[1] for call in hook.emit.call_args_list
                  if call.args[0] == 'probe']
        self.assertTrue(any('decision-none:reason=classification-queued'
                            in event for event in probes))


if __name__ == '__main__':
    unittest.main()
