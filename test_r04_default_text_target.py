"""Field-level R04 regressions: mocked Win32, metadata only, no live input."""
import threading
import time
import unittest
from unittest.mock import Mock

from password_fields import PasswordFieldGuard
from r04_text_target import BROWSER_HOSTS, native_field_metadata
from test_app_controls import key
from test_short_words_settings import HostedFocusTests
from typebridge_app import (LiveHook, Settings, VK_SPACE, VK_CONTROL, VK_SHIFT,
                            input_key, staged_password_guard)


def metadata(guard, state='safe', element='uia:1,100'):
    ticks = 621355968000000000 + int(time.time() * 10_000_000)
    guard.ingest(f'10|20|{state}|{ticks}|checked|{element}')


class R04DefaultTargetTests(unittest.TestCase):
    @staticmethod
    def hooked_context(process='chrome.exe', focus_class='Chrome_RenderWidgetHostHWND', strict=False):
        fixture = HostedFocusTests().fixture()
        from test_r04_staged_hook_integration import R04StagedIntegrationTests
        settings = Settings(experimental_password_guard=strict)
        guard = staged_password_guard(settings)
        hook = R04StagedIntegrationTests().real_constructor(guard)
        hook.user32 = fixture.user32
        hook.user32.CallNextHookEx.return_value = 0
        hook.user32.SendInput.side_effect = lambda count, *args: count
        hook.user32.GetKeyState.return_value = 0
        hook.process_name = lambda pid: process
        hook.user32.GetClassNameW.side_effect = lambda hwnd, buf, count: setattr(
            buf, 'value', 'Chrome_WidgetWin_1' if hwnd == 10 and process == 'chrome.exe' else focus_class)
        hook.settings = settings
        hook._cycle_word = Mock()
        hook._switch_direct = Mock()
        hook._try_snippet = Mock(return_value=(False, False))
        hook._correct = Mock(return_value=False)
        hook._try_early = Mock(return_value=False)
        hook._boundary = Mock(return_value=None)
        hook.engine.mapper.char = Mock(return_value='a')
        hook.engine.active_languages = ('ru', 'en', 'he')
        hook.engine.language_catalog = None
        hook._guard_skipped_token = False
        hook.keys = []
        return hook

    def test_browser_names_select_element_metadata_not_a_ban(self):
        for browser in BROWSER_HOSTS:
            with self.subTest(browser=browser):
                self.assertFalse(native_field_metadata(browser, browser, 'Edit'))
                hook = self.hooked_context(browser)
                metadata(hook.password_guard)
                self.assertIsNotNone(hook.context())

    def test_default_factory_checks_browser_and_keeps_native_fast_path(self):
        guard = staged_password_guard(Settings())
        self.assertTrue(guard.require_element_id)
        self.assertTrue(guard.native_fast_path)
        self.assertFalse(guard.native_only)
        self.assertFalse(staged_password_guard(Settings(experimental_password_guard=True)).native_fast_path)

    def test_safe_browser_auto_manual_and_cycle_reach_existing_routes(self):
        for strict in (False, True):
            with self.subTest(strict=strict):
                hook = self.hooked_context(strict=strict)
                metadata(hook.password_guard)
                hook._boundary.return_value = (VK_SPACE, '')
                key(hook, VK_SPACE)
                hook._correct.assert_called_once()
                hook._correct.reset_mock()
                key(hook, 0x91)  # configured manual correction
                hook._correct.assert_called_once()
                key(hook, 0x13)
                hook._cycle_word.assert_called_once()

    def test_browser_boundary_and_manual_packets_keep_all_six_directions(self):
        from engine import Decision
        hkls = {'ru': 0x419, 'en': 0x409, 'he': 0x40D}
        targets = {'ru': 'привет', 'en': 'hello', 'he': 'שלום'}
        # Real LiveHook/context/packet paths; only OS and lexical decision are
        # simulated. This proves routing, NOT a browser's document outcome.
        for source in hkls:
            for target in hkls:
                if source == target:
                    continue
                for manual in (False, True):
                    with self.subTest(source=source, target=target, manual=manual):
                        hook = self.hooked_context()
                        metadata(hook.password_guard)
                        hook.user32.GetKeyboardLayout.side_effect = None
                        hook.user32.GetKeyboardLayout.return_value = hkls[source]
                        hook.engine.mapper.layouts = hkls
                        hook.engine.decide_token.return_value = Decision(
                            'source', targets[target], target, 'unique dictionary match')
                        hook.keys = [(ord(c), False) for c in 'SOURCE']
                        context = hook.context()
                        self.assertEqual(context.language, source)
                        def switch(*_):
                            hook.user32.GetKeyboardLayout.return_value = hkls[target]
                            return 1
                        hook.user32.SendMessageTimeoutW.side_effect = switch
                        thread = threading.Thread(target=lambda: (
                            time.sleep(.025), metadata(hook.password_guard)))
                        thread.start()
                        try:
                            result = LiveHook._correct(hook, context,
                                None if manual else VK_SPACE, '', manual=manual)
                        finally:
                            thread.join()
                        self.assertTrue(result)
                        hook.user32.SendInput.assert_called_once()
                        self.assertEqual(hook.last_language, target)

    def test_password_unknown_and_stale_browser_keys_pass_without_mutations(self):
        for state in ('password', 'unknown', 'stale'):
            with self.subTest(state=state):
                hook = self.hooked_context()
                metadata(hook.password_guard, 'safe' if state == 'stale' else state)
                if state == 'stale':
                    hook.password_guard._update(10, 20, 'safe', element_id='uia:1,100',
                                               checked_at=time.monotonic() - 1)
                for vk in (65, 75, 85, 79, VK_SPACE, 0x91, 0x13):
                    self.assertEqual(key(hook, vk), 0)
                hook._correct.assert_not_called()
                hook._cycle_word.assert_not_called()
                hook._try_snippet.assert_not_called()
                hook.user32.SendInput.assert_not_called()
                self.assertFalse(hook.keys)

    def test_enter_delimiter_does_not_invalidate_safe_browser_word_prematurely(self):
        hook = self.hooked_context()
        metadata(hook.password_guard)
        hook._boundary.return_value = (0x0D, '')
        key(hook, 0x0D)
        hook._correct.assert_called_once()
        self.assertIsNotNone(hook.password_guard.permit_revision(10, 20))

    def test_same_hwnd_field_identity_discards_old_token(self):
        hook = self.hooked_context()
        metadata(hook.password_guard)
        key(hook, 65)
        self.assertEqual(len(hook.keys), 1)
        metadata(hook.password_guard, 'password', 'uia:1,101')
        key(hook, 66)
        self.assertEqual(hook.keys, [])
        metadata(hook.password_guard, 'safe', 'uia:1,102')
        key(hook, VK_SPACE)  # establish a complete new-field token boundary
        key(hook, 67)
        self.assertEqual(hook.keys, [(67, False)])

    def test_direct_layout_selection_works_without_text_permission(self):
        for state in ('unknown', 'password'):
            with self.subTest(state=state):
                hook = self.hooked_context()
                metadata(hook.password_guard, state)
                hook.pressed = lambda vk: vk in (VK_CONTROL, VK_SHIFT)
                key(hook, 0x70)  # Ctrl+Shift+F1 -> RU, no text mutation
                hook._switch_direct.assert_called_once()
                self.assertEqual(hook._switch_direct.call_args.args[1], 'ru')
                hook._correct.assert_not_called()
                hook.user32.SendInput.assert_not_called()

    def test_layout_worker_keeps_unknown_browser_permission_separate(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        hook = self.hooked_context()
        metadata(hook.password_guard, 'password')
        context = hook.layout_context()
        self.assertIsNotNone(context)
        self.assertIsNone(hook.context())
        hook.engine.mapper.layouts = {'ru': 0x419}
        service = Mock()
        service.capture.return_value = SimpleNamespace(top=10, focus=20)
        with patch('typebridge_app.WindowsLayouts', return_value=service):
            self.assertTrue(hook._schedule_native(context,
                lambda: LiveHook._switch_direct(hook, context, 'ru'), layout_only=True))
            hook.native_worker.join(timeout=1)
        self.assertFalse(hook.native_worker.is_alive())
        service.request.assert_called_once()
        hook.user32.SendInput.assert_not_called()
        self.assertIsNone(hook.context())  # TLS permission never leaks back.

    def test_pause_command_is_not_gated_by_password_metadata(self):
        hook = self.hooked_context()
        metadata(hook.password_guard, 'password')
        from typebridge_app import VK_MENU
        hook.pressed = lambda vk: vk in (VK_CONTROL, VK_MENU)
        self.assertEqual(key(hook, 0x13), 1)
        self.assertTrue(hook.paused)
        hook.user32.SendInput.assert_not_called()

    def test_excluded_window_does_not_poison_next_verified_word(self):
        hook = self.hooked_context()
        hook.settings = Settings(excluded_processes=('chrome.exe',))
        key(hook, 65)
        hook.settings = Settings()
        metadata(hook.password_guard)
        key(hook, 66)
        self.assertEqual(hook.keys, [(66, False)])

    def test_unknown_browser_prefix_does_not_poison_other_native_field(self):
        hook = self.hooked_context()
        key(hook, 65)  # Unknown field: pass physical prefix, no correction.
        hook.process_name = lambda _pid: 'notepad++.exe'
        def other_focus(_thread, output):
            output._obj.hwndFocus = 30
            return 1
        hook.user32.GetGUIThreadInfo.side_effect = other_focus
        hook.user32.GetClassNameW.side_effect = lambda hwnd, buf, count: setattr(
            buf, 'value', 'Scintilla')
        key(hook, 66)
        self.assertEqual(hook.keys, [(66, False)])

    def test_native_edit_and_scintilla_do_not_wait_for_default_helper(self):
        for process, name in (('notepad.exe','RichEditD2DPT'),('notepad++.exe','Scintilla')):
            hook = self.hooked_context(process, name)
            self.assertIsNotNone(hook.context())  # helper still unknown
        hook = self.hooked_context('notepad.exe', 'Edit')
        hook.user32.GetWindowLongPtrW.return_value = 0x20
        self.assertIsNone(hook.context())
        self.assertIsNotNone(hook.layout_context())

    def test_existing_process_exclusions_apply_to_layout_and_text(self):
        hook = self.hooked_context()
        metadata(hook.password_guard)
        hook.settings = Settings(excluded_processes=('chrome.exe',))
        self.assertIsNone(hook.context())
        self.assertIsNone(hook.layout_context())

    def test_shared_browser_scope_requires_uia_id_not_just_win32_handle(self):
        hook = self.hooked_context()
        metadata(hook.password_guard, element='win32:20')
        self.assertIsNone(hook.context())
        metadata(hook.password_guard, element='uia:1,100')
        self.assertIsNotNone(hook.context())

    def test_prewrite_requires_new_safe_sample_and_same_element(self):
        for state, element, allowed in (('safe','uia:1,100',True),
                                        ('password','uia:1,101',False),
                                        ('unknown','',False),
                                        ('safe','uia:1,102',False)):
            with self.subTest(state=state, element=element):
                hook = self.hooked_context()
                metadata(hook.password_guard)
                old = hook.context()
                def refresh():
                    time.sleep(.02)
                    metadata(hook.password_guard, state, element)
                thread = threading.Thread(target=refresh)
                thread.start()
                try:
                    sent, _ = hook._send_guarded_inputs([input_key(65)], lambda:
                        hook.context() is not None and hook.context().password_revision == old.password_revision)
                finally:
                    thread.join()
                self.assertEqual(bool(sent), allowed)
                if not allowed:
                    hook.user32.SendInput.assert_not_called()

    def test_helper_stall_refuses_write_and_hook_never_waits_for_refresh(self):
        hook = self.hooked_context()
        metadata(hook.password_guard)
        revision = hook.context().password_revision
        self.assertFalse(hook.password_guard.revalidate(10, 20, revision, timeout=.02))
        hook._in_keyboard_callback = True
        hook.password_guard.revalidate = Mock()
        sent, reason = hook._send_guarded_inputs([input_key(65)], lambda: True)
        self.assertEqual((sent, reason), (0, 'password-check-requires-worker'))
        hook.password_guard.revalidate.assert_not_called()
        hook.user32.SendInput.assert_not_called()


if __name__ == '__main__':
    unittest.main()
