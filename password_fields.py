"""Passive, out-of-process UIA password metadata guard; never reads field text.

The hook reads only a short-lived immutable snapshot. Unknown/stale states deny
processing. UIA focus events are asynchronous; this is not a guarantee against
providers that misreport password state, nor does it uninstall keyboard hooks.
"""
import base64
from dataclasses import dataclass
import os
import queue
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
        lock (Sync) { Version++; Console.WriteLine("0|0|unknown|" + DateTime.UtcNow.Ticks + "|focus-change"); Console.Out.Flush(); }
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
        long started = DateTime.UtcNow.Ticks;
        long version; lock(Sync) { version = Version; }
        long[] scope = null; string state = "unknown"; string reason = "unsupported-field";
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
                        { state = blocked ? "password" : "safe"; reason = "checked"; }
                }
            }
        } catch { state = "unknown"; reason = "provider-error"; }
        lock(Sync) {
            if (version != Version || scope == null) Console.WriteLine("0|0|unknown|" + started + "|focus-change");
            else Console.WriteLine(scope[0] + "|" + scope[1] + "|" + state + "|" + started + "|" + reason);
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
    reason: str = 'starting'


class PasswordFieldGuard:
    def __init__(self, changed=lambda: None, *, clock=time.monotonic):
        self.changed, self.clock = changed, clock
        self.snapshot = Snapshot()
        self.invalidated_at = 0.0
        self.last_response = self.clock()
        self.state_since = self.clock()
        self.unknown_notified = False
        self.stopped = threading.Event()
        self.lock = threading.RLock()
        self.process = None
        self.worker = None

    def _update(self, top, focus, state, reason='checked', *, started=None):
        with self.lock:
            if started is not None and started <= self.invalidated_at:
                return
            previous = self.snapshot
            changed = (top, focus, state) != (previous.top, previous.focus, previous.state)
            if changed:
                self.state_since = self.clock()
                self.unknown_notified = False
            notice_changed = changed or reason != previous.reason
            if state == 'unknown' and not self.unknown_notified and self.clock() - self.state_since >= 2:
                self.unknown_notified = True
                notice_changed = True
            self.snapshot = Snapshot(top, focus, state, self.clock(), previous.revision + int(changed), reason)
        if notice_changed:
            self.changed()

    def ingest(self, line):
        started = None
        try:
            parts = line.strip().split('|')
            if len(parts) not in (3, 5):
                raise ValueError()
            top, focus, state = parts[:3]
            top, focus = int(top), int(focus)
            if state not in ('safe', 'password', 'unknown') or min(top, focus) < 0:
                raise ValueError()
            reason = parts[4] if len(parts) == 5 else 'checked'
            if len(parts) == 5:
                started = (int(parts[3]) - 621355968000000000) / 10_000_000
                # A buffered answer started before the click cannot authorize its new field.
                if started <= self.invalidated_at:
                    return
        except (ValueError, TypeError):
            top, focus, state = 0, 0, 'unknown'
            reason = 'protocol-error'
        self.last_response = self.clock()
        self._update(top, focus, state, reason, started=started)

    def invalidate(self):
        with self.lock:
            self.invalidated_at = time.time()
            self._update(0, 0, 'unknown', 'focus-change')

    def permit_revision(self, top, focus):
        snapshot = self.snapshot
        now = self.clock()
        if (snapshot.state == 'safe' and (snapshot.top, snapshot.focus) == (top, focus)
                and 0 <= now - snapshot.checked <= .5):
            return snapshot.revision
        if snapshot.state == 'safe' and now - snapshot.checked > .5:
            self._update(0, 0, 'unknown', 'stale')
        return None

    def start(self):
        if self.worker is not None:
            return
        self.worker = threading.Thread(target=self._watch, name='password-metadata', daemon=True)
        self.worker.start()

    def _watch(self):
        while not self.stopped.is_set():
            self._watch_once()
            if not self.stopped.is_set():
                self._update(0, 0, 'unknown', 'restarting')
                self.stopped.wait(2)

    def _watch_once(self):
        try:
            executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            encoded = base64.b64encode(WATCH_SCRIPT.encode('utf-16-le')).decode('ascii')
            self.process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-MTA',
                                              '-EncodedCommand', encoded], stdin=subprocess.DEVNULL,
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             text=True, encoding='utf-8', creationflags=0x08000000)
            if self.stopped.is_set():
                return
            replies = queue.Queue()
            process = self.process
            def read_lines():
                try:
                    for line in process.stdout:
                        replies.put(line)
                finally:
                    replies.put(None)
            reader = threading.Thread(target=read_lines, name='password-replies', daemon=True)
            reader.start()
            self.last_response = self.clock()
            received = False
            while not self.stopped.is_set():
                try:
                    line = replies.get(timeout=.2)
                except queue.Empty:
                    if self.clock() - self.last_response > (3 if received else 10):
                        self._update(0, 0, 'unknown', 'helper-timeout')
                        return
                    continue
                if line is None:
                    self._update(0, 0, 'unknown', 'helper-exited')
                    return
                self.ingest(line)
                received = True
        except (OSError, UnicodeError):
            self._update(0, 0, 'unknown', 'helper-failed')
        finally:
            self._terminate()

    def _terminate(self):
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            except OSError:
                pass

    def close(self):
        self.stopped.set()
        self._terminate()
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)
        self._update(0, 0, 'unknown', 'stopped')
