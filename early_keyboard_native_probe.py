"""Background receiver experiment, NOT SendInput or physical typing evidence.

Uses a private inactive Windows desktop. Feeds the control-level effects of
the same plan (Backspace + Unicode) to EDIT and RichEdit in another process.
It tests receiver behavior/Undo only; it cannot establish the input-queue order.
"""
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

from early_keyboard_probe import Field, plan
from early_native_edit import utf8_position


def probe(field_class):
    u = ctypes.WinDLL('user32', use_last_error=True)
    k = ctypes.WinDLL('kernel32')
    u.GetThreadDesktop.argtypes, u.GetThreadDesktop.restype = [w.DWORD], w.HANDLE
    u.OpenDesktopW.argtypes, u.OpenDesktopW.restype = [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    u.SetThreadDesktop.argtypes, u.SetThreadDesktop.restype = [w.HANDLE], w.BOOL
    u.CloseDesktop.argtypes = [w.HANDLE]
    u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, ctypes.c_ssize_t,
                                      w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
    u.SendMessageTimeoutW.restype = ctypes.c_size_t
    original = u.GetThreadDesktop(k.GetCurrentThreadId())
    desktop = None
    command = [sys.executable, str(Path(__file__).with_name('early_native_harness.py')), '--worker']
    if field_class == 'EDIT': command.append('--edit-fixture')
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        startup = queue.Queue()
        threading.Thread(target=lambda: startup.put(child.stdout.readline()), daemon=True).start()
        ready = json.loads(startup.get(timeout=7))
        if 'error' in ready: raise RuntimeError(ready['error'])
        desktop = u.OpenDesktopW(ready['desktop'], 0, False, 0x1FF)
        if not desktop or not u.SetThreadDesktop(desktop): raise ctypes.WinError(ctypes.get_last_error())
        def msg(command, wp=0, lp=0):
            answer = ctypes.c_size_t()
            if not u.SendMessageTimeoutW(ready['focus'], command, wp, lp, 3, 500, ctypes.byref(answer)):
                raise TimeoutError('private-receiver-message')
            return answer.value
        def text():
            length = msg(0xE)
            value = ctypes.create_unicode_buffer(length+1)
            msg(0xD, length+1, ctypes.addressof(value))
            return value.value.replace('\r\n', '\r')
        checks = []
        for source, typed, replacement, target in (
                ('ru', 'руддщ', 'hello', 'en'), ('he', 'יקךךם', 'hello', 'en'),
                ('en', 'ghbdtn', 'привет', 'ru'), ('he', 'עינגאמ', 'привет', 'ru'),
                ('en', 'akuo', 'שלום', 'he'), ('ru', 'флгщ', 'שלום', 'he')):
            initial = '🙂\r' + typed[:-1] + ' end'
            field = Field((ready['top'], ready['focus']), initial, len(typed)+1, source)
            operation = plan(field, typed, replacement, target)
            value = ctypes.create_unicode_buffer(initial)
            msg(0xC, 0, ctypes.addressof(value))
            caret = utf8_position(initial, len(initial[:field.caret].encode()))
            msg(0xB1, caret, caret)
            msg(0xCD)  # Clear undo only in this disposable fixture.
            for key in operation.events:
                if key.vk:
                    msg(0x101 if key.flags & 2 else 0x100, key.vk, 1)
                    if key.vk == 8 and not key.flags & 2: msg(0x102, 8, 1)
                elif not key.flags & 2:
                    msg(0x102, key.scan, 1)
            after = text()
            if after != operation.expected.text: raise AssertionError('receiver-text-mismatch')
            msg(0xC7)
            undo = text()
            checks.append({'source': source, 'target': target, 'text_matches': True,
                           'undo_restores_full_typed_word': undo == '🙂\r'+typed+' end',
                           'undo_restores_delivered_prefix': undo == initial,
                           'fixture_text_after_undo': undo})
        return {'field_class': field_class, 'receiver_cases': checks}
    finally:
        u.SetThreadDesktop(original)
        if desktop: u.CloseDesktop(desktop)
        try: child.communicate('\n', timeout=5)
        except subprocess.TimeoutExpired:
            child.kill(); child.communicate()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    print(json.dumps({'kind': 'synthetic-receiver-only', 'SendInput_called': False,
                     'hooks_installed': False, 'physical_input_tested': False,
                     'cases': [probe(kind) for kind in ('EDIT', 'RichEdit50W')]}, ensure_ascii=False, indent=2))
