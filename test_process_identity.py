"""Safety-sensitive process exclusions must survive PID reuse, without hooks."""
import ctypes
import unittest
from unittest.mock import Mock

from typebridge_app import LiveHook


class ProcessIdentityTests(unittest.TestCase):
    def test_one_verified_identity_for_same_host_and_focus_pid(self):
        # Synthetic HWND/PID fixture, no real window or keyboard activity.
        from test_short_words_settings import HostedFocusTests
        hook = HostedFocusTests().fixture()
        def same_owner(hwnd, pid):
            pid._obj.value = 100
            return 11
        hook.user32.GetWindowThreadProcessId.side_effect = same_owner
        hook.process_name = Mock(return_value='notepad.exe')
        self.assertIsNotNone(hook.context())
        hook.process_name.assert_called_once_with(100)

    def test_separate_hosted_processes_keep_both_identity_checks(self):
        from test_short_words_settings import HostedFocusTests
        hook = HostedFocusTests().fixture()
        hook.process_name = Mock(side_effect=lambda pid:
            'applicationframehost.exe' if pid == 100 else 'systemsettings.exe')
        self.assertIsNotNone(hook.context())
        self.assertEqual(hook.process_name.call_count, 2)

    def fixture(self):
        hook = LiveHook.__new__(LiveHook)
        hook.process_cache = {}
        hook.kernel32 = Mock()
        hook.kernel32.OpenProcess.return_value = 123
        current = {'creation': 100, 'image': r'C:\Windows\notepad.exe'}

        def get_times(_handle, created, _exited, _kernel, _user):
            obj = ctypes.cast(created, ctypes.POINTER(ctypes.wintypes.FILETIME)).contents
            obj.dwHighDateTime = 0
            obj.dwLowDateTime = current['creation']
            return 1

        def query_name(_handle, _flags, buf, size):
            buf.value = current['image']
            return 1

        hook.kernel32.GetProcessTimes.side_effect = get_times
        hook.kernel32.QueryFullProcessImageNameW.side_effect = query_name
        return hook, current

    def test_reused_pid_reloads_name_and_honors_password_app(self):
        hook, proc = self.fixture()
        self.assertEqual(hook.process_name(34), 'notepad.exe')
        self.assertEqual(hook.process_name(34), 'notepad.exe')
        self.assertEqual(hook.kernel32.QueryFullProcessImageNameW.call_count, 1)
        proc.update(creation=101, image=r'C:\Program Files\KeePass\KeePass.exe')
        self.assertEqual(hook.process_name(34), 'keepass.exe')
        self.assertEqual(hook.kernel32.QueryFullProcessImageNameW.call_count, 2)

    def test_failed_process_identity_check_does_not_return_cached_name(self):
        hook, _ = self.fixture()
        self.assertEqual(hook.process_name(34), 'notepad.exe')
        hook.kernel32.GetProcessTimes.return_value = 0
        hook.kernel32.GetProcessTimes.side_effect = None
        self.assertEqual(hook.process_name(34), '')
        self.assertNotIn(34, hook.process_cache)
        hook.kernel32.OpenProcess.return_value = 0
        self.assertEqual(hook.process_name(34), '')
