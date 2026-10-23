"""Synthetic cross-process RichEdit checks on an inactive Windows desktop.

Run --self-test. No hooks, SendInput, clipboard or foreground changes.
The worker creates only its own private windows; no existing document is read.
"""
import ctypes
from ctypes import wintypes as w
import json
import queue
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import uuid

from early_native_edit import NativeEditBridge, utf8_position
from early_scintilla import replace_prefix
from early_tom import NativeUndoGroup


def worker():
    ready, result = threading.Event(), {}
    def windows():
        u = ctypes.WinDLL('user32', use_last_error=True)
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.LoadLibraryW.argtypes, k.LoadLibraryW.restype = [w.LPCWSTR], w.HMODULE
        u.GetThreadDesktop.argtypes, u.GetThreadDesktop.restype = [w.DWORD], w.HANDLE
        u.CreateDesktopW.argtypes = [w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p]
        u.CreateDesktopW.restype = w.HANDLE
        u.SetThreadDesktop.argtypes, u.SetThreadDesktop.restype = [w.HANDLE], w.BOOL
        u.CloseDesktop.argtypes = [w.HANDLE]
        u.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
        u.CreateWindowExW.restype = w.HWND
        u.DestroyWindow.argtypes = [w.HWND]
        u.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        u.DispatchMessageW.argtypes, u.DispatchMessageW.restype = [ctypes.POINTER(w.MSG)], ctypes.c_ssize_t
        tid = k.GetCurrentThreadId()
        original = u.GetThreadDesktop(tid)
        desktop_name = 'TypeBridge-test-' + uuid.uuid4().hex
        desk = u.CreateDesktopW(desktop_name, None, None, 0, 0x1FF, None)
        top = edit = None
        try:
            if not desk or not u.SetThreadDesktop(desk):
                raise ctypes.WinError(ctypes.get_last_error())
            if not k.LoadLibraryW('Msftedit.dll'):
                raise ctypes.WinError(ctypes.get_last_error())
            top = u.CreateWindowExW(0, 'STATIC', 'TypeBridge isolated test', 0,
                                    0, 0, 600, 300, None, None, None, None)
            edit = u.CreateWindowExW(0, 'RichEdit50W', '', 0x40000000 | 0x4 | 0x1000,
                                     0, 0, 500, 200, top, None, None, None)
            if not top or not edit:
                raise ctypes.WinError(ctypes.get_last_error())
            result.update(top=int(top), focus=int(edit), thread=tid, desktop=desktop_name)
            ready.set()
            msg = w.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        except Exception as error:
            result['error'] = type(error).__name__
            ready.set()
        finally:
            if edit: u.DestroyWindow(edit)
            if top: u.DestroyWindow(top)
            u.SetThreadDesktop(original)
            if desk: u.CloseDesktop(desk)
    thread = threading.Thread(target=windows, daemon=True)
    thread.start()
    if not ready.wait(5):
        raise TimeoutError('native-worker-start')
    print(json.dumps(result), flush=True)
    sys.stdin.readline()
    if 'thread' in result:
        u = ctypes.WinDLL('user32')
        u.PostThreadMessageW(result['thread'], 0x12, 0, 0)
    thread.join(3)


def self_test():
    u = ctypes.WinDLL('user32', use_last_error=True)
    k = ctypes.WinDLL('kernel32')
    u.GetThreadDesktop.argtypes, u.GetThreadDesktop.restype = [w.DWORD], w.HANDLE
    u.OpenDesktopW.argtypes, u.OpenDesktopW.restype = [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    u.SetThreadDesktop.argtypes, u.SetThreadDesktop.restype = [w.HANDLE], w.BOOL
    u.CloseDesktop.argtypes = [w.HANDLE]
    original = u.GetThreadDesktop(k.GetCurrentThreadId())
    desktop = None
    undo = None
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        startup = queue.Queue()
        threading.Thread(target=lambda: startup.put(process.stdout.readline()), daemon=True).start()
        try:
            ready = json.loads(startup.get(timeout=7))
        except queue.Empty:
            raise TimeoutError('native-worker-response') from None
        if 'error' in ready:
            raise RuntimeError(ready['error'])
        desktop = u.OpenDesktopW(ready['desktop'], 0, False, 0x1FF)
        if not desktop or not u.SetThreadDesktop(desktop):
            raise ctypes.WinError(ctypes.get_last_error())
        ctx = SimpleNamespace(top=ready['top'], focus=ready['focus'])
        undo = NativeUndoGroup(ctx.focus)
        checks = []
        cases = [('рудд', 'hello', 'щ'), ('יקךך', 'hello', 'ם'), ('ghbdt', 'привет', 'n'),
                 ('עינגא', 'привет', 'מ'), ('aku', 'שלום', 'o'), ('флг', 'שלום', 'щ')]
        for prefix, replacement, held in cases:
            initial = '🙂 начало\r' + prefix + ' конец'
            # Fixture setup is not a timed early operation. Let the child
            # finish its cold-start message-loop initialization first.
            with NativeEditBridge(ctx, lambda: True, pending_char=held, undo_group=undo) as bridge:
                value = ctypes.create_unicode_buffer(initial)
                answer = ctypes.c_size_t()
                if not bridge.u.SendMessageTimeoutW(ctx.focus, 0xC, 0, ctypes.addressof(value), 3, 500, ctypes.byref(answer)):
                    raise TimeoutError('fixture-setup-' + str(ctypes.get_last_error()))
                caret = utf8_position(initial, len(('🙂 начало\r' + prefix).encode()))
                bridge.u.SendMessageTimeoutW(ctx.focus, 0xB1, caret, caret, 3, 500, ctypes.byref(answer))
                bridge.u.SendMessageTimeoutW(ctx.focus, 0xCD, 0, 0, 3, 500, ctypes.byref(answer))
            with NativeEditBridge(ctx, lambda: True, pending_char=held, undo_group=undo) as bridge:
                outcome = replace_prefix(bridge, prefix, replacement, lambda: True)
                after, position = bridge.snapshot()
                expected = ('🙂 начало\r' + replacement + ' конец').encode()
                if outcome.state != 'verified' or after != expected or position != len(('🙂 начало\r' + replacement).encode()):
                    raise AssertionError('native-range-result-' + outcome.state + '-' + outcome.reason)
                if not bridge.msg(0xC7):  # EM_UNDO; isolated native RichEdit only.
                    raise AssertionError('native-undo-unavailable')
                undo_text = bridge.snapshot(allow_selection=True)[0]
                undo_ok = undo_text == ('🙂 начало\r' + prefix + held + ' конец').encode()
                if not bridge.msg(0x454) or bridge.snapshot(allow_selection=True) != (expected, position):  # EM_REDO
                    raise AssertionError('native-redo-result')
                restored_caret = utf8_position(expected.decode(), position)
                bridge.msg(0xB1, restored_caret, restored_caret)
                # Immediate synthetic continuation and separator; no sleeps.
                bridge.msg(0x102, ord('!'))  # WM_CHAR; only our isolated RichEdit.
                bridge.msg(0x102, ord(' '))
                after, _ = bridge.snapshot()
                if after != ('🙂 начало\r' + replacement + '!  конец').encode():
                    raise AssertionError('native-tail-order')
                checks.append({'replacement': replacement, 'state': outcome.state, 'undo_restores_source': undo_ok,
                               'fixture_undo_text': undo_text.decode() if not undo_ok else None})
        with NativeEditBridge(ctx, lambda: True, pending_char='x') as bridge:
            bridge.msg(0xB1, 0, 2)
            outcome = replace_prefix(bridge, 'xx', 'yy', lambda: True)
            if outcome.state != 'refused' or bridge.write_started:
                raise AssertionError('selection-not-refused')
        return {'range_checks_ok': True, 'native_cases': len(checks), 'selection_refused': True,
                'desktop': 'private-inactive', 'physical_input_tested': False,
                'clipboard_touched': False, 'hooks_installed': False,
                'undo_restores_source': all(check['undo_restores_source'] for check in checks),
                'integration_gate': 'BLOCKED_PENDING_UNDO_AND_PHYSICAL_INPUT', 'checks': checks}
    finally:
        if undo is not None: undo.close()
        u.SetThreadDesktop(original)
        if desktop: u.CloseDesktop(desktop)
        try:
            process.communicate('\n', timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    if '--worker' in sys.argv:
        worker()
    elif '--self-test' in sys.argv:
        report = self_test()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        # Successful range checks are not an admission pass. Make the unresolved
        # Undo gate visible to scripts as well as to readers of the JSON.
        if not report['undo_restores_source']:
            raise SystemExit(2)
    else:
        raise SystemExit('Use --self-test; never activates the private desktop.')
