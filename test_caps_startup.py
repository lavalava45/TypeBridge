"""Focused checks with no hooks, physical input or real startup registration."""
from dataclasses import replace
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from caps_correction import caps_decision
from engine import LanguageEngine, source_data_dir
from typebridge_app import (Settings, load_settings, save_settings, TypeBridgeGui,
                              FocusContext, VK_CAPITAL, VK_SPACE, OWN_INPUT_MARKER, main)
from test_app_controls import make_hook
from user_lexicon import UserLexicon
import startup_registration as startup


def keys(text):
    return [(ord(c.upper()), c.isupper()) for c in text]


class CapsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = LanguageEngine(source_data_dir())

    def test_known_inverted_title_case(self):
        for physical, language, expected in (("gHBDTN", "ru", "Привет"),
                                              ("hELLO", "en", "Hello")):
            result = caps_decision(self.engine, keys(physical), language)
            self.assertEqual((result.replacement, result.language), (expected, language))

    def test_deliberate_caps_mixed_unknown_and_hebrew_preserved(self):
        for text, lang in (("HELLO", "en"), ("Hello", "en"), ("hello", "en"),
                           ("iPhone", "en"), ("qZXQZX", "en"), ("akuo", "he")):
            self.assertIsNone(caps_decision(self.engine, keys(text), lang))

    def test_exclusions_respected(self):
        lexicon = UserLexicon()
        lexicon.add("exclusions", "en", "Hello", sensitive=True)
        engine = LanguageEngine(source_data_dir(), user_lexicon=lexicon)
        self.assertIsNone(caps_decision(engine, keys("hELLO"), "en"))

    def fixture(self, enabled=True, caps=True):
        hook = make_hook()
        hook.engine = self.engine
        hook.settings = Settings(fix_caps_lock=enabled)
        hook.keys = keys("gHBDTN")
        hook.user32.GetKeyState.return_value = int(caps)
        hook.user32.GetKeyboardLayout.return_value = self.engine.mapper.layouts['ru']
        hook.context = lambda: FocusContext(90, 100, 1, 'ru', 'editor.exe', 'test')
        return hook

    def test_caps_toggle_and_replacement_share_marked_batch(self):
        hook = self.fixture()
        context = FocusContext(90, 100, 1, 'ru', 'editor.exe', 'test')
        self.assertTrue(hook._correct(context, VK_SPACE, ''))
        count, batch, size = hook.user32.SendInput.call_args.args
        self.assertEqual([batch[i].u.ki.wVk for i in (0, 1)], [VK_CAPITAL, VK_CAPITAL])
        self.assertTrue(batch[1].u.ki.dwFlags & 2)
        self.assertTrue(all(batch[i].u.ki.dwExtraInfo == OWN_INPUT_MARKER for i in range(count)))
        self.assertEqual(hook.last_word.text, 'Привет')
        self.assertEqual(hook.engine.mapper.word(hook.last_word.keys, 'ru'), 'Привет')

    def test_disabled_caps_off_paused_and_partial_input(self):
        context = FocusContext(90, 100, 1, 'ru', 'editor.exe', 'test')
        for enabled, caps, paused in ((False, True, False), (True, False, False), (True, True, True)):
            hook = self.fixture(enabled, caps); hook.paused = paused
            self.assertFalse(hook._correct(context, VK_SPACE, ''))
            hook.user32.SendInput.assert_not_called()
        hook = self.fixture(); hook.user32.SendInput.side_effect = lambda count, *args: count - 1
        self.assertTrue(hook._correct(context, VK_SPACE, ''))  # Uncertain edit swallows boundary.
        self.assertTrue(hook.token_blocked)
        self.assertIsNone(hook.last_word)
        self.assertEqual(hook.user32.SendInput.call_count, 3)  # Batch + suffix attempt + release.


class StartupTests(unittest.TestCase):
    def test_frozen_command_quoted_and_registry_noop(self):
        with patch.object(startup.sys, 'frozen', True, create=True), patch.object(startup.sys, 'executable', r'C:\Program Files\TypeBridge\TypeBridge.exe'):
            self.assertEqual(startup.startup_command(), '"C:\\Program Files\\TypeBridge\\TypeBridge.exe" --autostart')
        with patch.object(startup, 'registration_state', return_value={'TypeBridge':None,'LayoutSwitch':None}), patch.object(startup.winreg, 'OpenKey') as opened, patch.object(startup.winreg, 'CreateKeyEx') as created:
            startup.write_registration(None)
            opened.assert_not_called(); created.assert_not_called()

    def test_enable_disable_only_own_user_value(self):
        with patch.object(startup, 'registration_state', return_value={'TypeBridge':None,'LayoutSwitch':None}), patch.object(startup.winreg, 'CreateKeyEx') as created, patch.object(startup.winreg, 'SetValueEx') as set_value:
            startup.write_registration('command')
            self.assertEqual(created.call_args.args[:2], (startup.winreg.HKEY_CURRENT_USER, startup.RUN_KEY))
            self.assertEqual(set_value.call_args.args[1:], ('TypeBridge', 0, startup.winreg.REG_SZ, 'command'))
        with patch.object(startup, 'registration_state', return_value={'TypeBridge':'command','LayoutSwitch':None}), patch.object(startup.winreg, 'CreateKeyEx'), patch.object(startup.winreg, 'DeleteValue') as deleted:
            startup.write_registration(None)
            self.assertEqual(deleted.call_args.args[1], 'TypeBridge')

    def test_flags_roundtrip_and_strict_booleans(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'settings.json'
            expected = Settings(fix_caps_lock=True, autostart=True, corrections_enabled=True)
            save_settings(expected,p); self.assertEqual(load_settings(p),expected)
            for flag in ('fix_caps_lock','autostart','corrections_enabled'):
                p.write_text('{"'+flag+'":"false"}',encoding='utf-8')
                with self.assertRaises(ValueError): load_settings(p)

    def test_autostart_hides_window_and_does_not_override_saved_correction_state(self):
        with patch('typebridge_app.tk.Tk') as root, patch('typebridge_app.TypeBridgeGui') as gui, patch('typebridge_app.migrate_user_data'):
            self.assertEqual(main(['--autostart']),0)
            root.return_value.withdraw.assert_called_once()
            root.return_value.after.assert_not_called()


class GuiTests(unittest.TestCase):
    def test_one_checkbox_no_stop_or_pause_label_and_settings(self):
        root=tk.Tk(); root.withdraw()
        try:
            with patch('typebridge_app.load_settings',return_value=Settings()), patch('typebridge_app.load_user_lexicon',return_value=UserLexicon()), patch('typebridge_app.read_registration',return_value=None), patch('typebridge_app.write_registration') as write, patch('typebridge_app.save_settings') as save:
                gui=TypeBridgeGui(root)
                gui.fix_caps_lock.set(True)
                gui.autostart.set(True)
                with patch('typebridge_app.startup_command',return_value='command'):
                    gui.save()
                write.assert_called_once_with('command')
                self.assertTrue(save.call_args.args[0].fix_caps_lock)
                self.assertTrue(save.call_args.args[0].autostart)
                def labels(widget):
                    values=[]
                    for child in widget.winfo_children():
                        try: values.append(str(child.cget('text')))
                        except tk.TclError: pass
                        values.extend(labels(child))
                    return values
                text=labels(root)
                self.assertEqual(text.count('Включить автоисправление'),1)
                self.assertNotIn('Остановить',text)
                self.assertFalse(any('Ctrl + Alt + Pause' in t for t in text))
                for state in ('running','paused','stopped','starting','error'):
                    gui.run_state=state; gui._sync_tray_state()
                    self.assertEqual(gui.corrections_enabled.get(),state in ('starting','running'))
        finally: root.destroy()

    def test_correction_preference_saved_and_restored_without_installing_hook(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            for enabled in (True,False):
                save_settings(Settings(corrections_enabled=enabled),path)
                root=tk.Tk(); root.withdraw()
                try:
                    scheduled=[]
                    with patch('typebridge_app.load_settings',return_value=load_settings(path)), patch('typebridge_app.load_user_lexicon',return_value=UserLexicon()), patch('typebridge_app.read_registration',return_value=None), patch.object(root,'after',side_effect=lambda delay,callback: scheduled.append((delay,callback))):
                        gui=TypeBridgeGui(root)
                        self.assertEqual(gui.corrections_enabled.get(),enabled)
                        self.assertEqual(gui.current_settings().corrections_enabled,enabled)
                        restore=[callback for delay,callback in scheduled if delay==300]
                        self.assertEqual(len(restore),int(enabled))
                        gui.start=Mock()
                        if restore: restore[0]()
                        self.assertEqual(gui.start.call_count,int(enabled))
                finally: root.destroy()

    def test_start_from_tray_saves_enabled_state_before_loading(self):
        gui=TypeBridgeGui.__new__(TypeBridgeGui)
        gui.closing=False; gui.corrections_enabled=Mock(); gui.worker=None
        gui.run_state='stopped'; gui.save=Mock(return_value=None); gui._sync_tray_state=Mock()
        gui.start()
        gui.corrections_enabled.set.assert_called_once_with(True)
        gui.save.assert_called_once()

    def test_registration_rolls_back_on_settings_write_failure(self):
        gui=TypeBridgeGui.__new__(TypeBridgeGui)
        gui.current_settings=Mock(return_value=Settings(autostart=True))
        gui.root=Mock()
        with patch('typebridge_app.read_registration',return_value='old'), patch('typebridge_app.startup_command',return_value='new'), patch('typebridge_app.write_registration') as write, patch('typebridge_app.save_settings',side_effect=OSError('disk full')), patch('typebridge_app.messagebox.showerror'):
            self.assertIsNone(gui.save())
            self.assertEqual([c.args[0] for c in write.call_args_list],['new','old'])


if __name__ == '__main__': unittest.main()
