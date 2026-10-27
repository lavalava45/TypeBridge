"""Debug trace is useful on a failing PC but never contains user keystrokes."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from dictionary_diagnostics import record_debug_event
from pipeline_probe import decision_failure, emit_pipeline


class FixedMapper:
    def __init__(self, variants):
        self.variants = variants

    def word(self, keys, language):
        return self.variants[language]


class PipelineProbeTests(unittest.TestCase):
    def test_disabled_probe_is_silent(self):
        got = []
        hook = SimpleNamespace(emit=lambda *args: got.append(args), pipeline_debug=False)
        emit_pipeline(hook, 'hook-keydown')
        self.assertEqual(got, [])

    def test_probe_never_includes_untrusted_text(self):
        got = []
        hook = SimpleNamespace(emit=lambda *args: got.append(args), pipeline_debug=True)
        emit_pipeline(hook, 'context-ready', app='notepad++.exe', language='en',
                      title='Secret document', text='typed-password', hkl='F0010409')
        self.assertEqual(got, [('probe',
            'pipeline:context-ready:app=notepad++.exe:language=en:hkl=F0010409')])
        emit_pipeline(hook, 'decision-none', reason='private information here')
        self.assertEqual(got[1], ('probe', 'pipeline:decision-none:reason=other'))

    def test_probe_rate_limit_only_suppresses_matching_reason(self):
        got = []
        hook = SimpleNamespace(emit=lambda *args: got.append(args), pipeline_debug=True)
        emit_pipeline(hook, 'context-rejected', reason='invalid-focus', interval=100)
        emit_pipeline(hook, 'context-rejected', reason='invalid-focus', interval=100)
        emit_pipeline(hook, 'context-rejected', reason='unrecognized-hkl', interval=100)
        self.assertEqual(len(got), 2)

    def test_journal_accepts_only_structured_probe_not_user_text(self):
        with tempfile.TemporaryDirectory() as folder:
            record_debug_event(folder, 'probe', 'pipeline:decision-none:reason=rare-word-policy')
            record_debug_event(folder, 'probe', 'pipeline:decision-none:reason=private password')
            record_debug_event(folder, 'probe', 'pipeline:arbitrary-event:reason=valid')
            record_debug_event(folder, 'probe', 'pipeline:hook-keydown:text=sensitive')
            body = (Path(folder) / 'debug.log').read_text(encoding='utf-8')
            self.assertEqual(body.count('pipeline:'), 1)
            self.assertIn('rare-word-policy', body)
            self.assertNotIn('sensitive', body)

    def test_no_match_or_rare_policy_is_explained_without_word(self):
        mapping = FixedMapper({'en': 'vtypehrf', 'ru': 'мензурка', 'he': 'אאאאאאאא'})
        engine = SimpleNamespace(mapper=mapping,
            words={'en': set(), 'ru': {'мензурка'}, 'he': set()},
            conservative=True,
            _known=lambda lang, word: lang == 'ru' and word == 'мензурка',
            _excluded=lambda lang, word: False,
            _confident=lambda lang, word: False)
        keys = [(65, False)] * 8
        self.assertEqual(decision_failure(engine, keys, 'en'), 'rare-word-policy')
        self.assertEqual(decision_failure(engine, keys, 'en', allow_rare=True),
                         'mapping-or-punctuation-policy')
        engine._known = lambda *_args: False
        self.assertEqual(decision_failure(engine, keys, 'en'), 'no-target-dictionary-match')

    def test_real_correction_path_emits_decision_transport_and_hkl_stages(self):
        # Existing mock keyboard editor: no global hook, physical input or focus.
        from test_app_controls import make_hook
        hook = make_hook()
        hook.pipeline_debug = True
        self.assertTrue(hook._correct(hook.context(), 0x20, ''))
        lines = [call.args[1] for call in hook.emit.call_args_list
                 if call.args[0] == 'probe']
        self.assertTrue(any(line.startswith('pipeline:delimiter:') for line in lines))
        self.assertTrue(any(line.startswith('pipeline:decision-found:') for line in lines))
        self.assertTrue(any(line.startswith('pipeline:transport:') for line in lines))
        self.assertTrue(any(line.startswith('pipeline:layout-result:') for line in lines))
        self.assertTrue(all('akuo' not in line and 'שלום' not in line for line in lines))


if __name__ == '__main__':
    unittest.main()
