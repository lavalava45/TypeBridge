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
from typebridge_app import FocusContext, LiveHook


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

    def test_dictionary_report_only_active_en_ar_and_real_package_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            from language_registry import LANGUAGE_BY_CODE, LanguageDescription
            base = Path(folder)
            pack = base / 'language_packs' / 'ar'
            catalog = dict(LANGUAGE_BY_CODE)
            catalog['ar'] = LanguageDescription(
                code='ar', name='Arabic', langid=0x0401,
                unicode_script='ARABIC', alphabet='ابت',
                dictionary_file='words.txt', confident_file='confident.txt',
                additions=(), alternatives=('en', 'ru'))
            engine = SimpleNamespace(
                active_languages=('en', 'ar'), language_catalog=catalog,
                words={'en': {'private'}, 'ar': {'secret'}},
                confident={'en': {'private'}, 'ar': {'secret'}})
            lines = dictionary_report(engine, base / 'data',
                                      pack_root=base / 'language_packs')
            report = '\n'.join(lines)
            self.assertEqual(len(lines), 3)
            self.assertIn('Словарь EN:', report)
            self.assertIn('Словарь AR:', report)
            self.assertNotIn('Словарь RU:', report)
            self.assertNotIn('Словарь HE:', report)
            # The report normalizes pack_root with Path.resolve(). On Windows
            # CI the temporary directory can be supplied as an 8.3 short path
            # (RUNNER~1), while resolve() expands it (runneradmin). Compare
            # canonical paths, preserving the check of the actual pack files.
            canonical_pack = (base / 'language_packs').resolve() / 'ar'
            self.assertIn(str(canonical_pack / 'words.txt'), report)
            self.assertIn(str(canonical_pack / 'confident.txt'), report)
            self.assertNotIn('private', report)
            self.assertNotIn('secret', report)

    def test_dictionary_report_subset_builtins_does_not_require_other_keys(self):
        engine = SimpleNamespace(active_languages=('en',),
                                 words={'en': {'hello'}}, confident={'en': {'hello'}})
        with tempfile.TemporaryDirectory() as folder:
            lines = dictionary_report(engine, folder)
        self.assertEqual(len(lines), 2)
        self.assertIn('EN:', lines[1])

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
        # TrialHook bypasses boundary correction in its own _correct override.
        # Use LiveHook's production boundary method with a verified native
        # process identity, retaining the actual uncertain-write quarantine.
        hook.context = lambda: FocusContext(90, 100, 1, language[0],
                                            'notepad.exe', 'RichEdit50W', 100)
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
            # Modern quarantine is scoped to the uncertain editor field. A
            # global flag is neither necessary nor desirable for this case.
            self.assertTrue(hook._native_blocked(hook.context()))
            self.assertFalse(getattr(hook, 'native_edit_disabled', False))
            hook.token_blocked = False  # A later delimiter no longer permits retries.
            hook.keys = [(65, False)] * len(decision.typed)
            self.assertFalse(LiveHook._correct(hook, hook.context(), 0x20, ''))
            self.assertFalse(LiveHook._try_early(hook, hook.context()))
            LiveHook._cycle_word(hook, hook.context())
            factory.assert_called_once()
        hook.user32.SendInput.assert_not_called()
        self.assertEqual(language[0], 'en')

    def test_uncertain_write_quarantine_does_not_block_unrelated_field(self):
        hook, editor, decision, language = fixtures.TrialDeliveryTests().fixture()
        hook.context = lambda: FocusContext(90, 100, 1, language[0],
                                            'notepad.exe', 'RichEdit50W', 100)
        hook._quarantine_native('uncertain-native-write', hook.context())
        self.assertTrue(hook._native_blocked(hook.context()))
        other = FocusContext(90, 101, 1, language[0], 'notepad.exe', 'RichEdit50W', 101)
        self.assertFalse(hook._native_blocked(other))


if __name__ == '__main__':
    unittest.main()
