"""Bounded word-at-caret recovery after a verified UIA field check.

No UIA, input, clipboard or processes on import. The helper reads only the
short caret range of the expected element; it never reads Value/Document text.
"""
from dataclasses import dataclass
import base64
import json
import os
from pathlib import Path
import subprocess


WORD_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class WordRecoveryNative {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int l,t,r,b; }
  [StructLayout(LayoutKind.Sequential)] public struct GUI {
    public uint size,flags;
    public IntPtr active,focus,capture,menu,move,caret;
    public RECT rect;
  }
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern bool GetGUIThreadInfo(uint id,ref GUI g);
  public static long Focus() {
    GUI g=new GUI(); g.size=(uint)Marshal.SizeOf(typeof(GUI));
    return GetGUIThreadInfo(0,ref g) ? g.focus.ToInt64() : 0;
  }
}
'@
function Safe-Target($r,$e) {
  if ($null -eq $e -or [WordRecoveryNative]::GetForegroundWindow().ToInt64() -ne [long]$r.top `
      -or [WordRecoveryNative]::Focus() -ne [long]$r.focus `
      -or -not $e.Equals([System.Windows.Automation.AutomationElement]::FocusedElement)) { return $false }
  $id='uia:' + [string]::Join(',', $e.GetRuntimeId())
  if ($id -cne $r.element_id) { return $false }
  $flag=$e.GetCurrentPropertyValue([System.Windows.Automation.AutomationElement]::IsPasswordProperty,$true)
  if ($flag -isnot [bool] -or $flag) { return $false }
  $ancestor=$e; $inTarget=$false
  for ($i=0; $i -lt 64; $i++) {
    if ($null -eq $ancestor) { return $inTarget }
    if ($ancestor.Current.IsPassword) { return $false }
    if ([long]$ancestor.Current.NativeWindowHandle -eq [long]$r.top) { $inTarget=$true }
    $ancestor=[System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($ancestor)
  }
  return $false
}
try {
  $r=ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
  $e=[System.Windows.Automation.AutomationElement]::FocusedElement
  if (-not (Safe-Target $r $e)) { exit 2 }
  $p=$null
  if (-not $e.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$p)) { exit 2 }
  $ranges=$p.GetSelection()
  if ($null -eq $ranges -or $ranges.Length -ne 1) { exit 2 }
  $start=[System.Windows.Automation.Text.TextPatternRangeEndpoint]::Start
  $end=[System.Windows.Automation.Text.TextPatternRangeEndpoint]::End
  $unit=[System.Windows.Automation.Text.TextUnit]::Character
  $caret=$ranges[0].Clone()
  if ($caret.CompareEndpoints($start,$caret,$end) -ne 0) { exit 2 }
  $left=$caret.Clone(); $right=$caret.Clone()
  $null=$left.MoveEndpointByUnit($start,$unit,-81)
  $null=$right.MoveEndpointByUnit($end,$unit,1)
  if (-not (Safe-Target $r $e)) { exit 2 }
  # Read beyond the accepted size, then reject overflow in Python. A shorter
  # GetText limit could silently truncate the caret end of a Unicode range.
  $before=$left.GetText(163); $after=$right.GetText(5)
  $atStart=($left.CompareEndpoints($start,$p.DocumentRange,$start) -eq 0)
  $again=$p.GetSelection()
  if ($null -eq $again -or $again.Length -ne 1 `
      -or $again[0].CompareEndpoints($start,$caret,$start) -ne 0 `
      -or $again[0].CompareEndpoints($end,$caret,$end) -ne 0 `
      -or -not (Safe-Target $r $e)) { exit 2 }
  if ($left.GetText(163) -cne $before -or $right.GetText(5) -cne $after) { exit 2 }
  if (-not (Safe-Target $r $e)) { exit 2 }
  @{before=$before;after=$after;at_start=$atStart;element_id=$r.element_id} | ConvertTo-Json -Compress
} catch { exit 2 }
'''


@dataclass(frozen=True)
class CaretWord:
    text: str
    keys: tuple
    suffix: str


def parse_caret_word(sample, mapper, language, element_id):
    if not isinstance(sample, dict) or sample.get('element_id') != element_id:
        return None
    before, after = sample.get('before'), sample.get('after')
    if (not isinstance(before, str) or not isinstance(after, str)
            or len(before.encode('utf-16-le')) > 162
            or len(after.encode('utf-16-le')) > 4
            or (after and not after[0].isspace())):
        return None
    end = len(before.rstrip(' \u00a0'))
    suffix = before[end:]
    start = end
    while start and before[start - 1].isalpha():
        start -= 1
    word = before[start:end]
    if (not 1 <= len(word) <= 40 or len(suffix) > 40
            or (start and not before[start - 1].isspace())
            or (not start and sample.get('at_start') is not True)):
        return None
    reverse = {}
    for shift in (False, True):
        for vk in (*range(65, 91), *range(0xBA, 0xC1), *range(0xDB, 0xDF)):
            char = mapper.char(vk, language, shift)
            if len(char) == 1:
                reverse.setdefault(char, []).append((vk, shift))
    if any(len(reverse.get(char, ())) != 1 for char in word):
        return None
    keys = tuple(reverse[char][0] for char in word)
    if mapper.word(list(keys), language) != word:
        return None
    return CaretWord(word, keys, suffix)


def read_caret_word(context, mapper, element_id, valid):
    if not isinstance(element_id, str) or not element_id.startswith('uia:') or not valid():
        return None
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    encoded = base64.b64encode(WORD_SCRIPT.encode('utf-16-le')).decode('ascii')
    request = json.dumps({'top': context.top, 'focus': context.focus, 'element_id': element_id})
    try:
        result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-STA',
                                 '-EncodedCommand', encoded], input=request,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                encoding='utf-8', timeout=2.5,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or len(result.stdout) > 4096 or not valid():
            return None
        return parse_caret_word(json.loads(result.stdout), mapper, context.language, element_id)
    except (OSError, ValueError, TypeError, UnicodeError, subprocess.TimeoutExpired):
        return None
