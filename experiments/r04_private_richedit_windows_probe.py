"""Real Win32 R04 receiver probe; PRIVATE INACTIVE desktop, never live input.

Uses the existing early_native_harness.py --worker to create its own RichEdit A
on a disposable desktop and creates independent RichEdit B there. All text is
synthetic. No hooks, SendInput, focus/HKL changes, clipboard, or user windows.

The optional timeout case briefly suspends ONLY the disposable worker thread
belonging to the child process that this script launched. It always resumes
the thread in finally and never suspends any user or TypeBridge process.
"""
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from early_native_edit import NativeEditBridge
from early_scintilla import replace_prefix

SMTO = 0x23  # SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT
WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0xC, 0xD, 0xE
WM_CHAR, WM_CLOSE = 0x102, 0x10
EM_SETSEL, EM_EMPTYUNDOBUFFER, EM_UNDO, EM_REDO = 0xB1, 0xCD, 0xC7, 0x454


def configured_apis():
    u = ctypes.WinDLL('user32', use_last_error=True)
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    u.GetThreadDesktop.argtypes, u.GetThreadDesktop.restype = [w.DWORD], w.HANDLE
    u.OpenDesktopW.argtypes, u.OpenDesktopW.restype = [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    u.SetThreadDesktop.argtypes, u.SetThreadDesktop.restype = [w.HANDLE], w.BOOL
    u.CloseDesktop.argtypes = [w.HANDLE]
    u.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
    u.CreateWindowExW.restype = w.HWND
    u.DestroyWindow.argtypes, u.DestroyWindow.restype = [w.HWND], w.BOOL
    u.IsWindow.argtypes, u.IsWindow.restype = [w.HWND], w.BOOL
    u.GetKeyboardLayout.argtypes, u.GetKeyboardLayout.restype = [w.DWORD], ctypes.c_void_p
    u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, ctypes.c_ssize_t,
                                      w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
    u.SendMessageTimeoutW.restype = ctypes.c_size_t
    k.GetCurrentThreadId.restype = w.DWORD
    k.LoadLibraryW.argtypes, k.LoadLibraryW.restype = [w.LPCWSTR], w.HMODULE
    k.OpenThread.argtypes, k.OpenThread.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    k.SuspendThread.argtypes, k.SuspendThread.restype = [w.HANDLE], w.DWORD
    k.ResumeThread.argtypes, k.ResumeThread.restype = [w.HANDLE], w.DWORD
    k.CloseHandle.argtypes, k.CloseHandle.restype = [w.HANDLE], w.BOOL
    return u, k


def send(u, hwnd, message, wp=0, lp=0, timeout=500):
    answer = ctypes.c_size_t()
    ctypes.set_last_error(0)
    ok = bool(u.SendMessageTimeoutW(hwnd, message, wp, lp, SMTO,
                                    timeout, ctypes.byref(answer)))
    if not ok:
        raise TimeoutError(f'isolated-message-{message:x}-error-{ctypes.get_last_error()}')
    return answer.value


def read(u, hwnd):
    length = send(u, hwnd, WM_GETTEXTLENGTH)
    buf = ctypes.create_unicode_buffer(length + 1)
    send(u, hwnd, WM_GETTEXT, length + 1, ctypes.addressof(buf))
    return buf.value.replace('\r\n', '\r')


def seed(u, hwnd, text):
    content = ctypes.create_unicode_buffer(text)
    send(u, hwnd, WM_SETTEXT, 0, ctypes.addressof(content))
    end = len(text.encode('utf-16-le')) // 2
    send(u, hwnd, EM_SETSEL, end, end)
    send(u, hwnd, EM_EMPTYUNDOBUFFER)


def probe():
    u, k = configured_apis()
    original_desktop = u.GetThreadDesktop(k.GetCurrentThreadId())
    child = subprocess.Popen(
        [sys.executable, str(ROOT / 'early_native_harness.py'), '--worker'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    desktop = top_b = field_b = None
    on_private_desktop = False
    report = {'desktop': 'private-inactive', 'worker_child_pid': child.pid,
              'hooks': False, 'SendInput': False, 'foreground_changed': False,
              'HKL_changed': False, 'clipboard_accessed': False, 'cases': []}
    try:
        startup = queue.Queue()
        threading.Thread(target=lambda: startup.put(child.stdout.readline()), daemon=True).start()
        try:
            ready = json.loads(startup.get(timeout=7))
        except queue.Empty:
            raise TimeoutError('child-did-not-announce-private-desktop') from None
        if 'error' in ready:
            raise RuntimeError(f'private-desktop-worker:{ready["error"]}')
        desktop = u.OpenDesktopW(ready['desktop'], 0, False, 0x1FF)
        if not desktop:
            raise ctypes.WinError(ctypes.get_last_error())
        if not u.SetThreadDesktop(desktop):
            raise ctypes.WinError(ctypes.get_last_error())
        on_private_desktop = True
        if not k.LoadLibraryW('Msftedit.dll'):
            raise ctypes.WinError(ctypes.get_last_error())
        top_b = u.CreateWindowExW(0, 'STATIC', 'TypeBridge test B', 0,
                                  0, 0, 300, 200, None, None, None, None)
        field_b = u.CreateWindowExW(0, 'RichEdit50W', '', 0x40000000 | 0x4 | 0x1000,
                                    0, 0, 280, 100, top_b, None, None, None)
        if not top_b or not field_b:
            raise ctypes.WinError(ctypes.get_last_error())
        a = ready['focus']
        b = int(field_b)
        if a == b or not u.IsWindow(a) or not u.IsWindow(b):
            raise AssertionError('not-distinct-live-disposable-windows')
        lease_context = type('Context', (), dict(
            top=ready['top'], focus=a, thread_id=ready['thread'],
            hkl=int(u.GetKeyboardLayout(ready['thread']) or 0) & 0xFFFFFFFF,
            password_revision=0))()
        report['layout_observed'] = f'{lease_context.hkl:08X}'

        # Real A/B windows on the same inaccessible-to-user desktop.
        for source, replacement in (('ghbdtn', 'привет'),
                                    ('akuo', 'שלום'),
                                    ('руддщ', 'hello')):
            seed(u, a, source)
            seed(u, b, 'B_UNCHANGED')
            with NativeEditBridge(lease_context, lambda: True, off_hook=True,
                                  experimental_target_lease=True) as bridge:
                outcome = replace_prefix(bridge, source, replacement, lambda: True)
                result = read(u, a)
                other = read(u, b)
                if outcome.state != 'verified' or result != replacement or other != 'B_UNCHANGED':
                    raise AssertionError(f'addressed-result-{source}-{outcome.state}-{outcome.reason}')
                if not bridge.msg(EM_UNDO) or read(u, a) != source:
                    raise AssertionError('isolated-original-native-undo-failed')
                if not bridge.msg(EM_REDO) or read(u, a) != replacement:
                    raise AssertionError('isolated-original-native-redo-failed')
                report['cases'].append({'kind': 'native-addressed-correction',
                                        'source': source, 'target': replacement,
                                        'A_readback': True, 'B_unchanged': True,
                                        'undo': True, 'redo': True})

        # Exercise the ACTUAL TypeBridge manual-cycle native mutation method
        # on our disposable RichEdit field. The surrounding hook context/HKL
        # are synthetic by design; real HWND text, caret and Undo are read back.
        from typebridge_app import FocusContext, LiveHook
        from word_cycle import remember_word
        ctx = FocusContext(int(ready['top']), a, int(ready['thread']), 'en',
                           'private-fixture.exe', 'TypeBridge private fixture',
                           layout_window=a, hkl=lease_context.hkl, native_target=True)
        hook = LiveHook.__new__(LiveHook)
        hook.user32 = Mock()
        hook.user32.GetKeyboardLayout.return_value = ctx.hkl
        hook.context = lambda: ctx
        hook._native_is_off_hook = lambda: True
        hook._postwrite_permitted = lambda *_args, **_kwargs: True
        hook._record_replay_layout = lambda _ctx, requested: requested
        hook._language_from_id = lambda langid: 'ru' if langid == 0x0419 else 'en'
        hook.engine = SimpleNamespace(mapper=SimpleNamespace(layouts={
            'ru': 0x04190419, 'en': 0x04090409}))
        hook.keys = []
        hook.pressed = lambda _vk: False
        hook.emit = Mock()
        hook.debug_words = hook.pipeline_debug = hook.stopping = False
        seed(u, a, 'ghbdtn   ')
        seed(u, b, 'B_UNCHANGED')
        original_keys = [(ord(c.upper()), False) for c in 'ghbdtn']
        recent = remember_word(original_keys, 'ghbdtn', 'en', '   ',
                               hook._word_scope(ctx), whole=True)
        if recent is None:
            raise AssertionError('failed-to-construct-manual-fixture')
        hook.last_word = recent
        hook._cycle_edit_word(ctx, recent, 'ru', 'привет', native=True)
        if read(u, a) != 'привет   ' or read(u, b) != 'B_UNCHANGED':
            raise AssertionError('manual-native-first-cycle-failed')
        if recent.text != 'привет' or recent.language != 'ru' or recent.suffix != '   ':
            raise AssertionError('manual-native-memory-not-updated')
        hook._cycle_edit_word(ctx, recent, 'en', 'ghbdtn', native=True)
        if read(u, a) != 'ghbdtn   ' or read(u, b) != 'B_UNCHANGED':
            raise AssertionError('manual-native-repeat-cycle-failed')
        if recent.text != 'ghbdtn' or recent.language != 'en':
            raise AssertionError('manual-native-repeat-memory-not-updated')
        report['cases'].append({'kind': 'real-manual-cycle-method',
                                'field': 'private-RichEdit50W',
                                'first_and_repeat': True, 'spaces_preserved': True,
                                'neighbor_unchanged': True,
                                'hook_key_delivery': 'not tested'})

        # Deterministic timeout: suspend ONLY our own disposable child GUI
        # thread. Exercise the ACTUAL NativeEditBridge.msg error path. A
        # timeout never proves that a previously sent WM_CHAR cannot arrive.
        seed(u, a, 'T')
        seed(u, b, 'B_UNCHANGED')
        thread_handle = k.OpenThread(0x0002, False, ready['thread'])  # THREAD_SUSPEND_RESUME.
        if not thread_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        with NativeEditBridge(lease_context, lambda: True, off_hook=True,
                              experimental_target_lease=True) as bridge:
            try:
                old_count = k.SuspendThread(thread_handle)
                if old_count == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    try:
                        bridge.msg(WM_CHAR, ord('X'), 1)
                    except TimeoutError:
                        accepted = False
                    else:
                        accepted = True
                    failure = bridge.failure
                    timed_out = bridge.timeout
                    write_started = bridge.write_started
                finally:
                    k.ResumeThread(thread_handle)
            finally:
                k.CloseHandle(thread_handle)
        after = read(u, a)  # Sync read after resume: may observe a late WM_CHAR.
        if read(u, b) != 'B_UNCHANGED':
            raise AssertionError('timeout-target-B-mutated')
        if accepted or not timed_out or not write_started or 'error-1460' not in failure:
            raise AssertionError(f'native-timeout-not-observed:{accepted}:{failure}')
        report['cases'].append({'kind': 'native-bridge-suspended-worker-timeout',
                                'dispatch_returned_success': accepted, 'native_failure': failure,
                                'native_timeout_flag': timed_out, 'write_attempted': write_started,
                                'A_text_after_resume': after, 'B_unchanged': True,
                                'late_delivery_possible': True, 'retry_performed': False})

        # Destroy A, then attempt an OPT-IN addressed mutation against the
        # original NativeTargetLease. Must refuse before writing B.
        seed(u, a, 'ghbdtn')
        seed(u, b, 'B_UNCHANGED')
        with NativeEditBridge(lease_context, lambda: True, off_hook=True,
                              experimental_target_lease=True) as bridge:
            send(u, a, WM_CLOSE)
            if u.IsWindow(a):
                raise AssertionError('test-control-A-still-live')
            try:
                bridge.msg(WM_CHAR, ord('!'), 1)
            except (ValueError, TimeoutError) as exc:
                label = type(exc).__name__ + ':' + str(exc)
            else:
                raise AssertionError('destroyed-control-was-not-refused')
        if read(u, b) != 'B_UNCHANGED':
            raise AssertionError('destroy-target-B-mutated')
        report['cases'].append({'kind': 'destroy-before-next-write',
                                'refusal': label, 'B_unchanged': True})
        report['result'] = 'PASS'
        return report
    finally:
        # B belongs to this worker's temporary desktop, and is never the
        # user's window. Destroy it before restoring the original desktop.
        if field_b:
            u.DestroyWindow(field_b)
        if top_b:
            u.DestroyWindow(top_b)
        if on_private_desktop:
            if not u.SetThreadDesktop(original_desktop):
                # Throwing before child cleanup could strand resources; leave
                # the process cleanup to the nested finally below.
                report['restore_desktop_error'] = ctypes.get_last_error()
        if desktop:
            u.CloseDesktop(desktop)
        try:
            child.communicate('\n', timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
