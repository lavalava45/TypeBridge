"""Desktop editor compatibility: mocked OS/fields, no hooks or live typing."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from engine import Decision
from password_fields import WATCH_SCRIPT, SCRIPT_BOOTSTRAP
import test_r04_default_text_target as r04
from typebridge_app import LiveHook, Settings, VK_SPACE, VK_BACK

# Representative process/control metadata, not a list granting permissions.
DESKTOP_FIELDS = (
    ('notes.exe', 'Chrome_WidgetWin_1'),       # desktop web/Electron
    ('winword.exe', '_WwG'),                  # Office document
    ('soffice.bin', 'SALFRAME'),              # office custom window
    ('qt-editor.exe', 'Qt5152QWindowIcon'),
    ('wpf-editor.exe', 'HwndWrapper[Editor;;1]'),
    ('winui-editor.exe', 'Microsoft.UI.Content.DesktopChildSiteBridge'),
)
HKLS = {'ru': 0x419, 'en': 0x409, 'he': 0x40D}
WORDS = {'ru': 'привет', 'en': 'hello', 'he': 'שלום'}


class DesktopEditorTests(unittest.TestCase):
    def fixture(self, process, control, source='en', target='ru'):
        hook = r04.R04DefaultTargetTests.hooked_context(process, control)
        r04.metadata(hook.password_guard)
        # Simulate provider freshness here. Real refresh rejection/timeout is
        # separately covered by R04 tests; no UIA of a user's field is started.
        hook.password_guard.revalidate = Mock(side_effect=lambda top, focus, rev:
            hook.password_guard.permit_revision(top, focus) == rev)
        hook.settings = replace(Settings(), early_correction=True, early_native=True)
        hook.engine.mapper.layouts = HKLS
        hook.engine.mapper.word = lambda keys, language: WORDS[language]
        hook.engine.decide_token.return_value = Decision('source', WORDS[target], target, 'unique dictionary match')
        hook.engine.decide.return_value = None
        hook.keys = [(ord(c), False) for c in 'SOURCE']
        hook.user32.GetKeyboardLayout.side_effect = None
        hook.user32.GetKeyboardLayout.return_value = HKLS[source]
        def change_layout(*args):
            hook.user32.GetKeyboardLayout.return_value = int(args[3])
            return 1
        hook.user32.SendMessageTimeoutW.side_effect = change_layout
        return hook

    def test_boundary_and_manual_packets_in_six_directions_for_each_family(self):
        for process, control in DESKTOP_FIELDS:
            for source in HKLS:
                for target in HKLS:
                    if source == target:
                        continue
                    for manual in (False, True):
                        with self.subTest(process=process, source=source, target=target, manual=manual):
                            hook = self.fixture(process, control, source, target)
                            context = hook.context()
                            self.assertIsNotNone(context)
                            self.assertTrue(LiveHook._correct(hook, context,
                                None if manual else VK_SPACE, '', manual=manual))
                            hook.user32.SendInput.assert_called_once()
                            events = hook.user32.SendInput.call_args.args[1]
                            text = ''.join(chr(e.u.ki.wScan) for e in events
                                if e.u.ki.dwFlags & 4 and not e.u.ki.dwFlags & 2)
                            self.assertEqual(text, WORDS[target])
                            self.assertEqual(hook.last_language, target)

    def test_early_keyboard_route_survives_richedit_quarantine_in_all_families(self):
        for process, control in DESKTOP_FIELDS:
            with self.subTest(process=process):
                hook = self.fixture(process, control)
                hook.native_early_disabled = True
                hook.native_edit_disabled = True
                hook.native_early_deferred = True
                context = hook.context()
                decision = Decision('source', WORDS['ru'], 'ru', 'unique dictionary match')
                with patch('typebridge_app.suggest_live_early', return_value=decision), \
                     patch('typebridge_app.NativeEditBridge') as native, \
                     patch('typebridge_app.ScintillaEarlyBridge') as scintilla:
                    self.assertTrue(LiveHook._try_early(hook, context))
                native.assert_not_called()
                scintilla.assert_not_called()
                events = hook.user32.SendInput.call_args.args[1]
                deletes = [e for e in events if e.u.ki.wVk == VK_BACK and not e.u.ki.dwFlags & 2]
                self.assertEqual(len(deletes), 5)  # final physical key is held
                self.assertTrue(hook.early_done)
                self.assertEqual(hook.last_language, 'ru')

    def test_keyboard_failure_still_blocks_its_early_route(self):
        hook = self.fixture(*DESKTOP_FIELDS[0])
        hook.keyboard_early_disabled = True
        with patch('typebridge_app.suggest_live_early') as decide:
            self.assertFalse(LiveHook._try_early(hook, hook.context()))
        decide.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_keyboard_failure_does_not_disable_native_richedit_recognition(self):
        hook = self.fixture('notepad.exe', 'RichEditD2DPT')
        hook.keyboard_early_disabled = True
        with patch('typebridge_app.suggest_live_early', return_value=None) as decide:
            self.assertFalse(LiveHook._try_early(hook, hook.context()))
        decide.assert_called_once()
        hook.user32.SendInput.assert_not_called()

    def test_lost_input_barrier_still_blocks_keyboard_early_in_that_field(self):
        hook = self.fixture(*DESKTOP_FIELDS[0])
        context = hook.context()
        hook._quarantine_native('input-barrier-lost', context=context)
        with patch('typebridge_app.suggest_live_early') as decide:
            self.assertFalse(LiveHook._try_early(hook, context))
        decide.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_unexpected_worker_error_preserves_keyboard_early_stop(self):
        hook = self.fixture(*DESKTOP_FIELDS[0])
        hook._quarantine_native('worker-error')
        self.assertTrue(hook.keyboard_early_disabled)
        with patch('typebridge_app.suggest_live_early') as decide:
            self.assertFalse(LiveHook._try_early(hook, hook.context()))
        decide.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_native_quarantine_still_blocks_actual_richedit(self):
        hook = self.fixture('notepad.exe', 'RichEditD2DPT')
        hook.native_edit_disabled = True
        with patch('typebridge_app.suggest_live_early') as decide:
            self.assertFalse(LiveHook._try_early(hook, hook.context()))
        decide.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_password_unknown_and_missing_identity_refuse_desktop_context(self):
        for process, control in DESKTOP_FIELDS:
            for state, identity in (('password', 'uia:1,100'), ('unknown', ''), ('safe', '')):
                with self.subTest(process=process, state=state):
                    hook = self.fixture(process, control)
                    r04.metadata(hook.password_guard, state, identity)
                    self.assertIsNone(hook.context())
                    hook.user32.SendInput.assert_not_called()

    def test_development_exclusions_remain_in_force(self):
        hook = self.fixture('code.exe', 'Chrome_WidgetWin_1')
        self.assertIsNone(hook.context())
        hook.user32.SendInput.assert_not_called()

    def test_real_compiled_metadata_policy_for_desktop_capabilities(self):
        # Compile the real helper without Start/Query/field observation. Only
        # pure metadata policy is called: no GetText/Value/selection reads.
        script = WATCH_SCRIPT.split('try {\n    [PasswordMetadata]::Start()')[0]
        script += r"""
$edit = [System.Windows.Automation.ControlType]::Edit.Id
$doc = [System.Windows.Automation.ControlType]::Document.Id
$custom = [System.Windows.Automation.ControlType]::Custom.Id
$pane = [System.Windows.Automation.ControlType]::Pane.Id
$combo = [System.Windows.Automation.ControlType]::ComboBox.Id
$button = [System.Windows.Automation.ControlType]::Button.Id
@(
 [PasswordMetadata]::EditableMetadata($edit,$true,$true,$true,$false,$false,-1,$true)
 [PasswordMetadata]::EditableMetadata($doc,$true,$true,$false,$false,$true,-1,$true)
 [PasswordMetadata]::EditableMetadata($custom,$true,$true,$true,$false,$false,-1,$true)
 [PasswordMetadata]::EditableMetadata($doc,$true,$true,$false,$false,$true,0,$false)
 [PasswordMetadata]::EditableMetadata($custom,$true,$true,$true,$false,$false,-1,$false)
 [PasswordMetadata]::EditableMetadata($pane,$true,$true,$false,$false,$true,0,$false)
 [PasswordMetadata]::EditableMetadata($combo,$true,$true,$true,$false,$false,-1,$false)
 [PasswordMetadata]::EditableMetadata($custom,$true,$true,$true,$true,$false,-1,$false)
 [PasswordMetadata]::EditableMetadata($doc,$true,$true,$false,$false,$true,1,$false)
 [PasswordMetadata]::EditableMetadata($custom,$true,$true,$false,$false,$true,-1,$false)
 [PasswordMetadata]::EditableMetadata($button,$true,$true,$true,$false,$false,-1,$false)
 [PasswordMetadata]::EditableMetadata($pane,$true,$false,$true,$false,$false,-1,$false)
 [PasswordMetadata]::EditableMetadata($pane,$false,$true,$true,$false,$false,-1,$false)
) | ConvertTo-Json -Compress
"""
        exe = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-MTA', '-Command', SCRIPT_BOOTSTRAP],
            input=script.encode('utf-8'), capture_output=True, timeout=15, creationflags=0x08000000)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertEqual(json.loads(result.stdout.decode('utf-8-sig').strip().splitlines()[-1]),
            [True, True, False, True, True, True, True, False, False, False, False, False, False])


if __name__ == '__main__':
    unittest.main()
