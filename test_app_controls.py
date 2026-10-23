"""App controls tested without global hooks, foreground input or real settings."""
import ctypes
from pathlib import Path
from types import SimpleNamespace
import tempfile
import queue
import unittest
from unittest.mock import Mock, patch

from engine import Decision
from typebridge_app import (
    FocusContext, KbdHook, LiveHook, TypeBridgeGui, Settings, load_settings, save_settings,
    load_user_lexicon, WM_KEYDOWN, WM_KEYUP, VK_F8, VK_PAUSE, VK_CONTROL,
    VK_MENU, VK_SHIFT, OWN_INPUT_MARKER,
)


class SettingsTests(unittest.TestCase):
    def test_roundtrip_and_no_temporary_files_left(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            expected = Settings(conservative=False, manual_f8=False,
                                only_window_title="new 2")
            save_settings(expected, path)
            self.assertEqual(load_settings(path), expected)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["settings.json"])

    def test_old_settings_without_exclusions_load_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text('{"conservative": true}', encoding="utf-8")
            self.assertEqual(load_settings(path), Settings())

    def test_strings_are_not_accepted_as_booleans(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text('{"conservative": "false"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)

    def test_builtin_password_exclusions_cannot_be_removed_by_empty_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text('{"excluded_processes": []}', encoding="utf-8")
            self.assertIn("keepass.exe", load_settings(path).excluded_processes)

    def test_user_word_and_exclusion_persist_and_affect_engine(self):
        from engine import LanguageEngine, source_data_dir
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            lexicon = load_user_lexicon(path)
            lexicon.add("exclusions", "he", "שלום")
            lexicon.add("words", "en", "customtoken")
            lexicon.save(path)
            engine = LanguageEngine(source_data_dir(), user_lexicon=load_user_lexicon(path))
            self.assertIsNone(engine.decide([(ord(c.upper()), False) for c in "akuo"], "en"))
            self.assertTrue(engine._known("en", "customtoken"))


def make_hook():
    hook = LiveHook.__new__(LiveHook)
    hook.engine = SimpleNamespace(decide=Mock(return_value=Decision(
        "akuo", "שלום", "he", "unique")), mapper=SimpleNamespace(layouts={"he": 0x040D}))
    hook.engine.decide_token = hook.engine.decide
    hook.settings = Settings()
    hook.emit = Mock()
    hook.accept_injected = False
    hook.user32 = Mock()
    hook.user32.CallNextHookEx.return_value = 0
    hook.user32.GetKeyboardLayout.return_value = 0x040D
    hook.user32.SendInput.side_effect = lambda count, *args: count
    hook.user32.SendMessageTimeoutW.return_value = 0
    hook.keys = [(65, False), (75, False), (85, False), (79, False)]
    hook.last_focus = 100
    hook.last_language = "en"
    hook.suppressed_keyups = set()
    hook.keyboard_hook = None
    hook.paused = False
    hook.stopping = False
    hook.thread_id = 0
    hook.pressed = lambda vk: False
    hook.context = lambda: FocusContext(90, 100, 1, "en", "editor.exe", "test")
    return hook


def key(hook, vk, message=WM_KEYDOWN, *, flags=0, extra=0):
    event = KbdHook(vk, 0, flags, 0, extra)
    return hook.handle_keyboard(0, message, ctypes.addressof(event))


class HookControlTests(unittest.TestCase):
    def test_stop_event_unlocks_controls_even_while_worker_is_finishing(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.events = queue.Queue()
        gui.events.put(("stopped", "Остановлен"))
        gui.worker = Mock()
        gui.worker.is_alive.return_value = True
        gui.root = Mock()
        gui.status = Mock()
        gui.log = Mock()
        gui.log.index.return_value = "2.0"
        gui.corrections_enabled = Mock()
        gui.corrections_check = Mock()
        gui.pause_button = Mock()
        gui.closing = False
        gui.run_state = "running"
        gui.last_start_error = ""
        gui.tray = Mock()
        gui._drain()
        gui.corrections_enabled.set.assert_called_with(False)
        gui.corrections_check.configure.assert_called_with(state="normal")

    def test_tray_toggle_pauses_running_and_starts_stopped_or_paused(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.pause = Mock()
        gui.start = Mock()
        for state in ("running", "paused", "stopped", "error", "starting", "stopping"):
            gui.run_state = state
            gui.pause.reset_mock()
            gui.start.reset_mock()
            gui.toggle_corrections()
            self.assertEqual(gui.pause.call_count, int(state == "running"))
            self.assertEqual(gui.start.call_count, int(state in ("paused", "stopped", "error")))

    def test_start_error_is_not_erased_by_final_stopped_event(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.events = queue.Queue()
        gui.events.put(("error", "Ошибка запуска: SwitchER is still running"))
        gui.events.put(("stopped", "Остановлен"))
        gui.worker = Mock()
        gui.worker.is_alive.return_value = False
        gui.root = Mock()
        gui.status = Mock()
        gui.log = Mock()
        gui.log.index.return_value = "2.0"
        gui.corrections_enabled = Mock()
        gui.corrections_check = Mock()
        gui.pause_button = Mock()
        gui.closing = False
        gui.run_state = "starting"
        gui.last_start_error = ""
        gui.tray = Mock()
        gui._drain()
        self.assertEqual(gui.run_state, "error")
        gui.status.set.assert_called_with("Ошибка запуска: SwitchER is still running")
        gui.tray.set_state.assert_called_with("error", "Ошибка запуска: SwitchER is still running")

    def test_tray_menu_has_one_checked_toggle_and_disables_invalid_actions(self):
        from tray_win32 import menu_items, MENU_ENABLE
        for state in ("running", "paused", "stopped", "starting", "stopping", "error"):
            rows = {code: (title, enabled, checked)
                    for code, title, enabled, checked in menu_items(state)}
            self.assertEqual(rows[MENU_ENABLE][2], state == "running")
            self.assertEqual(rows[MENU_ENABLE][1], state not in ("starting", "stopping"))
            self.assertFalse(any("Остановить" in row[0] for row in rows.values()))
            if state in ("starting", "stopping", "error"):
                self.assertFalse(rows[0][1])
        self.assertIn("SwitchER", menu_items("error", "Ошибка запуска: SwitchER")[0][1])

    def test_manual_f8_replaces_unfinished_word_without_space_and_swallows_keyup(self):
        hook = make_hook()
        observed = []
        decision = hook.engine.decide.return_value
        hook.engine.decide.side_effect = lambda keys, language, **options: (
            observed.append((tuple(keys), language, options)) or decision)
        self.assertEqual(key(hook, VK_F8), 1)
        self.assertEqual(observed, [(((65, False), (75, False), (85, False),
                                     (79, False)), "en", {"allow_rare": True})])
        # 4 backspaces + 4 unicode letters, down/up pairs; no extra separator.
        self.assertEqual(hook.user32.SendInput.call_args.args[0], 16)
        self.assertEqual(hook.last_language, "he")
        self.assertEqual(key(hook, VK_F8, WM_KEYUP), 1)
        self.assertFalse(hook.suppressed_keyups)

    def test_manual_f8_can_be_disabled(self):
        hook = make_hook()
        hook.settings = Settings(manual_f8=False)
        hook.pressed = lambda vk: vk == VK_CONTROL
        self.assertEqual(key(hook, VK_F8), 0)
        hook.engine.decide.assert_not_called()

    def test_pause_chord_does_not_repeat_and_clears_token(self):
        hook = make_hook()
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_MENU)
        self.assertEqual(key(hook, VK_PAUSE), 1)
        self.assertTrue(hook.paused)
        self.assertFalse(hook.keys)
        key(hook, VK_PAUSE)
        self.assertTrue(hook.paused)
        key(hook, VK_PAUSE, WM_KEYUP)
        key(hook, VK_PAUSE)
        self.assertFalse(hook.paused)

    def test_paused_hook_does_not_correct(self):
        hook = make_hook()
        hook.paused = True
        self.assertEqual(key(hook, VK_F8), 0)
        hook.engine.decide.assert_not_called()
        self.assertFalse(hook.keys)

    def test_shift_does_not_destroy_unfinished_word(self):
        hook = make_hook()
        key(hook, VK_SHIFT)
        self.assertEqual(len(hook.keys), 4)

    def test_injected_input_and_own_input_cannot_trigger_manual_correction(self):
        for flags, extra in ((0x10, 0), (0x10, OWN_INPUT_MARKER)):
            hook = make_hook()
            self.assertEqual(key(hook, VK_F8, flags=flags, extra=extra), 0)
            hook.engine.decide.assert_not_called()

    def test_stop_before_start_sets_flag_without_posting_to_invalid_thread(self):
        hook = make_hook()
        self.assertFalse(hook.request_stop())
        self.assertTrue(hook.stopping)
        hook.user32.PostThreadMessageW.assert_not_called()


class WordStartRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from engine import LanguageEngine, source_data_dir
        cls.engine = LanguageEngine(source_data_dir())

    def type_token(self, physical):
        hook = make_hook()
        hook.engine = self.engine
        hook.keys = []
        hook.user32.GetKeyState.return_value = 0
        hook.user32.GetKeyboardLayout.return_value = 0x0419
        output = []

        def capture(count, batch, size):
            output.append("".join(chr(event.u.ki.wScan) for event in batch
                                  if event.u.ki.dwFlags == 4))
            return count

        hook.user32.SendInput.side_effect = capture
        for char in physical:
            vk = {",": 0xBC, ".": 0xBE, ";": 0xBA}.get(char, ord(char.upper()))
            key(hook, vk)
            key(hook, vk, WM_KEYUP)
        key(hook, 0x20)
        return output

    def test_belyi_is_collected_with_initial_comma_key(self):
        self.assertEqual(self.type_token(",tksq"), ["белый"])

    def test_other_letter_keys_at_word_start_and_existing_case(self):
        for physical, expected in ((".ysq", "юный"), (";tknsq", "желтый"),
                                    ("ptktysq", "зеленый")):
            with self.subTest(physical=physical):
                self.assertEqual(self.type_token(physical), [expected])

    def test_literal_punctuation_and_correct_english_are_not_converted(self):
        for physical in (",", ".", ";", ",hello", ".hello", "hello,"):
            with self.subTest(physical=physical):
                self.assertEqual(self.type_token(physical), [])


if __name__ == "__main__":
    unittest.main()
