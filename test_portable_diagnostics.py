import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dictionary_diagnostics import dictionary_report, record_debug_event
from early_scintilla import failure_reason, replace_prefix
from test_early_runtime import ByteEditor
import test_early_native_edit as fixtures
from typebridge_app import LiveHook


class PortableDiagnosticsTests(unittest.TestCase):
    def engine(self):
        return SimpleNamespace(words={lang: {'private-word'} for lang in ('ru', 'en', 'he')},
                               confident={lang: set() for lang in ('ru', 'en', 'he')})

    def test_dictionary_report_shows_used_lexicons_without_frequency_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            text = '\n'.join(dictionary_report(self.engine(), folder))
            self.assertIn(str(Path(folder).resolve()), text)
            self.assertIn('загружено 1', text)
            self.assertIn('уверенных 0', text)
            self.assertNotIn('частот', text.lower())
            self.assertNotIn('frequency_', text)
            self.assertNotIn('private-word', text)

    def test_frequency_files_are_not_inspected_or_reported_on_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'FREQUENCY-SOURCES.json').write_text('[]')
            for lang in ('ru', 'en', 'he'):
                (Path(folder) / f'frequency_{lang}.tsv').write_text('not-a-frequency-index')
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('frequency file read')):
                report = '\n'.join(dictionary_report(self.engine(), folder))
            self.assertNotIn('frequency_', report)
            self.assertNotIn('частот', report.lower())
            self.assertNotIn('повреждён', report)

    def test_debug_journal_does_not_write_arbitrary_ui_or_user_text(self):
        with tempfile.TemporaryDirectory() as folder:
            record_debug_event(folder, 'error', 'private clipboard contents')
            self.assertFalse((Path(folder) / 'debug.log').exists())
            record_debug_event(folder, 'diagnostic', 'boundary-uncertain:write-not-confirmed')
            record_debug_event(folder, 'info', 'Защита паролей: provider-error')
            text = (Path(folder) / 'debug.log').read_text(encoding='utf-8')
            self.assertIn('write-not-confirmed', text)
            self.assertIn('provider-error', text)
            self.assertNotIn('private', text)

    def test_exception_details_are_limited_to_known_internal_codes(self):
        self.assertEqual(failure_reason(TimeoutError('native-edit-budget')), 'TimeoutError:native-edit-budget')
        self.assertEqual(failure_reason(ValueError('private document text')), 'ValueError')
        self.assertEqual(failure_reason(ValueError('snapshot-changed')), 'ValueError:snapshot-changed')

    def test_preflight_failure_metadata_does_not_include_document_content(self):
        editor = ByteEditor('private-document-content')
        editor.snapshot = lambda: (_ for _ in ()).throw(ValueError('snapshot-changed'))
        outcome = replace_prefix(editor, 'ghbdtn', 'привет', lambda: True)
        self.assertEqual(outcome.state, 'refused')
        self.assertEqual(editor.failure, 'ValueError:snapshot-changed')
        self.assertNotIn('private-document-content', editor.failure)

    def test_uncertain_boundary_disables_later_automatic_early_and_manual_edits(self):
        hook, editor, decision, language = fixtures.TrialDeliveryTests().fixture()
        editor.data = decision.typed.encode()
        editor.caret = len(editor.data)
        hook.engine.decide_token = lambda *args, **kwargs: decision
        original_replace = editor.replace
        def partial(*args):
            original_replace(*args)
            raise TimeoutError('native-edit-budget')
        editor.replace = partial
        with patch('typebridge_app.NativeEditBridge', return_value=editor) as factory:
            self.assertFalse(LiveHook._correct(hook, hook.context(), 0x20, ''))
            self.assertTrue(hook.native_edit_disabled)
            hook.token_blocked = False  # A later delimiter no longer permits retries.
            hook.keys = [(65, False)] * len(decision.typed)
            self.assertFalse(LiveHook._correct(hook, hook.context(), 0x20, ''))
            self.assertFalse(LiveHook._try_early(hook, hook.context()))
            LiveHook._cycle_word(hook, hook.context())
            factory.assert_called_once()
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(language[0], 'en')


if __name__ == '__main__':
    unittest.main()
