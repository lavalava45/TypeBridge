"""Regression checks for performance fixes; no physical hooks, UI or input."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from incremental_recognition import (prepare_early, cached_early_index,
                                    build_early_index, _prefix_matches)
from typebridge_app import LiveHook, Settings
from user_lexicon import UserLexicon


class IndexReuseTests(unittest.TestCase):
    def fixture(self):
        lexicon = UserLexicon()
        engine = SimpleNamespace(words={'en': {'alpha', 'alpine', 'hello'},
                                        'ru': {'ёж', 'ёлка', 'привет'},
                                        'he': {'שלום', 'ספר'}}, user_lexicon=lexicon)
        prepare_early(engine)
        return engine, lexicon

    def test_visual_settings_exclusions_and_toggle_reuse_the_index(self):
        engine, lexicon = self.fixture()
        original = engine._early_prefix_index
        hook = LiveHook.__new__(LiveHook)
        hook.engine, hook.thread_id = engine, 0
        with patch('incremental_recognition.build_early_index', side_effect=AssertionError('full rebuild')):
            for settings in (replace(Settings(), early_correction=True),
                             replace(Settings(), early_correction=True, show_caret_flag=True),
                             replace(Settings(), early_correction=False)):
                hook.request_config(settings, lexicon)
            lexicon.add('exclusions', 'en', 'alpha')
            hook.request_config(replace(Settings(), early_correction=True), lexicon)
            self.assertIs(hook.pending_config[2], original)

    def test_overlay_has_the_same_sorted_deduplicated_prefixes_as_full_rebuild(self):
        engine, lexicon = self.fixture()
        for lang, word in (('ru', 'ёлочка'), ('en', 'alpha'), ('en', 'albatross'), ('he', 'שמש')):
            lexicon.add('words', lang, word)
        overlay = cached_early_index(engine, lexicon)
        full = build_early_index(engine, lexicon)
        for lang in full:
            for prefix in ('', 'a', 'al', 'alpha', 'ё', 'е', 'ש', 'ס', 'nothing'):
                for limit in (1, 2, 10):
                    self.assertEqual(_prefix_matches(overlay[lang], prefix, limit),
                                     _prefix_matches(full[lang], prefix, limit))

    def test_removal_same_size_edit_and_case_sensitive_words_invalidate_overlay_only(self):
        engine, lexicon = self.fixture()
        base = engine._early_base_index
        lexicon.add('words', 'en', 'bravo', sensitive=True)
        old = cached_early_index(engine, lexicon)
        lexicon.remove('words', 'en', 'bravo', sensitive=True)
        lexicon.add('words', 'en', 'charlie', sensitive=True)
        new = cached_early_index(engine, lexicon)
        self.assertIs(engine._early_base_index, base)
        self.assertIsNot(new, old)
        self.assertEqual(_prefix_matches(old['en'], 'brav'), ('bravo',))
        self.assertEqual(_prefix_matches(new['en'], 'brav'), ())
        self.assertEqual(_prefix_matches(new['en'], 'char'), ('charlie',))


class DirectLayoutWorkerTests(unittest.TestCase):
    def fixture(self):
        from test_case_direct_hotkeys import ShortcutRuntimeTests
        hook, service = ShortcutRuntimeTests().fixture('ru')
        hook._in_keyboard_callback = True
        hook._browser_field_revision = 0
        hook.thread_id = 123
        hook.user32.PostThreadMessageW.return_value = 1
        return hook, service

    def release(self, hook):
        serial = hook.user32.PostThreadMessageW.call_args.args[2]
        hook._release_keyboard_job(serial)
        hook.native_worker.join(1)
        self.assertFalse(hook.native_worker.is_alive())

    def test_direct_command_waits_for_callback_release_even_when_correction_paused(self):
        from test_app_controls import key
        for paused in (False, True):
            hook, service = self.fixture()
            hook.paused = paused
            with patch('typebridge_app.WindowsLayouts', return_value=service):
                self.assertEqual(key(hook, 0x70), 1)
                service.request.assert_not_called()
                self.release(hook)
            service.request.assert_called_once()
            hook.user32.SendInput.assert_not_called()
            self.assertEqual(hook.last_language, 'ru')

    def test_changed_focus_and_scheduler_failure_never_fall_back_to_inline_wait(self):
        from test_app_controls import key
        hook, service = self.fixture()
        with patch('typebridge_app.WindowsLayouts', return_value=service):
            key(hook, 0x70)
            old = hook.context()
            hook.context = lambda: replace(old, focus=old.focus+1)
            self.release(hook)
        service.request.assert_not_called()
        hook, service = self.fixture()
        hook.user32.PostThreadMessageW.return_value = 0
        with patch('typebridge_app.WindowsLayouts', return_value=service):
            key(hook, 0x70)
        service.request.assert_not_called()
        hook.user32.SendInput.assert_not_called()


class DiagnosticQueueTests(unittest.TestCase):
    def test_gui_yields_before_draining_backlog_and_keeps_control_events(self):
        import queue
        from typebridge_app import TypeBridgeGui
        app = TypeBridgeGui.__new__(TypeBridgeGui)
        app.events = queue.Queue()
        app.root, app._diagnostic_writer = Mock(), Mock()
        app.closing, app.worker = False, None
        app.journal_service = SimpleNamespace(generation=1, enabled=True)
        app.journal_status, app.tray = Mock(), Mock()
        for _ in range(1000):
            app.events.put(('word-trace', {'event': 'typed-character', 'source': 'x'}))
        app.events.put(('journal', (1, 'control-event')))
        with patch('typebridge_app.DEBUG_WORDS_ENABLED', True):
            app._drain()
            self.assertGreaterEqual(app.events.qsize(), 901)
            self.assertEqual(app.root.after.call_args.args[0], 1)
            for _ in range(20):
                if app.events.empty():
                    break
                app._drain()
        self.assertTrue(app.events.empty())
        self.assertEqual(app._diagnostic_writer.submit.call_count, 1000)
        app.journal_status.set.assert_called_once_with('control-event')

    def test_gui_time_budget_is_checked_between_events(self):
        import queue
        from typebridge_app import TypeBridgeGui
        app = TypeBridgeGui.__new__(TypeBridgeGui)
        app.events, app.root, app._diagnostic_writer = queue.Queue(), Mock(), Mock()
        app.closing, app.worker = False, None
        for _ in range(5):
            app.events.put(('word-trace', {}))
        with patch('typebridge_app.DEBUG_WORDS_ENABLED', True), patch('typebridge_app.perf_counter', side_effect=[0, 0, .004, .009]):
            app._drain()
        self.assertEqual(app.events.qsize(), 3)
        self.assertEqual(app._diagnostic_writer.submit.call_count, 2)

    def test_public_writer_rejects_text_even_with_word_flag(self):
        import tempfile
        from diagnostic_writer import DiagnosticWriter
        with tempfile.TemporaryDirectory() as folder:
            writer = DiagnosticWriter(folder, debug=True, words=True, diagnostics=False)
            self.assertFalse(writer.submit('word', {'event': 'typed-character', 'source': 'private'}))
            self.assertIsNone(writer.worker)
            self.assertTrue(writer.finished.is_set())

    def test_slow_writer_has_bounded_queue_reports_drops_and_flushes_on_close(self):
        import tempfile, threading
        from pathlib import Path
        from diagnostic_writer import DiagnosticWriter
        with tempfile.TemporaryDirectory() as folder:
            writer = DiagnosticWriter(folder, capacity=2)
            entered, release = threading.Event(), threading.Event()
            original = writer._write_batch
            def slow(batch):
                entered.set()
                if not release.wait(2):
                    raise AssertionError('test timeout')
                original(batch)
            writer._write_batch = slow
            try:
                self.assertTrue(writer.submit('runtime', 'start'))
                self.assertTrue(entered.wait(1))
                self.assertTrue(writer.submit('runtime', 'first'))
                self.assertTrue(writer.submit('runtime', 'second'))
                self.assertFalse(writer.submit('runtime', 'dropped-payload'))
                self.assertEqual(writer.jobs.qsize(), 2)
                writer.close()
                self.assertFalse(writer.finished.is_set())
                self.assertFalse(writer.submit('runtime', 'after-close'))
            finally:
                release.set()
                writer.worker.join(2)
            self.assertTrue(writer.finished.is_set())
            text = (Path(folder)/'replacement-diagnostics.log').read_text(encoding='utf-8')
            for token in ('start', 'first', 'second', 'diagnostic-queue-dropped:1'):
                self.assertIn(token, text)
            self.assertNotIn('dropped-payload', text)
            self.assertNotIn('after-close', text)

    def test_batch_opens_each_file_once_and_sanitizes_word_fields(self):
        import tempfile
        from pathlib import Path
        from diagnostic_writer import DiagnosticWriter
        with tempfile.TemporaryDirectory() as folder:
            writer = DiagnosticWriter(folder, debug=True, words=True, diagnostics=True)
            original = Path.open
            opened = []
            def tracked(path, *args, **kwargs):
                opened.append(path.name)
                return original(path, *args, **kwargs)
            with patch.object(Path, 'open', tracked):
                writer._write_batch([('word', {'event': 'typed-character', 'source': 'x', 'forbidden': 'hidden'}) for _ in range(100)])
            self.assertEqual(opened, ['debug-words.log'])
            text = (Path(folder)/'debug-words.log').read_text(encoding='utf-8')
            self.assertEqual(len(text.splitlines()), 100)
            self.assertNotIn('hidden', text)

    def test_gui_close_waits_asynchronously_for_writer_but_has_a_deadline(self):
        import queue
        from typebridge_app import TypeBridgeGui
        app = TypeBridgeGui.__new__(TypeBridgeGui)
        app.events, app.root, app._diagnostic_writer = queue.Queue(), Mock(), Mock()
        app.closing, app.worker = True, None
        app._diagnostic_writer.finished.is_set.return_value = False
        with patch('typebridge_app.monotonic', return_value=0):
            app._drain()
        app.root.destroy.assert_not_called()
        self.assertEqual(app.root.after.call_args.args[0], 20)
        with patch('typebridge_app.monotonic', return_value=3):
            app._drain()
        app.root.destroy.assert_called_once()


    def test_malformed_unicode_cannot_drop_an_entire_log_batch(self):
        import tempfile
        from pathlib import Path
        from diagnostic_writer import DiagnosticWriter
        with tempfile.TemporaryDirectory() as folder:
            writer = DiagnosticWriter(folder, debug=True, words=True, diagnostics=True)
            writer._write_batch([('word', {'event': 'typed-character', 'source': '\ud800'}),
                                 ('word', {'event': 'typed-character', 'source': 'ok'})])
            text = (Path(folder)/'debug-words.log').read_text(encoding='utf-8')
            self.assertEqual(len(text.splitlines()), 2)
            self.assertIn('ok', text)

    def test_worker_start_failure_does_not_break_the_gui_event_loop(self):
        import tempfile
        from diagnostic_writer import DiagnosticWriter
        with tempfile.TemporaryDirectory() as folder:
            writer = DiagnosticWriter(folder)
            with patch('diagnostic_writer.threading.Thread') as factory:
                factory.return_value.start.side_effect = RuntimeError('thread unavailable')
                self.assertFalse(writer.submit('runtime', 'synthetic-code'))
            self.assertTrue(writer.finished.is_set())
            self.assertEqual(writer.error, 'RuntimeError')
