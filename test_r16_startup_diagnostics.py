"""Startup worker integration with external language (no real keyboard hook)."""

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

from language_pack_loader import available_language_catalog
from test_r16_language_packs import write_pack
from typebridge_app import Settings, TypeBridgeGui, load_settings, save_settings
from user_lexicon import UserLexicon


class SynchronousThread:
    """Run a GUI worker in the calling test thread, with no OS side effects."""

    def __init__(self, target, daemon=False):
        self.target = target

    def start(self):
        self.target()

    def is_alive(self):
        return False


class StartupDiagnosticsTests(unittest.TestCase):
    def test_gui_worker_starts_en_ar_through_dictionary_report_and_hook_boundary(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / 'data'
            data.mkdir()
            for filename, content in {
                'en.txt': 'hello\n', 'confident_en.txt': 'hello\n',
                'exclude.txt': '',
            }.items():
                (data / filename).write_text(content, encoding='utf-8')
            packs = base / 'language_packs'
            packs.mkdir()
            write_pack(packs, 'ar', word='مرحبا\n')
            catalog = available_language_catalog(packs)
            profile = base / 'profile'
            profile.mkdir()
            settings_file = profile / 'settings.json'
            save_settings(Settings(active_languages=('en', 'ar')),
                          settings_file, catalog=catalog)
            settings = load_settings(settings_file, catalog=catalog)
            lexicon = UserLexicon(catalog=catalog)

            mapper = Mock()
            mapper.layouts = {'en': 0x04090409, 'ar': 0x04010401}
            messages = []
            hooked = []

            def fake_hook(engine, current, notify, *, snippets,
                          password_guard=None):
                # A genuine LanguageEngine and verified lexical data made it
                # through startup; this is the ONLY hook boundary we mock.
                self.assertIsNone(password_guard)  # Default-off R04.
                hooked.append((engine, current))
                self.assertEqual(current.active_languages, ('en', 'ar'))
                self.assertEqual(set(engine.words), {'en', 'ar'})
                self.assertIn('مرحبا', engine.confident['ar'])
                result = Mock()
                result.run = Mock()
                return result

            gui = TypeBridgeGui.__new__(TypeBridgeGui)
            gui.closing = False
            gui.corrections_enabled = Mock()
            gui.worker = None
            gui.hook = None
            gui.run_state = 'stopped'
            gui.launch_languages = ('en', 'ar')
            gui.save = Mock(return_value=settings)
            gui._sync_tray_state = Mock()
            gui.cancel_start = threading.Event()
            gui.lexicon = lexicon
            gui.snippet_config = Mock()
            gui._runtime_config_lock = threading.RLock()
            gui._desired_runtime_config = (settings, lexicon)
            gui.status = Mock()
            gui.emit = lambda kind, message: messages.append((kind, message))

            with (patch('typebridge_app.threading.Thread', SynchronousThread),
                  patch('typebridge_app.DEBUG_ENABLED', False),
                  patch('typebridge_app.USER_DIR', profile),
                  patch('typebridge_app.LANGUAGE_PACK_DIR', packs),
                  patch('typebridge_app.source_data_dir', return_value=data),
                  patch('typebridge_app.require_no_original_switcher'),
                  patch('typebridge_app.exclusive_live_hook', return_value=nullcontext()),
                  patch('engine.LayoutMapper', return_value=mapper),
                  patch('typebridge_app.LiveHook', side_effect=fake_hook)):
                gui.start()

            self.assertEqual(len(hooked), 1)
            self.assertFalse(any(kind == 'error' for kind, _ in messages), messages)
            report = '\n'.join(message for kind, message in messages if kind == 'info')
            self.assertIn('Словарь EN:', report)
            self.assertIn('Словарь AR:', report)
            self.assertNotIn('Словарь RU:', report)
            self.assertNotIn('Словарь HE:', report)
            self.assertIn(str(packs / 'ar' / 'words.txt'), report)
            self.assertIn('AR=04010401', report)
            self.assertIsNone(gui.hook)


if __name__ == '__main__':
    unittest.main()
