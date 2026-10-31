"""Scintilla worker transport: byte editor and fake messages, no global input."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading
import unittest

from early_scintilla import ScintillaEarlyBridge
from engine import Decision
from native_transaction_queue import PhysicalKey
from test_app_controls import make_hook, key
from typebridge_app import FocusContext, WM_KEYDOWN, WM_KEYUP


class ModelEditor(ScintillaEarlyBridge):
    def __init__(self, text, hook):
        self.data, self.caret = text.encode(), len(text.encode())
        self.hook = hook
        self.timeout = self.budget_exceeded = self.write_started = False
        self.writes = 0
        self.reads = []
        self.commands = []
        self.undo = []
        self.group = None
        self.fault = ''
        self.on_prepare = lambda: None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.fault == 'exit' and self.write_started:
            raise RuntimeError('synthetic exit error')

    def snapshot(self):
        self.reads.append(threading.get_ident())
        if self.fault == 'read' and not self.write_started:
            self.timeout = True
            raise TimeoutError('editor-message')
        return self.data, self.caret

    def prepare(self, start, end, target):
        self.target, self.target_addr = target, 123
        self.on_prepare()

    def check_deadline(self):
        pass

    def msg(self, command, wp=0, lp=0):
        self.commands.append(command)
        if command == 2190: self.start = wp
        elif command == 2192: self.end = wp
        elif command == 2191: return self.start
        elif command == 2193: return self.end
        elif command == 2078: self.group = (self.data, self.caret)
        elif command == 2194:
            self.writes += 1
            self.data = self.data[:self.start] + self.target + self.data[self.end:]
            if self.fault == 'write':
                self.timeout = True
                raise TimeoutError('editor-message')
            return len(self.target)
        elif command == 2160: self.caret = wp
        elif command == 2079:
            self.undo.append(self.group)
            self.group = None
        return 0


class ScintillaWorkerTests(unittest.TestCase):
    def test_physical_six_directions_undo_includes_last_source_character(self):
        """Undo must restore the whole source, including physically delivered last key.

        This models physical delivery in Scintilla; it does not synthesize a
        global Windows hook or assert Notepad++ hardware integration.
        """
        for source, typed, target, expected, physical in (
                ('en', 'ghbdtn', 'ru', 'привет', 'ghbdtn'),
                ('he', 'עינגאמ', 'ru', 'привет', 'ghbdtn'),
                ('ru', 'руддщ', 'en', 'hello', 'hello'),
                ('he', 'יקךךם', 'en', 'hello', 'hello'),
                ('en', 'akuo', 'he', 'שלום', 'akuo'),
                ('ru', 'флгщ', 'he', 'שלום', 'akuo')):
            with self.subTest(source=source, target=target):
                hook, editor, language, factory = self.fixture(
                    source=source, typed=typed, target=target,
                    replacement=expected, physical=physical)
                self.assertFalse(hook._try_early(
                    hook.context(), await_native_keyup=True))
                factory.assert_not_called()
                self.assertEqual(editor.data, typed[:-1].encode())
                # The real editor receives the last physical key; no synthetic
                # SendInput may create an extra character or undo group.
                last = typed[-1].encode()
                editor.data += last
                editor.caret += len(last)
                self.assertEqual(key(hook, ord(physical[-1].upper()), WM_KEYUP), 0)
                hook.native_worker.join(2)
                self.assertFalse(hook.native_worker.is_alive())
                self.assertEqual(editor.data, expected.encode())
                self.assertEqual(editor.undo, [(typed.encode(), len(typed.encode()))])
                self.assertEqual(language[0], target)
                hook.user32.SendInput.assert_not_called()

    def test_keyup_before_last_character_reaches_scintilla_refuses_safely(self):
        hook, editor, language, factory = self.fixture(
            source='en', typed='akuo', target='he',
            replacement='שלום', physical='akuo')
        self.assertFalse(hook._try_early(hook.context(), await_native_keyup=True))
        self.assertEqual(key(hook, ord('O'), WM_KEYUP), 0)
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())
        self.assertEqual(editor.data, b'aku')
        self.assertEqual(editor.undo, [])
        self.assertEqual(language[0], 'en')
        hook.user32.SendInput.assert_not_called()

    def fixture(self, source='en', typed='ghbdtn', target='ru', replacement='привет', physical='ghbdtn'):
        hook = make_hook()
        hook._in_keyboard_callback = True
        hook._browser_field_revision = 0
        hook.thread_id = 123
        hook.user32.PostThreadMessageW.return_value = 1
        hook.user32.GetKeyState.return_value = 0
        hook.native_pending_recovery = None
        hook.token_blocked = False
        hook.mouse_hook = None
        hook.settings = replace(hook.settings, early_correction=True)
        layouts = {'en':0x409, 'ru':0x419, 'he':0x40d}
        language = [source]
        hook.last_language = source
        hook.engine = SimpleNamespace(mapper=SimpleNamespace(layouts=layouts))
        hook.keys = [(ord(c.upper()), False) for c in physical]
        hook._last_physical_event = PhysicalKey(hook.keys[-1][0],0,0,WM_KEYDOWN,True)
        hook.context = lambda: FocusContext(90,100,1,language[0],'notepad++.exe','synthetic',100)
        hook.user32.GetKeyboardLayout.side_effect = lambda tid: layouts[language[0]]
        def switch(hwnd, command, wp, hkl, *args):
            self.assertTrue(hook._native_is_off_hook())
            language[0] = next(lang for lang, value in layouts.items() if value == hkl)
            return 1
        hook.user32.SendMessageTimeoutW.side_effect = switch
        editor = ModelEditor(typed[:-1],hook)
        decision = Decision(typed,replacement,target,'synthetic')
        self.enterContext(patch('typebridge_app.suggest_live_early',return_value=decision))
        factory = self.enterContext(patch('typebridge_app.ScintillaEarlyBridge',return_value=editor))
        return hook,editor,language,factory

    def release(self,hook):
        serial = hook.user32.PostThreadMessageW.call_args.args[2]
        hook._release_keyboard_job(serial)
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())

    def start(self,hook):
        # Legacy scheduler unit tests exercise the worker directly. The
        # physical-keydown/key-up route has separate acceptance tests below.
        return hook._try_early(hook.context(),await_native_keyup=False)

    def test_no_adapter_reads_writes_or_hkl_request_before_callback_release(self):
        hook,editor,language,factory = self.fixture()
        caller = threading.get_ident()
        self.assertTrue(self.start(hook))
        factory.assert_not_called()
        self.assertFalse(editor.reads)
        hook.user32.SendMessageTimeoutW.assert_not_called()
        self.release(hook)
        self.assertEqual(editor.data.decode(),'привет')
        self.assertEqual(editor.writes,1)
        self.assertEqual(len(editor.reads),3)
        self.assertTrue(all(t != caller for t in editor.reads))
        self.assertEqual(language[0],'ru')
        hook.user32.SendInput.assert_not_called()

    def test_all_six_directions_keep_one_native_undo_group(self):
        for source,typed,target,expected,physical in (
            ('en','ghbdtn','ru','привет','ghbdtn'),('he','עינגאמ','ru','привет','ghbdtn'),
            ('ru','руддщ','en','hello','hello'),('he','יקךךם','en','hello','hello'),
            ('en','akuo','he','שלום','akuo'),('ru','флгщ','he','שלום','akuo')):
            with self.subTest(source=source,target=target):
                hook,editor,language,_ = self.fixture(source,typed,target,expected,physical)
                before = editor.snapshot()
                editor.reads.clear()
                self.assertTrue(self.start(hook)); self.release(hook)
                self.assertEqual(editor.data,expected.encode())
                self.assertEqual(language[0],target)
                self.assertEqual(editor.commands.count(2078),1)
                self.assertEqual(editor.commands.count(2079),1)
                # Preserve the original transaction's Undo snapshot/span;
                # this is a model check, not a real Notepad++ Undo test.
                self.assertEqual(editor.undo,[before])
                hook.user32.SendInput.assert_not_called()

    def test_fast_tail_is_delivered_once_after_confirmed_target_hkl(self):
        hook,editor,language,_ = self.fixture()
        packets = []
        def inject(count, events, *args):
            packets.append([(e.u.ki.wVk,e.u.ki.dwFlags) for e in events])
            for e in events:
                if e.u.ki.dwFlags & 2: continue
                char = ' ' if e.u.ki.wVk == 32 else ('а' if language[0]=='ru' else 'f')
                raw = char.encode()
                editor.data = editor.data[:editor.caret]+raw+editor.data[editor.caret:]
                editor.caret += len(raw)
            return count
        hook.user32.SendInput.side_effect = inject
        self.assertTrue(self.start(hook))
        key(hook,ord('N'),WM_KEYUP)
        key(hook,ord('F'));key(hook,ord('F'),WM_KEYUP)
        key(hook,32);key(hook,32,WM_KEYUP)
        self.release(hook)
        self.assertEqual(editor.data.decode(),'привета ')
        self.assertEqual(len(packets),1)
        self.assertFalse(any(vk==ord('N') for vk,flags in packets[0]))

    def test_read_only_timeout_replays_original_letter_once(self):
        hook,editor,language,_ = self.fixture()
        editor.fault = 'read'
        self.assertTrue(self.start(hook));key(hook,ord('N'),WM_KEYUP)
        self.release(hook)
        self.assertEqual(editor.writes,0)
        self.assertEqual(language[0],'en')
        events = hook.user32.SendInput.call_args.args[1]
        self.assertEqual([(e.u.ki.wVk,e.u.ki.dwFlags) for e in events],[(ord('N'),0),(ord('N'),2)])

    def test_possible_write_and_adapter_exit_fault_never_replay_held_letter(self):
        for fault in ('write','exit'):
            with self.subTest(fault=fault):
                hook,editor,language,_ = self.fixture()
                editor.fault = fault
                self.assertTrue(self.start(hook));key(hook,ord('N'),WM_KEYUP)
                self.release(hook)
                self.assertEqual(editor.writes,1)
                self.assertEqual(editor.commands.count(2079),1)
                hook.user32.SendInput.assert_not_called()
                hook.user32.SendMessageTimeoutW.assert_not_called()
                self.assertTrue(hook.early_disabled)

    def test_post_write_hkl_fault_cannot_duplicate_original_key(self):
        hook,editor,_,_ = self.fixture()
        hook.user32.SendMessageTimeoutW.side_effect = RuntimeError('synthetic HKL fault')
        self.assertTrue(self.start(hook));key(hook,ord('N'),WM_KEYUP)
        self.release(hook)
        self.assertEqual(editor.writes,1)
        hook.user32.SendInput.assert_not_called()
        self.assertTrue(hook.early_disabled)

    def test_focus_change_cancels_without_opening_adapter(self):
        hook,editor,_,factory = self.fixture()
        self.assertTrue(self.start(hook))
        original = hook.context()
        hook.context = lambda: replace(original,focus=original.focus+1)
        self.release(hook)
        factory.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_config_or_token_change_during_preflight_prevents_writes(self):
        for kind in ('settings','token'):
            with self.subTest(kind=kind):
                hook,editor,_,_ = self.fixture()
                def change():
                    if kind == 'settings':hook.settings = replace(hook.settings,early_correction=False)
                    else:hook.clear_token()
                editor.on_prepare = change
                self.assertTrue(self.start(hook));self.release(hook)
                self.assertEqual(editor.writes,0)
                self.assertEqual(hook.user32.SendInput.call_count,1)

    def test_failed_scheduler_never_falls_back_to_inline_scintilla(self):
        hook,editor,_,factory = self.fixture()
        hook.user32.PostThreadMessageW.return_value = 0
        self.assertFalse(self.start(hook))
        factory.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_metadata_reset_before_worker_does_not_drop_the_literal_key(self):
        hook,editor,_,factory = self.fixture()
        self.assertTrue(self.start(hook))
        hook.clear_token()
        self.release(hook)
        factory.assert_not_called()
        events = hook.user32.SendInput.call_args.args[1]
        self.assertEqual([(e.u.ki.wVk,e.u.ki.dwFlags) for e in events],[(ord('N'),0)])

    def test_click_during_read_preflight_cannot_write_or_replay_into_changed_caret(self):
        hook,editor,_,_ = self.fixture()
        editor.on_prepare = lambda: hook.handle_mouse(0,0x0201,0)
        self.assertTrue(self.start(hook));self.release(hook)
        self.assertEqual(editor.writes,0)
        hook.user32.SendInput.assert_not_called()


if __name__ == '__main__':unittest.main()
