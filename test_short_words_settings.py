"""Regressions without hooks or foreground input."""
import ctypes
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from engine import LanguageEngine, source_data_dir
from typebridge_app import LiveHook, Settings
from tray_layouts import WindowsLayouts, LayoutTarget


class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = LanguageEngine(source_data_dir())

    def keys(self, text):
        return [(ord(c.upper()), c.isupper()) for c in text]

    def test_russian_one_letter_words_on_word_boundary(self):
        for physical, expected in (("f", "а"), ("b", "и"), ("z", "я"),
                                   ("d", "в"), ("e", "у"), ("r", "к"),
                                   ("c", "с"), ("j", "о"), ("F", "А"), ("Z", "Я")):
            result = self.engine.decide_token(self.keys(physical), "en")
            self.assertIsNotNone(result)
            self.assertEqual((result.language, result.replacement), ("ru", expected))

    def test_english_one_letter_words_and_unknown_abbreviations_preserved(self):
        for physical in ("a", "i", "x", "o"):
            self.assertIsNone(self.engine.decide_token(self.keys(physical), "en"))

    def test_single_letter_ambiguity_is_not_hidden_by_confidence(self):
        with patch.dict(self.engine.words, {"he": self.engine.words["he"] | {"ז"}}):
            self.assertIsNone(self.engine.decide_token(self.keys("z"), "en"))

    def test_yo_variants_match_dictionary_without_rewriting_spelling(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            for name in ("ru", "en", "he", "he_custom", "confident_ru", "confident_en", "confident_he", "exclude"):
                (data / (name + ".txt")).write_text("ещё\nёлка\n" if name in ("ru", "confident_ru") else "", encoding="utf-8")
            engine = LanguageEngine(data)
            for physical, expected in (("tot", "еще"), ("tkrf", "елка")):
                result = engine.decide_token(self.keys(physical), "en")
                self.assertEqual(result.replacement, expected)
            self.assertTrue(engine._known("ru", "ЕЩЁ"))

    def test_real_english_tot_preserved_but_f8_allows_explicit_conversion(self):
        self.assertIsNone(self.engine.decide_token(self.keys("tot"), "en"))
        result = self.engine.decide_token(self.keys("tot"), "en", allow_rare=True)
        self.assertEqual((result.language, result.replacement), ("ru", "еще"))


class HostedFocusTests(unittest.TestCase):
    def fixture(self):
        hook = LiveHook.__new__(LiveHook)
        hook.user32 = Mock()
        hook.settings = Settings()
        hook.user32.GetForegroundWindow.return_value = 10
        def window_thread(hwnd, pid):
            pid._obj.value = 100 if hwnd == 10 else 200
            return 11 if hwnd == 10 else 22
        def gui_info(thread, info):
            info._obj.hwndFocus = 20
            return True
        hook.user32.GetWindowThreadProcessId.side_effect = window_thread
        hook.user32.GetGUIThreadInfo.side_effect = gui_info
        hook.user32.IsChild.return_value = True
        def class_name(hwnd, output, size):
            output.value = "ApplicationFrameWindow" if hwnd == 10 else "Windows.UI.Core.CoreWindow"
            return len(output.value)
        hook.user32.GetClassNameW.side_effect = class_name
        hook.user32.GetKeyboardLayout.side_effect = lambda thread: 0x04190419 if thread == 11 else 0x04090409
        hook.process_name = lambda pid: "applicationframehost.exe" if pid == 100 else "systemsettings.exe"
        return hook

    def test_hosted_settings_uses_host_layout_despite_child_reporting_english(self):
        context = self.fixture().context()
        self.assertEqual((context.focus, context.thread_id, context.process_name), (20, 11, "systemsettings.exe"))
        self.assertEqual((context.language, context.layout_window), ("ru", 10))

    def test_unknown_cross_thread_editor_uses_same_foreground_owner(self):
        hook = self.fixture()
        hook.user32.GetClassNameW.side_effect = lambda hwnd, output, size: 0
        context = hook.context()
        self.assertEqual((context.focus, context.thread_id, context.language,
                          context.layout_window), (20, 11, "ru", 10))

    def test_correction_requests_host_and_observes_host_thread(self):
        from test_app_controls import make_hook
        hook = make_hook()
        context = self.fixture().context()
        self.assertTrue(hook._correct(context, 32, ""))
        self.assertEqual(hook.user32.SendMessageTimeoutW.call_args.args[0], 10)
        hook.user32.GetKeyboardLayout.assert_called_with(11)

    def test_unrelated_focus_and_excluded_child_rejected(self):
        hook = self.fixture()
        hook.user32.IsChild.return_value = False
        self.assertIsNone(hook.context())
        hook.user32.IsChild.return_value = True
        hook.settings = Settings(excluded_processes=("systemsettings.exe",))
        self.assertIsNone(hook.context())

    def test_tray_target_validates_host_and_content_separately(self):
        service = WindowsLayouts.__new__(WindowsLayouts)
        service.u = self.fixture().user32
        service.u.IsWindow.return_value = True
        target = LayoutTarget(10, 20, 22, 200, 100)
        self.assertTrue(service.valid(target))
        service.u.IsChild.return_value = False
        self.assertFalse(service.valid(target))


if __name__ == "__main__":
    unittest.main()
