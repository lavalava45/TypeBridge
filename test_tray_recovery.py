import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from tray_win32 import TrayController
from typebridge_app import TypeBridgeGui
from password_fields import PasswordFieldGuard


class TrayRecoveryTests(unittest.TestCase):
    def test_degraded_tray_explains_partial_native_failure_without_unchecking_toggle(self):
        from tray_win32 import menu_items, MENU_ENABLE
        detail = 'Нативные исправления ограничены для исходного поля'
        rows = menu_items('degraded', detail)
        self.assertIn(detail, rows[0][1])
        self.assertFalse(rows[0][2])
        toggle = next(row for row in rows if row[0] == MENU_ENABLE)
        self.assertTrue(toggle[2])
        self.assertTrue(toggle[3])
        tray = self.fixture()
        tray.run_state = ('degraded', detail)
        tray.hwnd, tray.icons = 123, {'idle':456}
        tray.language = 'idle'
        tray.journal_enabled = False
        self.assertIn('ограничены', tray._icon_data()[-1])

    def test_tray_recovery_command_requires_manual_request(self):
        from tray_win32 import menu_items, MENU_RESTORE_INPUT
        self.assertNotIn(MENU_RESTORE_INPUT, [row[0] for row in menu_items('running')])
        rows = menu_items('degraded', recovery_available=True)
        restore = next(row for row in rows if row[0] == MENU_RESTORE_INPUT)
        self.assertTrue(restore[2])
        self.assertIn('исходное поле', restore[1])

    def test_gui_preserves_active_run_state_while_tray_signals_degradation(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.run_state = 'running'
        gui.last_start_error = ''
        gui.native_quarantine_message = 'Нативные исправления ограничены для исходного поля'
        self.assertEqual(gui._tray_state(), ('degraded', gui.native_quarantine_message))
        gui.native_quarantine_message = ''
        self.assertEqual(gui._tray_state(), ('running',''))

    def fixture(self):
        tray = TrayController.__new__(TrayController)
        tray.closing = False
        tray.ready = threading.Event()
        tray.ready.set()
        tray.worker = Mock()
        tray.worker.is_alive.return_value = True
        tray.hwnd = 123
        tray.notify = Mock()
        tray.last_icon_status = None
        tray.next_recovery = 0.0
        tray.run_state = ('stopped', '')
        tray.language = 'idle'
        tray.icons = {'idle': 456}
        tray.journal_enabled = False
        return tray

    def test_shell_confirmation_can_arrive_on_later_recovery(self):
        tray = self.fixture()
        with patch('tray_win32.icon_registered', side_effect=[False, False, False, True]), \
                patch('tray_win32.win32gui.Shell_NotifyIcon') as shell:
            tray._ensure_icon()
            self.assertFalse(tray.ready.is_set())
            tray._ensure_icon()
        self.assertTrue(tray.ready.is_set())
        self.assertEqual(shell.call_count, 2)
        self.assertEqual(tray.notify.call_args.args[0][0], 'tray-diagnostic')

    def test_pywin32_shell_error_is_retried_without_escaping_callback(self):
        from pywintypes import error
        tray = self.fixture()
        with patch('tray_win32.icon_registered', return_value=False), \
                patch('tray_win32.win32gui.Shell_NotifyIcon',
                      side_effect=error(1460, 'Shell_NotifyIcon', 'timeout')):
            tray._ensure_icon()
        self.assertFalse(tray.ready.is_set())
        self.assertIn('1460', tray.notify.call_args.args[0][1])
        with patch('tray_win32.icon_registered', return_value=True):
            tray._ensure_icon()
        self.assertTrue(tray.ready.is_set())

    def test_registered_icon_is_not_readded_every_recovery_tick(self):
        tray = self.fixture()
        with patch('tray_win32.icon_registered', return_value=True), \
                patch('tray_win32.win32gui.Shell_NotifyIcon') as shell:
            tray._ensure_icon()
            tray._ensure_icon()
        shell.assert_not_called()
        self.assertEqual(tray.notify.call_count, 1)

    def test_recovery_is_throttled_and_only_posts_to_tray_thread(self):
        from tray_win32 import WM_RETRY_ICON
        tray = self.fixture()
        with patch('tray_win32.monotonic', side_effect=[10, 10.5, 12.1]), \
                patch('tray_win32.win32gui.PostMessage') as post:
            tray.maintain()
            tray.maintain()
            tray.maintain()
        self.assertEqual(post.call_count, 2)
        post.assert_called_with(123, WM_RETRY_ICON, 0, 0)

    def test_dead_tray_worker_can_restart_but_closing_tray_cannot(self):
        tray = self.fixture()
        tray.worker.is_alive.return_value = False
        tray.start = Mock()
        with patch('tray_win32.monotonic', return_value=10):
            tray.maintain()
        tray.start.assert_called_once()
        tray.closing = True
        tray.maintain()
        tray.start.assert_called_once()

    def test_taskbar_restart_registers_icon_again(self):
        tray = self.fixture()
        tray.taskbar_created = 9999
        with patch('tray_win32.icon_registered', side_effect=[False, True]), \
                patch('tray_win32.win32gui.Shell_NotifyIcon') as shell:
            tray._wndproc(123, 9999, 0, 0)
        from tray_win32 import win32gui
        shell.assert_called_once_with(win32gui.NIM_ADD, tray._icon_data())
        self.assertTrue(tray.ready.is_set())

    def test_unconfirmed_startup_keeps_message_loop_for_future_retry(self):
        tray = self.fixture()
        tray.clipboard_hotkeys = None
        tray._register_clipboard_hotkeys = Mock()
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch('tray_win32._icon_files', return_value={'idle': Path('idle.ico')}))
            stack.enter_context(patch('tray_win32.icon_registered', return_value=False))
            for api, value in [('RegisterWindowMessage', 9999), ('RegisterClass', 1),
                               ('CreateWindow', 123), ('LoadImage', 456),
                               ('Shell_NotifyIcon', None), ('IsWindow', False),
                               ('UnregisterClass', None), ('DestroyIcon', None)]:
                stack.enter_context(patch('tray_win32.win32gui.' + api, return_value=value))
            pump = stack.enter_context(patch('tray_win32.win32gui.PumpMessages'))
            tray._message_loop()
        pump.assert_called_once()
        tray._register_clipboard_hotkeys.assert_called_once()
        self.assertTrue(all(c.args[0][0] == 'tray-diagnostic' for c in tray.notify.call_args_list))

    def test_missing_shell_icon_still_minimizes_to_recoverable_taskbar(self):
        tray = self.fixture()
        gui = SimpleNamespace(tray=tray, root=Mock(), emit=Mock())
        with patch('tray_win32.win32gui.IsWindow', return_value=True), patch('tray_win32.icon_registered', return_value=False):
            TypeBridgeGui.hide(gui)
        gui.root.withdraw.assert_not_called()
        gui.root.iconify.assert_called_once()

    def test_confirmed_icon_never_withdraws_last_available_window(self):
        tray = self.fixture()
        gui = SimpleNamespace(tray=tray, root=Mock(), emit=Mock())
        with patch('tray_win32.win32gui.IsWindow', return_value=True), patch('tray_win32.icon_registered', return_value=True):
            TypeBridgeGui.hide(gui)
            gui.root.withdraw.assert_not_called()
            gui.root.iconify.assert_called_once()
            tray.worker.is_alive.return_value = False
            self.assertFalse(tray.available())

    def test_repeated_minimize_always_preserves_taskbar_restoration(self):
        tray = self.fixture()
        gui = SimpleNamespace(tray=tray, root=Mock(), emit=Mock())
        with patch('tray_win32.win32gui.IsWindow', return_value=True), patch('tray_win32.icon_registered', return_value=True):
            TypeBridgeGui.hide(gui)
            TypeBridgeGui.hide(gui)
        self.assertEqual(gui.root.iconify.call_count, 2)
        gui.root.withdraw.assert_not_called()

    def test_taskbar_flag_changes_with_language_without_hiding_window(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.root = Mock()
        gui.emit = Mock()
        gui._window_icon_language = None
        files = {k: Path('test') / f'{k}.ico' for k in ('ru', 'en', 'he', 'idle')}
        with patch('tray_win32._icon_files', return_value=files):
            gui._update_taskbar_language_icon('ru')
            gui._update_taskbar_language_icon('ru')
            gui._update_taskbar_language_icon('he')
            gui._update_taskbar_language_icon('unknown')
        self.assertEqual([c.args for c in gui.root.iconbitmap.call_args_list],
                         [(str(files['ru']),), (str(files['he']),),
                          (str(files['idle']),)])
        gui.root.withdraw.assert_not_called()

    def test_autostart_does_not_withdraw_settings_without_recovery(self):
        from typebridge_app import main
        root, gui = Mock(), Mock()
        with patch('typebridge_app.tk.Tk', return_value=root), \
                patch('typebridge_app.TypeBridgeGui', return_value=gui), \
                patch('typebridge_app.migrate_user_data'):
            self.assertEqual(main(['--autostart']), 0)
        root.withdraw.assert_not_called()
        root.after.assert_called_once_with(1200, gui.hide)

    def test_unknown_own_field_does_not_permanently_poison_editor_permission(self):
        guard = PasswordFieldGuard(clock=lambda: 10)
        guard.ingest('90|100|safe')
        revision = guard.permit_revision(90, 100)
        guard.invalidate()
        guard.ingest('200|201|unknown')  # Own settings/log: processing is denied.
        self.assertIsNone(guard.permit_revision(90, 100))
        guard.ingest('90|100|safe')  # Returned to editor, fresh verification.
        self.assertIsNotNone(guard.permit_revision(90, 100))
        self.assertNotEqual(guard.permit_revision(90, 100), revision)
        guard.ingest('90|100|password')
        self.assertIsNone(guard.permit_revision(90, 100))

    def test_disappeared_tray_restores_hidden_window_and_keeps_timer(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.closing = False
        gui.tray = Mock()
        gui.tray.available.return_value = False
        gui.tray.observe_foreground.side_effect = OSError('Explorer restarted')
        gui.root = Mock()
        gui.root.state.return_value = 'withdrawn'
        gui.emit = Mock()
        gui.last_tray_language = 'en'
        gui.caret_indicator = None
        with patch('typebridge_app.foreground_language', return_value='en'):
            gui._update_tray()
        gui.root.deiconify.assert_called_once()
        gui.root.lift.assert_not_called()
        gui.root.after.assert_called_once_with(500, gui._update_tray)
        gui.tray.maintain.assert_called_once()


if __name__ == '__main__':
    unittest.main()
