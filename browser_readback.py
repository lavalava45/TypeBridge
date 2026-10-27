"""Opt-in browser caret samples. Read-only UIA in a disposable helper process.

No process, window, hook, clipboard or UIA access at import. A single background
worker warms the helper once and serializes bounded requests. Provider hangs kill
only that helper. A sample is deferred evidence, never document verification.
"""
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from time import monotonic


CARET_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class BrowserProbeNative {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int l,t,r,b; }
  [StructLayout(LayoutKind.Sequential)] public struct GUI {
    public uint cbSize, flags;
    public IntPtr active, focus, capture, menu, move, caret;
    public RECT rect;
  }
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern bool GetGUIThreadInfo(uint id, ref GUI gui);
  public static long Focus() {
    GUI g = new GUI(); g.cbSize = (uint)Marshal.SizeOf(typeof(GUI));
    return GetGUIThreadInfo(0, ref g) ? g.focus.ToInt64() : 0;
  }
}
'@
function Check-Window($r) {
  return ([BrowserProbeNative]::GetForegroundWindow().ToInt64() -eq [long]$r.top `
      -and [BrowserProbeNative]::Focus() -eq [long]$r.focus)
}
function Read-Caret($r) {
  if (-not (Check-Window $r)) { return @{state='skipped';reason='window-changed'} }
  $e = [System.Windows.Automation.AutomationElement]::FocusedElement
  if ($null -eq $e) { return @{state='skipped';reason='no-focused-element'} }
  $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
  $ancestor = $e; $inTarget = $false; $safe = $false
  for ($i=0; $i -lt 64; $i++) {
    if ($null -eq $ancestor) { $safe=$true; break }
    if ($ancestor.Current.IsPassword) { return @{state='skipped';reason='password-field'} }
    if ([long]$ancestor.Current.NativeWindowHandle -eq [long]$r.top) { $inTarget=$true }
    $ancestor = $walker.GetParent($ancestor)
  }
  if (-not $safe -or -not $inTarget) { return @{state='skipped';reason='uia-target-unconfirmed'} }
  $pattern = $null
  if (-not $e.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$pattern)) {
    return @{state='skipped';reason='text-pattern-unavailable'}
  }
  $ranges = $pattern.GetSelection()
  if ($null -eq $ranges -or $ranges.Length -ne 1) {
    return @{state='skipped';reason='caret-unavailable'}
  }
  $range = $ranges[0].Clone()
  $start = [System.Windows.Automation.Text.TextPatternRangeEndpoint]::Start
  $end = [System.Windows.Automation.Text.TextPatternRangeEndpoint]::End
  if ($range.CompareEndpoints($start,$range,$end) -ne 0) {
    return @{state='skipped';reason='selection-not-collapsed'}
  }
  $null = $range.MoveEndpointByUnit($start,[System.Windows.Automation.Text.TextUnit]::Character,-60)
  $text = $range.GetText(60)
  if (-not (Check-Window $r) -or -not $e.Equals([System.Windows.Automation.AutomationElement]::FocusedElement)) {
    return @{state='skipped';reason='focus-changed-during-read'}
  }
  if ($e.Current.IsPassword) { return @{state='skipped';reason='password-field'} }
  return @{state='sampled';observed=$text}
}
[Console]::WriteLine('{"state":"ready"}')
while ($null -ne ($line = [Console]::ReadLine())) {
  $r = $null
  try {
    $r = ConvertFrom-Json -InputObject $line
    $reply = Read-Caret $r
  } catch {
    $reply = @{state='failed';reason='uia-provider-error'}
  }
  $reply.id = $r.id
  [Console]::WriteLine((ConvertTo-Json -InputObject $reply -Compress))
}
'''


class ReadbackFailure(Exception):
    pass


class CaretReader:
    """One persistent PowerShell process; no reads until an explicit request."""
    def __init__(self):
        exe = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        encoded = base64.b64encode(CARET_SCRIPT.encode('utf-16-le')).decode('ascii')
        self.process = subprocess.Popen(
            [str(exe), '-NoProfile', '-NonInteractive', '-STA', '-EncodedCommand', encoded],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.responses = queue.Queue(maxsize=8)
        self.counter = 0
        self.ready = False
        threading.Thread(target=self._read_lines, name='BrowserCaretPipe', daemon=True).start()

    def warm(self):
        if self.ready:
            return
        try:
            if self._reply(6).get('state') != 'ready':
                raise ReadbackFailure('helper-not-ready')
            self.ready = True
        except Exception:
            self.close()
            raise

    def _read_lines(self):
        try:
            for line in self.process.stdout:
                try:
                    value = json.loads(line) if len(line) <= 8192 else {}
                    self.responses.put_nowait(value)
                except (ValueError, queue.Full):
                    pass
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.responses.put_nowait(None)
            except queue.Full:
                pass

    def _reply(self, timeout):
        try:
            value = self.responses.get(timeout=timeout)
        except queue.Empty:
            raise ReadbackFailure('helper-timeout') from None
        if not isinstance(value, dict):
            raise ReadbackFailure('helper-exited')
        return value

    def read(self, context):
        self.warm()
        self.counter += 1
        request = {'id': self.counter, 'top': int(context.top), 'focus': int(context.focus)}
        try:
            self.process.stdin.write(json.dumps(request) + '\n')
            self.process.stdin.flush()
            value = self._reply(.9)
        except (OSError, ValueError):
            raise ReadbackFailure('helper-pipe-error') from None
        if value.get('id') != self.counter:
            raise ReadbackFailure('helper-response-mismatch')
        if value.get('state') == 'sampled' and not isinstance(value.get('observed'), str):
            raise ReadbackFailure('helper-invalid-text')
        if 'observed' in value:
            value['observed'] = value['observed'][-60:]
        return value

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()

    def cancel(self):
        if self.process.poll() is None:
            self.process.kill()


class BrowserReadback:
    """One queue, one worker. Diagnostic failures never touch correction state."""
    def __init__(self, notify, reader_factory=CaretReader, clock=monotonic):
        self.notify, self.reader_factory, self.clock = notify, reader_factory, clock
        self.jobs = queue.Queue(maxsize=4)
        self.closed = threading.Event()
        self.reader = None
        self.retry_after = 0
        self.worker = None

    def start(self):
        self.worker = threading.Thread(target=self._run, name='BrowserReadback', daemon=True)
        self.worker.start()

    def request(self, hook, context, details, revision, created):
        job = (hook, context, dict(details), revision, created)
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            self._deliver(job, {'state': 'skipped', 'reason': 'readback-queue-full'})

    def _valid(self, job):
        hook, context, _, revision, _ = job
        if (self.closed.is_set() or hook.stopping or not hook.debug_words
                or getattr(hook, '_browser_field_revision', 0) != revision):
            return False
        current = hook.context()
        return current is not None and hook._word_scope(current) == hook._word_scope(context)

    def _deliver(self, job, result, elapsed=0):
        hook, context, details, _, created = job
        payload = {**details, 'state': result.get('state', 'failed'),
                   'reason': result.get('reason', ''), 'transport': 'uia-text',
                   'delay_ms': str(max(0, int((self.clock() - created) * 1000))),
                   'read_ms': str(max(0, int(elapsed * 1000)))}
        if result.get('state') == 'sampled':
            payload['observed'] = result['observed'][-60:]
        self.notify((hook, context, payload))

    def _read_job(self, job):
        read_started = None
        result = {'state': 'skipped', 'reason': 'context-changed-before-read'}
        try:
            # Let the held physical callback return and the browser process
            # the submitted input. This wait is on the diagnostic worker only.
            self.closed.wait(max(0, .08 - (self.clock() - job[4])))
            if self.clock() - job[4] > 1.5:
                result = {'state': 'skipped', 'reason': 'readback-request-stale'}
            elif self._valid(job):
                if self.reader is None:
                    if self.clock() < self.retry_after:
                        self._deliver(job, {'state': 'skipped', 'reason': 'helper-restarting'})
                        return
                    self._make_reader()
                if self.clock() - job[4] > 1.5:
                    result = {'state': 'skipped', 'reason': 'readback-request-stale'}
                elif self._valid(job):
                    read_started = self.clock()
                    result = self.reader.read(job[1])
                    if not self._valid(job):
                        result = {'state': 'skipped', 'reason': 'context-changed-during-read'}
        except Exception as error:
            reason = str(error) if isinstance(error, ReadbackFailure) else 'readback-error'
            result = {'state': 'failed', 'reason': reason}
            self._reset_reader()
            self.retry_after = self.clock() + 2
        self._deliver(job, result, 0 if read_started is None else self.clock() - read_started)

    def _reset_reader(self):
        reader, self.reader = self.reader, None
        if reader:
            try:
                reader.close()
            except Exception:
                pass

    def _make_reader(self):
        self.reader = self.reader_factory()
        warm = getattr(self.reader, 'warm', None)
        if warm:
            warm()

    def _run(self):
        try:
            # Warm while dictionaries are loading, not on the first keystroke.
            try:
                self._make_reader()
            except Exception:
                self._reset_reader()
                self.retry_after = self.clock() + 2
            while not self.closed.is_set():
                try:
                    job = self.jobs.get(timeout=.1)
                except queue.Empty:
                    continue
                self._read_job(job)
        finally:
            self._reset_reader()

    def close(self):
        self.closed.set()
        reader = self.reader
        if reader:
            cancel = getattr(reader, 'cancel', None)
            if cancel:
                try:
                    cancel()
                except Exception:
                    pass
        # Do not join or wait on the GUI thread. In-flight reads have a fixed
        # timeout; the daemon worker tears down its helper in finally.
