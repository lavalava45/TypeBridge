"""Passive, out-of-process UIA password metadata guard; never reads field text.

The hook reads only a short-lived immutable snapshot. Unknown/stale states deny
processing. UIA focus events are asynchronous; this is not a guarantee against
providers that misreport password state, nor does it uninstall keyboard hooks.
"""
import base64
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import threading
import time


WATCH_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase
$refs = @([System.Windows.Automation.AutomationElement].Assembly.Location,
          [System.Windows.Automation.AutomationProperty].Assembly.Location,
          [System.Windows.Point].Assembly.Location)
Add-Type -ReferencedAssemblies $refs -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;
public static class PasswordMetadata {
    [StructLayout(LayoutKind.Sequential)] struct Rect { public int L,T,R,B; }
    [StructLayout(LayoutKind.Sequential)] struct GUI {
        public int Size, Flags;
        public IntPtr Active, Focus, Capture, Menu, Move, Caret;
        public Rect Rectangle;
    }
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool GetGUIThreadInfo(uint tid, ref GUI info);
    static readonly object Sync = new object();
    public static readonly AutoResetEvent Wake = new AutoResetEvent(false);
    public static long Version;
    static AutomationElement watched;
    static readonly AutomationFocusChangedEventHandler FocusHandler = (s,e) => Invalidate();
    static readonly AutomationPropertyChangedEventHandler PropertyHandler = (s,e) => Invalidate();
    public static void Invalidate() {
        lock (Sync) { Version++; Console.WriteLine("0|0|unknown"); Console.Out.Flush(); }
        Wake.Set();
    }
    static long[] Scope() {
        IntPtr top = GetForegroundWindow(); uint pid;
        uint tid = GetWindowThreadProcessId(top, out pid);
        GUI info = new GUI(); info.Size = Marshal.SizeOf(typeof(GUI));
        if (top == IntPtr.Zero || tid == 0 || !GetGUIThreadInfo(tid, ref info)) return null;
        return new long[] { top.ToInt64(), (info.Focus == IntPtr.Zero ? top : info.Focus).ToInt64() };
    }
    public static void Start() { Automation.AddAutomationFocusChangedEventHandler(FocusHandler); }
    public static void Stop() {
        Automation.RemoveAutomationFocusChangedEventHandler(FocusHandler);
        if (watched != null) Automation.RemoveAutomationPropertyChangedEventHandler(watched, PropertyHandler);
    }
    public static void Query() {
        long version; lock(Sync) { version = Version; }
        long[] scope = null; string state = "unknown";
        try {
            scope = Scope(); AutomationElement element = AutomationElement.FocusedElement;
            if (scope != null && element != null) {
                if (watched == null || !watched.Equals(element)) {
                    if (watched != null) {
                        try { Automation.RemoveAutomationPropertyChangedEventHandler(watched, PropertyHandler); }
                        catch { } // A disappearing old field must not prevent checking the new one.
                    }
                    watched = null;
                    Automation.AddAutomationPropertyChangedEventHandler(element, TreeScope.Element,
                        PropertyHandler, AutomationElement.IsPasswordProperty);
                    watched = element;
                }
                object password = element.GetCurrentPropertyValue(AutomationElement.IsPasswordProperty, true);
                if (password is bool) {
                    bool blocked = (bool)password; bool inScope = false; bool reachedRoot = false;
                    AutomationElement ancestor = element;
                    for (int i = 0; i < 64; i++) {
                        if (ancestor == null) { reachedRoot = true; break; }
                        object flag = ancestor.GetCurrentPropertyValue(AutomationElement.IsPasswordProperty, true);
                        if (flag is bool && (bool)flag) blocked = true;
                        long hwnd = (long)(uint)ancestor.Current.NativeWindowHandle;
                        if (hwnd == scope[0] || hwnd == scope[1]) inScope = true;
                        ancestor = TreeWalker.RawViewWalker.GetParent(ancestor);
                    }
                    long[] after = Scope();
                    if (inScope && reachedRoot && after != null && scope[0] == after[0]
                        && scope[1] == after[1] && element.Equals(AutomationElement.FocusedElement))
                        state = blocked ? "password" : "safe";
                }
            }
        } catch { state = "unknown"; }
        lock(Sync) {
            if (version != Version || scope == null) Console.WriteLine("0|0|unknown");
            else Console.WriteLine(scope[0] + "|" + scope[1] + "|" + state);
            Console.Out.Flush();
        }
    }
}
'@
try {
    [PasswordMetadata]::Start()
    while ($true) {
        [PasswordMetadata]::Query()
        [void][PasswordMetadata]::Wake.WaitOne(100)
    }
} finally { [PasswordMetadata]::Stop() }
'''


@dataclass(frozen=True)
class Snapshot:
    top: int = 0
    focus: int = 0
    state: str = 'unknown'
    checked: float = 0.0
    revision: int = 0


class PasswordFieldGuard:
    def __init__(self, changed=lambda: None, *, clock=time.monotonic):
        self.changed, self.clock = changed, clock
        self.snapshot = Snapshot()
        self.not_before = 0.0
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self.worker = None

    def _update(self, top, focus, state):
        with self.lock:
            previous = self.snapshot
            changed = (top, focus, state) != (previous.top, previous.focus, previous.state)
            self.snapshot = Snapshot(top, focus, state, self.clock(), previous.revision + int(changed))
        if changed:
            self.changed()

    def ingest(self, line):
        try:
            top, focus, state = line.strip().split('|')
            top, focus = int(top), int(focus)
            if state not in ('safe', 'password', 'unknown') or min(top, focus) < 0:
                raise ValueError()
        except (ValueError, TypeError):
            top, focus, state = 0, 0, 'unknown'
        self._update(top, focus, state)

    def invalidate(self):
        self.not_before = self.clock() + .15
        self._update(0, 0, 'unknown')

    def permit_revision(self, top, focus):
        snapshot = self.snapshot
        now = self.clock()
        if (snapshot.state == 'safe' and (snapshot.top, snapshot.focus) == (top, focus)
                and self.not_before <= now and 0 <= now - snapshot.checked <= .5):
            return snapshot.revision
        if snapshot.state == 'safe' and now - snapshot.checked > .5:
            self._update(0, 0, 'unknown')
        return None

    def start(self):
        if self.worker is not None:
            return
        self.worker = threading.Thread(target=self._watch, name='password-metadata', daemon=True)
        self.worker.start()

    def _watch(self):
        try:
            executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            encoded = base64.b64encode(WATCH_SCRIPT.encode('utf-16-le')).decode('ascii')
            self.process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-MTA',
                                              '-EncodedCommand', encoded], stdin=subprocess.DEVNULL,
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             text=True, encoding='utf-8', creationflags=0x08000000)
            if self.stopped.is_set():
                return
            for line in self.process.stdout:
                if self.stopped.is_set():
                    break
                self.ingest(line)
        except (OSError, UnicodeError):
            self.changed()
        finally:
            self._update(0, 0, 'unknown')
            self._terminate()

    def _terminate(self):
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
            except OSError:
                pass

    def close(self):
        self.stopped.set()
        self._terminate()
        self._update(0, 0, 'unknown')
