"""Passive, out-of-process UIA password metadata guard; never reads field text.

The hook reads only a short-lived immutable snapshot. Unknown/stale states deny
processing. UIA focus events are asynchronous; this is not a guarantee against
providers that misreport password state, nor does it uninstall keyboard hooks.
"""
from dataclasses import dataclass
import os
import queue
from pathlib import Path
import subprocess
import threading
import time

# Stream the fixed, local script instead of expanding it into -EncodedCommand:
# UTF-16/base64 can exceed CreateProcess's 32767-character command-line limit.
SCRIPT_BOOTSTRAP = ('[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false); '
                    '& ([ScriptBlock]::Create([Console]::In.ReadToEnd()))')


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
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr hwnd, System.Text.StringBuilder text, int capacity);
    [DllImport("user32.dll", SetLastError=true)] static extern IntPtr GetWindowLongPtrW(IntPtr hwnd, int index);
    [DllImport("kernel32.dll")] static extern void SetLastError(uint code);
    public static int NativeStyleState(string name, long style) {
        name = name.ToLowerInvariant();
        if (name != "edit" && name != "richedit20w" && name != "richedit50w" && name != "richeditd2dpt") return -1;
        return (style & 0x20) != 0 ? 1 : 0;
    }
    static int NativeState(long[] scope) {
        if (scope == null) return -1;
        var name = new System.Text.StringBuilder(128);
        if (GetClassName(new IntPtr(scope[1]), name, name.Capacity) == 0) return -1;
        if (NativeStyleState(name.ToString(), 0) < 0) return -1;
        SetLastError(0);
        long style = GetWindowLongPtrW(new IntPtr(scope[1]), -16).ToInt64();
        if (style == 0 && Marshal.GetLastWin32Error() != 0) return -1;
        return NativeStyleState(name.ToString(), style);
    }
    static bool SharedScope(long[] scope) {
        if (scope == null) return false;
        foreach (long hwnd in scope) {
            var name = new System.Text.StringBuilder(128);
            GetClassName(new IntPtr(hwnd), name, name.Capacity);
            string cls = name.ToString().ToLowerInvariant();
            foreach (string prefix in new string[] { "chrome_", "chromium", "cef", "webview", "mozilla", "ieframe", "internet explorer_server" })
                if (cls.StartsWith(prefix)) return true;
            uint pid; GetWindowThreadProcessId(new IntPtr(hwnd), out pid);
            try {
                using (var process = System.Diagnostics.Process.GetProcessById((int)pid)) {
                    string exe = process.ProcessName.ToLowerInvariant();
                    if (Array.IndexOf(new string[] { "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "msedgewebview2", "webview2", "cefclient", "cefsharp.browsersubprocess" }, exe) >= 0) return true;
                }
            } catch { return true; } // Use element metadata when the owner cannot be resolved.
        }
        return false;
    }
    static bool EditableElement(AutomationElement element) {
        object kind = element.GetCurrentPropertyValue(AutomationElement.ControlTypeProperty, true);
        object enabled = element.GetCurrentPropertyValue(AutomationElement.IsEnabledProperty, true);
        object focused = element.GetCurrentPropertyValue(AutomationElement.HasKeyboardFocusProperty, true);
        if (!(enabled is bool) || !(bool)enabled || !(focused is bool) || !(bool)focused
            || !(kind is ControlType) || ((ControlType)kind != ControlType.Edit && (ControlType)kind != ControlType.Document)) return false;
        object value;
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out value))
            return !((ValuePattern)value).Current.IsReadOnly;
        object text = element.GetCurrentPropertyValue(AutomationElement.IsTextPatternAvailableProperty, true);
        return text is bool && (bool)text;
    }
    static readonly object Sync = new object();
    public static readonly AutoResetEvent Wake = new AutoResetEvent(false);
    public static long Version;
    static AutomationElement watched;
    static AutomationElement verified;
    static bool lastSafe;
    static long lastSafeTop, lastSafeFocus, lastSafeTicks;
    static readonly AutomationFocusChangedEventHandler FocusHandler = (s,e) => FocusChanged(s);
    static readonly AutomationPropertyChangedEventHandler PropertyHandler = (s,e) => Invalidate();
    public static bool FocusNeedsRecheck(bool freshSafe, bool sameElement, bool sameScope,
                                          bool knownPasswordFlag, bool password) {
        return !(freshSafe && sameElement && sameScope && knownPasswordFlag && !password);
    }
    static bool AncestorsUnprotected(AutomationElement element) {
        for (int i = 0; i < 64; i++) {
            if (element == null) return true;
            object flag = element.GetCurrentPropertyValue(AutomationElement.IsPasswordProperty, true);
            if (flag is bool && (bool)flag) return false;
            element = TreeWalker.RawViewWalker.GetParent(element);
        }
        return false;
    }
    static void FocusChanged(object sender) {
        bool recheck = true;
        try {
            AutomationElement element = sender as AutomationElement;
            long[] scope = Scope();
            lock (Sync) {
                long age = DateTime.UtcNow.Ticks - lastSafeTicks;
                bool same = element != null && verified != null && element.Equals(verified)
                            && element.Equals(AutomationElement.FocusedElement);
                object flag = same ? element.GetCurrentPropertyValue(AutomationElement.IsPasswordProperty, true) : null;
                recheck = FocusNeedsRecheck(lastSafe && age >= 0 && age <= TimeSpan.TicksPerMillisecond * 500,
                    same, scope != null && scope[0] == lastSafeTop && scope[1] == lastSafeFocus,
                    flag is bool, flag is bool && (bool)flag);
                if (!recheck) {
                    bool unprotected = AncestorsUnprotected(element);
                    long[] after = Scope();
                    recheck = !unprotected || after == null
                        || after[0] != scope[0] || after[1] != scope[1]
                        || !element.Equals(AutomationElement.FocusedElement);
                }
            }
        } catch { recheck = true; }
        if (recheck) Invalidate();
        else Wake.Set(); // Duplicate focus notification: refresh without discarding the word.
    }
    public static void Invalidate() {
        lock (Sync) { lastSafe = false; Version++; Console.WriteLine("0|0|unknown|" + DateTime.UtcNow.Ticks + "|focus-change"); Console.Out.Flush(); }
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
        string elementId = "";
        AutomationElement checkedElement = null;
        try {
            scope = Scope();
            int native = NativeState(scope);
            if (native >= 0 && !SharedScope(scope)) {
                bool blocked = native == 1;
                try {
                    AutomationElement nativeElement = AutomationElement.FromHandle(new IntPtr(scope[1]));
                    object flag = nativeElement.GetCurrentPropertyValue(AutomationElement.IsPasswordProperty, true);
                    if (flag is bool && (bool)flag) blocked = true;
                } catch { } // Native EDIT metadata remains authoritative when UIA is unsupported.
                long[] after = Scope();
                if (after != null && after[0] == scope[0] && after[1] == scope[1] && NativeState(after) == native) {
                    state = blocked ? "password" : "safe";
                    reason = blocked ? "native-password" : "native-edit-style";
                    elementId = "win32:" + scope[1].ToString();
                } else reason = "focus-changed-during-check";
            } else {
            AutomationElement element = AutomationElement.FocusedElement;
            if (scope == null) reason = "missing-scope";
            else if (element == null) reason = "no-focused-element";
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
                if (!(password is bool)) reason = "password-property-unsupported";
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
                    reason = !inScope ? "scope-mismatch" : !reachedRoot ? "ancestor-limit" : "focus-changed-during-check";
                    if (inScope && reachedRoot && after != null && scope[0] == after[0]
                        && scope[1] == after[1] && element.Equals(AutomationElement.FocusedElement)) {
                        // HWND does not distinguish different DOM inputs. Poll the focused
                        // UIA element's opaque runtime ID even when a focus event is missed.
                        // No ID means no permission to mutate text.
                        int[] runtimeId = element.GetRuntimeId();
                        if (runtimeId != null && runtimeId.Length > 0) {
                            elementId = "uia:" + string.Join(",", runtimeId);
                            state = blocked ? "password" : EditableElement(element) ? "safe" : "unknown";
                            reason = state == "unknown" ? "not-editable-element" : "checked";
                            checkedElement = state == "safe" ? element : null;
                        } else reason = "missing-runtime-id";
                    }
                }
            }
            }
        } catch { state = "unknown"; reason = "provider-error"; }
        if (state == "unknown" && scope != null) {
            var name = new System.Text.StringBuilder(128);
            if (GetClassName(new IntPtr(scope[1]), name, name.Capacity) > 0)
                reason += ":class=" + name.ToString();
        }
        lock(Sync) {
            lastSafe = version == Version && scope != null && state == "safe";
            verified = lastSafe ? checkedElement : null;
            if (lastSafe) { lastSafeTop = scope[0]; lastSafeFocus = scope[1]; lastSafeTicks = started; }
            if (version != Version || scope == null) Console.WriteLine("0|0|unknown|" + started + "|focus-change");
            else Console.WriteLine(scope[0] + "|" + scope[1] + "|" + state + "|" + started + "|" + reason + "|" + elementId);
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
    element_id: str = ''


class PasswordFieldGuard:
    def __init__(self, changed=lambda: None, *, clock=time.monotonic,
                 require_element_id=False, native_only=False, native_fast_path=False):
        self.changed, self.clock = changed, clock
        # The real GUI/headless opt-in must never accept the legacy 3/5-field
        # protocol, because it cannot distinguish different HTML inputs.
        # Legacy model fixtures may still explicitly exercise that protocol.
        self.require_element_id = bool(require_element_id)
        # Kept for constructor compatibility with older harnesses only. No
        # production policy refuses a browser based on this historical flag.
        self.native_only = bool(native_only)
        self.native_fast_path = bool(native_fast_path)
        self.snapshot = Snapshot()
        self.invalidated_at = 0.0
        self.last_response = self.clock()
        self.state_since = self.clock()
        self.unknown_notified = False
        self.stopped = threading.Event()
        self.lock = threading.RLock()
        self.refreshed = threading.Condition(self.lock)
        self.process = None
        self.worker = None

    def _update(self, top, focus, state, reason='checked', *, started=None,
                element_id='', checked_at=None):
        with self.lock:
            if started is not None and started <= self.invalidated_at:
                return
            previous = self.snapshot
            changed = (top, focus, state, element_id) != (
                previous.top, previous.focus, previous.state, previous.element_id)
            if changed:
                self.state_since = self.clock()
                self.unknown_notified = False
            notice_changed = changed or reason != previous.reason
            if state == 'unknown' and not self.unknown_notified and self.clock() - self.state_since >= 2:
                self.unknown_notified = True
                notice_changed = True
            self.snapshot = Snapshot(top, focus, state,
                                     self.clock() if checked_at is None else checked_at,
                                     previous.revision + int(changed), reason, element_id)
            self.refreshed.notify_all()
        if notice_changed:
            self.changed()

    def ingest(self, line):
        started = None
        checked_at = None
        try:
            parts = line.strip().split('|')
            if len(parts) not in (3, 5, 6):
                raise ValueError()
            top, focus, state = parts[:3]
            top, focus = int(top), int(focus)
            if state not in ('safe', 'password', 'unknown') or min(top, focus) < 0:
                raise ValueError()
            reason = parts[4] if len(parts) >= 5 else 'checked'
            element_id = parts[5] if len(parts) == 6 else ''
            if self.require_element_id and state == 'safe' and len(parts) != 6:
                state, reason = 'unknown', 'missing-element-id'
            valid_uia = (element_id.startswith('uia:') and
                         bool(element_id[4:]) and
                         all(item.lstrip('-').isdigit() and item not in ('', '-')
                             for item in element_id[4:].split(',')))
            valid_win32 = (element_id == f'win32:{focus}' and focus > 0)
            if len(parts) == 6 and state == 'safe' and (
                    not element_id or len(element_id) > 256
                    or not (valid_uia or valid_win32)):
                state, reason, element_id = 'unknown', 'missing-element-id', ''
            if len(parts) >= 5:
                started = (int(parts[3]) - 621355968000000000) / 10_000_000
                # A buffered answer started before the click cannot authorize its new field.
                if started <= self.invalidated_at:
                    return
                if len(parts) == 6 and state == 'safe':
                    # The provider can stall before writing a response. A
                    # recently *received* answer is not necessarily recent
                    # metadata: bound the wall-clock age of the query itself.
                    age = time.time() - started
                    if not -0.1 <= age <= 0.5:
                        state, reason, element_id = 'unknown', 'stale-helper-response', ''
                    else:
                        # TTL also starts at query initiation, not at receipt.
                        checked_at = self.clock() - max(0.0, age)
        except (ValueError, TypeError):
            top, focus, state = 0, 0, 'unknown'
            reason = 'protocol-error'
            element_id = ''
        self.last_response = self.clock()
        self._update(top, focus, state, reason, started=started,
                     element_id=element_id, checked_at=checked_at)

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

    def revalidate(self, top, focus, revision, *, timeout=.35):
        """Off-hook check using metadata queried after this request.

        This narrows the asynchronous UIA race; it cannot atomically bind a
        SendInput call to a DOM element. No field contents are read.
        """
        started = self.clock()
        deadline = time.monotonic() + timeout
        with self.refreshed:
            while not self.stopped.is_set():
                permitted = self.permit_revision(top, focus)
                sample = self.snapshot
                if permitted != revision:
                    return False
                # Windows monotonic can have a coarse clock tick: equality
                # is NOT evidence that another provider query has finished.
                if sample.element_id.startswith('win32:') or sample.checked > started:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.refreshed.wait(min(remaining, .05))
        return False

    def _watch(self):
        while not self.stopped.is_set():
            self._watch_once()
            if not self.stopped.is_set():
                self._update(0, 0, 'unknown', 'restarting')
                self.stopped.wait(2)

    def _watch_once(self):
        try:
            executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            self.process = subprocess.Popen([str(executable), '-NoProfile', '-NonInteractive', '-MTA',
                                              '-Command', SCRIPT_BOOTSTRAP], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             text=True, encoding='utf-8', creationflags=0x08000000)
            if self.stopped.is_set():
                return
            self.process.stdin.write(WATCH_SCRIPT)
            self.process.stdin.close()
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
