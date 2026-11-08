"""One-shot Edge/UIA capability probe on an INACTIVE disposable Win32 desktop.

No real user fields, input, clipboard, hooks, extensions, driver, injected
code, or foreground switch. One disposable Edge profile and synthetic HTML
document are launched and destroyed in this process. The PowerShell UIA
client only addresses the two synthetic test fields. This is NOT a release
gate: browser/IME/Undo semantics need separate coverage.
"""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import psutil
import subprocess
import sys
import tempfile
import time
import uuid


EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
POWERSHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
PAGE = r'''<!doctype html><meta charset="utf-8"><title>TypeBridge Private R04 Probe</title>
<input id="probe-a" aria-label="TypeBridge Private Field A" value="ghbdtn">
<input id="probe-b" aria-label="TypeBridge Private Field B" value="B_UNCHANGED">
<input id="probe-secret" aria-label="TypeBridge Private Secret" type="password" value="synthetic">
<button aria-label="TypeBridge Focus B" onclick="document.getElementById('probe-b').focus()">Focus B</button>
<button aria-label="TypeBridge Focus A" onclick="document.getElementById('probe-a').focus()">Focus A</button>
<button aria-label="TypeBridge Undo" onclick="document.execCommand('undo')">Undo</button>
<button aria-label="TypeBridge Redo" onclick="document.execCommand('redo')">Redo</button>
'''
PS_SCRIPT = r'''
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new(WINDOW_HWND))
if ($null -eq $root) {throw 'no-edge-uia-root'}
$desc=[System.Windows.Automation.TreeScope]::Descendants
$condition=[System.Windows.Automation.Condition]::TrueCondition
$all=$root.FindAll($desc,$condition)
function Named([string]$name) {
  for($i=0;$i -lt $all.Count;$i++) {
    $e=$all.Item($i)
    if($e.Current.Name -eq $name) {return $e}
  }
  return $null
}
$a=Named 'TypeBridge Private Field A'
$b=Named 'TypeBridge Private Field B'
$secret=Named 'TypeBridge Private Secret'
if ($null -eq $a -or $null -eq $b) {
  @{status='unavailable';reason='synthetic-fields-not-exposed';count=$all.Count} | ConvertTo-Json -Compress
  exit
}
function ReadValue($e) {
  $p=$null
  if (-not $e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$p)) {return @{available=$false}}
  return @{available=$true;value=$p.Current.Value;readonly=$p.Current.IsReadOnly}
}
function Id($e) {return (($e.GetRuntimeId() | ForEach-Object {[string]$_}) -join ',')}
function Invoke($name) {
  $e=Named $name
  $p=$null
  if ($null -eq $e -or -not $e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$p)) {
    return $false
  }
  $p.Invoke()
  return $true
}
$result=@{
  status='ok'
  A_id=Id $a; B_id=Id $b; A_hwnd=$a.Current.NativeWindowHandle; B_hwnd=$b.Current.NativeWindowHandle
  secret_found=($null -ne $secret)
  secret_password=($null -ne $secret -and $secret.Current.IsPassword)
  A_initial=ReadValue $a;B_initial=ReadValue $b
}
$p=$null
if ($a.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$p)) {
  $result['A_setvalue_available']= -not $p.Current.IsReadOnly
  if(-not $p.Current.IsReadOnly) {
    try {
      $p.SetValue('привет')
      $result['A_after_set']=ReadValue $a
      $result['B_after_set']=ReadValue $b
      $result['focus_b_invoked']=Invoke 'TypeBridge Focus B'
      $result['A_after_focus_B']=ReadValue $a
      $result['B_after_focus_B']=ReadValue $b
      $result['undo_invoked']=Invoke 'TypeBridge Undo'
      $result['A_after_undo']=ReadValue $a
      $result['redo_invoked']=Invoke 'TypeBridge Redo'
      $result['A_after_redo']=ReadValue $a
      $result['focus_a_invoked']=Invoke 'TypeBridge Focus A'
      $result['A_final']=ReadValue $a
      $result['B_final']=ReadValue $b
    } catch {
      $result['mutation_error']=$_.Exception.GetType().Name
    }
  }
}
$result | ConvertTo-Json -Compress -Depth 6
'''


def probe():
    if os.name != 'nt' or not EDGE.exists():
        return {'status': 'unavailable', 'reason': 'isolated-Edge-Windows-unavailable'}
    u = ctypes.WinDLL('user32', use_last_error=True)
    u.CreateDesktopW.argtypes = [w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p]
    u.CreateDesktopW.restype = w.HANDLE
    u.CloseDesktop.argtypes, u.CloseDesktop.restype = [w.HANDLE], w.BOOL
    u.EnumDesktopWindows.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_ssize_t]
    u.EnumDesktopWindows.restype = w.BOOL
    u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.IsWindowVisible.argtypes, u.IsWindowVisible.restype = [w.HWND], w.BOOL
    name = 'TypeBridge-R04-Edge-' + uuid.uuid4().hex
    desktop = u.CreateDesktopW(name, None, None, 0, 0x1FF, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    report = {'desktop': 'private-inactive', 'user_foreground_changed': False,
              'global_input': False, 'clipboard': False, 'extensions': False}
    edge = None
    with tempfile.TemporaryDirectory(prefix='typebridge-r04-browser-') as tmp:
        base = Path(tmp)
        profile = base / 'profile'
        page = base / 'case.html'
        page.write_text(PAGE, encoding='utf-8')
        try:
            si = subprocess.STARTUPINFO()
            si.lpDesktop = 'WinSta0\\' + name
            edge = subprocess.Popen([
                str(EDGE), '--user-data-dir=' + str(profile),
                '--no-first-run', '--no-default-browser-check', '--disable-extensions',
                '--disable-background-mode', '--disable-gpu',
                '--force-renderer-accessibility',
                '--app=' + page.as_uri()],
                startupinfo=si, creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report['edge_parent_pid'] = edge.pid
            hwnd = None
            callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, ctypes.c_ssize_t)
            def each(window, _):
                nonlocal hwnd
                cname = ctypes.create_unicode_buffer(256)
                title = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(window, cname, 256)
                u.GetWindowTextW(window, title, 256)
                if cname.value.startswith('Chrome_WidgetWin') and u.IsWindowVisible(window):
                    hwnd = int(window)
                return True
            callback = callback_type(each)
            for _ in range(35):
                u.EnumDesktopWindows(desktop, callback, 0)
                if hwnd:
                    break
                time.sleep(.2)
            if not hwnd:
                report.update(status='unavailable', reason='private-edge-window-not-created')
                return report
            report['private_browser_hwnd'] = hwnd
            si2 = subprocess.STARTUPINFO()
            si2.lpDesktop = 'WinSta0\\' + name
            script = PS_SCRIPT.replace('WINDOW_HWND', str(hwnd))
            completed = subprocess.run([
                str(POWERSHELL), '-NoProfile', '-NonInteractive', '-STA',
                '-EncodedCommand',
                __import__('base64').b64encode(script.encode('utf-16-le')).decode('ascii')],
                startupinfo=si2, creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True, encoding='utf-8-sig', timeout=14)
            if completed.returncode:
                report.update(status='unavailable', reason='private-uia-query-failed',
                              returncode=completed.returncode)
            else:
                try:
                    report['uia'] = json.loads(completed.stdout)
                    report['status'] = report['uia'].get('status')
                except Exception:
                    report.update(status='unavailable', reason='private-uia-output-invalid')
            return report
        finally:
            # Kill ONLY browsers launched with our unique disposable profile.
            # Never kill any user's pre-existing Edge instance.
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    args = proc.info.get('cmdline') or ()
                    if (proc.info.get('name', '').casefold() == 'msedge.exe' and
                            any(str(profile).casefold() in arg.casefold() for arg in args)):
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
                    pass
            if edge is not None:
                try:
                    edge.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    # Only our own root Edge launcher (unique profile).
                    edge.kill()
                    edge.wait()
            u.CloseDesktop(desktop)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
