"""R04 UIA ValuePattern experiment on two disposable native EDIT controls.

No user windows, global hooks, physical keys, clipboard, browser extensions,
VM, injection, SetForegroundWindow or SwitchDesktop. Only the child process's
own native controls on an inactive desktop are modified. This proves the
semantics of this ONE Windows provider, not Chrome's UIA provider.
"""
import base64
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parent.parent
PS = r'''
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$a=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new(A_HANDLE))
$b=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new(B_HANDLE))
$p=$null
$q=$null
$data=@{status='unavailable'; A_is_password=$a.Current.IsPassword;
        B_is_password=$b.Current.IsPassword;
        A_id=(($a.GetRuntimeId() | ForEach-Object { [string]$_ }) -join ',');
        B_id=(($b.GetRuntimeId() | ForEach-Object { [string]$_ }) -join ',')}
if ($a.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$p)) {
  $data['A_readonly']=$p.Current.IsReadOnly
  if(-not $p.Current.IsReadOnly) {
    $data['A_initial']=$p.Current.Value
    $data['status']='supported'
    $p.SetValue('привет')
    $data['A_after']=$p.Current.Value
    if($b.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$q)) {
      $data['B_after']=$q.Current.Value
    }
  }
}
$data | ConvertTo-Json -Compress -Depth 4
'''


def probe():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user32.OpenDesktopW.argtypes, user32.OpenDesktopW.restype = [
        w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    user32.GetThreadDesktop.argtypes, user32.GetThreadDesktop.restype = [w.DWORD], w.HANDLE
    user32.SetThreadDesktop.argtypes, user32.SetThreadDesktop.restype = [w.HANDLE], w.BOOL
    user32.CloseDesktop.argtypes = [w.HANDLE]
    user32.SendMessageTimeoutW.argtypes = [
        w.HWND, w.UINT, w.WPARAM, ctypes.c_ssize_t,
        w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
    user32.SendMessageTimeoutW.restype = ctypes.c_size_t
    kernel.GetCurrentThreadId.restype = w.DWORD
    original_desktop = user32.GetThreadDesktop(kernel.GetCurrentThreadId())
    child = subprocess.Popen(
        [sys.executable, str(ROOT / 'early_native_harness.py'), '--worker', '--edit-fixture'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    desktop = None
    attached = False
    report = {'desktop': 'private-inactive', 'mutation': 'UIA-ValuePattern',
              'global_input': False, 'user_clipboard': False, 'user_focus_changed': False}
    def msg(hwnd, message, wp=0, lp=0):
        output = ctypes.c_size_t()
        if not user32.SendMessageTimeoutW(hwnd, message, wp, lp,
                                          0x23, 500, ctypes.byref(output)):
            raise TimeoutError('isolated-window-message-' + str(message))
        return output.value
    def get(hwnd):
        buf = ctypes.create_unicode_buffer(512)
        msg(hwnd, 0xD, len(buf), ctypes.addressof(buf))
        return buf.value
    try:
        notices = queue.Queue()
        threading.Thread(target=lambda: notices.put(child.stdout.readline()), daemon=True).start()
        ready = json.loads(notices.get(timeout=7))
        if 'error' in ready:
            raise RuntimeError(ready['error'])
        desktop = user32.OpenDesktopW(ready['desktop'], 0, False, 0x1FF)
        if not desktop or not user32.SetThreadDesktop(desktop):
            raise ctypes.WinError(ctypes.get_last_error())
        attached = True
        a, b = ready['focus'], ready['other']
        for hwnd, content in ((a, 'ghbdtn'), (b, 'B_UNCHANGED')):
            seed = ctypes.create_unicode_buffer(content)
            msg(hwnd, 0xC, 0, ctypes.addressof(seed))
            msg(hwnd, 0xCD)
        report['A_before'], report['B_before'] = get(a), get(b)
        script = PS.replace('A_HANDLE', str(a)).replace('B_HANDLE', str(b))
        startup = subprocess.STARTUPINFO()
        startup.lpDesktop = 'WinSta0\\' + ready['desktop']
        ps = Path(__import__('os').environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(ps), '-NoProfile', '-NonInteractive', '-STA',
            '-EncodedCommand', base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
            startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True, text=True, encoding='utf-8-sig', timeout=9)
        if result.returncode:
            report['provider_status'] = 'unavailable'
            report['provider_exit_code'] = result.returncode
            # Synthetic test only; do not emit any real user document data.
            report['provider_error'] = result.stderr[-500:]
        else:
            report['uia'] = json.loads(result.stdout)
            report['provider_status'] = report['uia'].get('status')
        report['A_after'], report['B_after'] = get(a), get(b)
        # An EDIT can report successful Undo after a prior WM_SETTEXT even
        # when UIA mutation never happened. Never mark that as proven Undo.
        report['A_mutated_by_uia'] = (
            report.get('provider_status') == 'supported'
            and report['A_after'] == 'привет')
        report['A_undo_return'] = msg(a, 0xC7)  # EM_UNDO on own EDIT.
        report['A_after_undo'], report['B_after_undo'] = get(a), get(b)
        report['other_unchanged'] = report['B_after'] == 'B_UNCHANGED' == report['B_after_undo']
        report['undo_tested'] = bool(report['A_mutated_by_uia'])
        report['undo_restored'] = (report['A_after_undo'] == 'ghbdtn'
                                   if report['undo_tested'] else None)
        return report
    finally:
        if attached:
            user32.SetThreadDesktop(original_desktop)
        if desktop:
            user32.CloseDesktop(desktop)
        try:
            child.communicate('\n', timeout=4)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
