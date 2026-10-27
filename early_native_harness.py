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


def worker(field_class='RichEdit50W'):
    if field_class not in ('EDIT', 'RichEdit50W'):
        raise ValueError('unsupported-fixture-class')
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
            edit = u.CreateWindowExW(0, field_class, '', 0x40000000 | 0x4 | 0x1000,
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


def runtime_checks(ctx, thread):
    """Main recognition/routing against the private native field, no OS hooks.

    Physical-key delivery is simulated explicitly with WM_CHAR only when the
    callback forwards a key. SendInput and actual hook chaining are forbidden.
    """
    from engine import LanguageEngine, source_data_dir, LANGS
    from typebridge_app import LiveHook, Settings, FocusContext, KbdHook, WM_KEYDOWN, WM_KEYUP
    engine = LanguageEngine(source_data_dir())
    hook = LiveHook(engine, Settings(early_correction=True, early_native=True))
    hook.pressed = lambda vk: False
    hook.user32.GetKeyState = lambda vk: 0
    hook.user32.CallNextHookEx = lambda *args: 0
    def forbidden(*args):
        raise AssertionError('background-runtime-must-not-use-SendInput')
    hook.user32.SendInput = forbidden
    def context():
        code = int(hook.user32.GetKeyboardLayout(thread) or 0) & 0xFFFF
        language = next((key for key, value in LANGS.items() if code == value), '')
        return FocusContext(ctx.top, ctx.focus, thread, language, 'native-fixture.exe', 'private fixture', ctx.focus)
    hook.context = context
    def message(command, wp=0, lp=0):
        # Fixture setup / delivery may include cold font and language loading.
        # Only the real early transaction is subject to its 45 ms deadline.
        with NativeEditBridge(ctx, lambda: True, pending_char='x') as bridge:
            answer = ctypes.c_size_t()
            if not bridge.u.SendMessageTimeoutW(ctx.focus, command, wp, lp, 3, 500, ctypes.byref(answer)):
                raise TimeoutError('private-fixture-message')
            return answer.value
    def text():
        with NativeEditBridge(ctx, lambda: True, pending_char='x') as bridge:
            return bridge.snapshot(allow_selection=True)[0].decode()
    def type_keys(physical):
        for char in physical:
            vk = 0x20 if char == ' ' else ord(char.upper())
            event = KbdHook(vkCode=vk)
            consumed = hook.handle_keyboard(0, WM_KEYDOWN, ctypes.addressof(event))
            if not consumed:
                translated = ' ' if vk == 0x20 else engine.mapper.char(vk, context().language, False)
                if len(translated) != 1:
                    raise AssertionError('fixture-key-translation')
                message(0x102, ord(translated), 1)
            hook.handle_keyboard(0, WM_KEYUP, ctypes.addressof(event))
    checks = []
    for source, physical, target, expected in (
            ('ru', 'hello', 'en', 'hello'), ('he', 'hello', 'en', 'hello'),
            ('en', 'ghbdtn', 'ru', 'привет'), ('he', 'ghbdtn', 'ru', 'привет'),
            ('en', 'akuo', 'he', 'שלום'), ('ru', 'akuo', 'he', 'שלום')):
        empty = ctypes.create_unicode_buffer('')
        message(0xC, 0, ctypes.addressof(empty))
        message(0x50, 0, int(engine.mapper.layouts[source]))
        if context().language != source:
            raise AssertionError('private-fixture-source-layout')
        hook.clear_token()
        hook.last_focus, hook.last_language = ctx.focus, source
        hook.early_disabled = hook.native_early_disabled = False
        hook.suppressed_keyups.clear()
        type_keys(physical)
        if text() != expected or context().language != target or not hook.early_done:
            raise AssertionError('main-native-runtime-before-space-' + source + '-' + target)
        type_keys(' ')
        if text() != expected + ' ':
            raise AssertionError('main-native-runtime-boundary')
        if target == 'en':
            type_keys('ghbdtn ')
            if text() != expected + ' привет ':
                raise AssertionError('main-native-runtime-next-token')
        checks.append({'source': source, 'target': target, 'before_space': True,
                       'separator_preserved': True, 'next_token_checked': target == 'en'})
    return checks


def self_test(*, include_runtime=False):
    u = ctypes.WinDLL('user32', use_last_error=True)
    k = ctypes.WinDLL('kernel32')
    u.GetThreadDesktop.argtypes, u.GetThreadDesktop.restype = [w.DWORD], w.HANDLE
    u.OpenDesktopW.argtypes, u.OpenDesktopW.restype = [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    u.SetThreadDesktop.argtypes, u.SetThreadDesktop.restype = [w.HANDLE], w.BOOL
    u.CloseDesktop.argtypes = [w.HANDLE]
    original = u.GetThreadDesktop(k.GetCurrentThreadId())
    desktop = None
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
        checks = []
        cases = [('рудд', 'hello', 'щ'), ('יקךך', 'hello', 'ם'), ('ghbdt', 'привет', 'n'),
                 ('עינגא', 'привет', 'מ'), ('aku', 'שלום', 'o'), ('флг', 'שלום', 'щ')]
        for prefix, replacement, held in cases:
            initial = '🙂 начало\r' + prefix + ' конец'
            # Fixture setup is not a timed early operation. Let the child
            # finish its cold-start message-loop initialization first.
            with NativeEditBridge(ctx, lambda: True, pending_char=held) as bridge:
                value = ctypes.create_unicode_buffer(initial)
                answer = ctypes.c_size_t()
                if not bridge.u.SendMessageTimeoutW(ctx.focus, 0xC, 0, ctypes.addressof(value), 3, 500, ctypes.byref(answer)):
                    raise TimeoutError('fixture-setup-' + str(ctypes.get_last_error()))
                caret = utf8_position(initial, len(('🙂 начало\r' + prefix).encode()))
                bridge.u.SendMessageTimeoutW(ctx.focus, 0xB1, caret, caret, 3, 500, ctypes.byref(answer))
                bridge.u.SendMessageTimeoutW(ctx.focus, 0xCD, 0, 0, 3, 500, ctypes.byref(answer))
            with NativeEditBridge(ctx, lambda: True, pending_char=held) as bridge:
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
                # Continuation is a separate Undo group, then one more Undo
                # must restore the source word, including the held character.
                if not bridge.msg(0xC7) or bridge.snapshot(allow_selection=True)[0] != expected:
                    raise AssertionError('native-tail-undo-group')
                if not bridge.msg(0xC7) or bridge.snapshot(allow_selection=True)[0] != ('🙂 начало\r' + prefix + held + ' конец').encode():
                    raise AssertionError('native-replacement-undo-group')
                checks.append({'replacement': replacement, 'state': outcome.state, 'undo_restores_source': undo_ok,
                               'fixture_undo_text': undo_text.decode() if not undo_ok else None})
        # Simulate repeated manual Pause cycles with real RichEdit ranges,
        # including a space suffix and RTL Hebrew, on the inactive desktop.
        # This is a transaction test, NOT a physical global-hotkey test.
        with NativeEditBridge(ctx, lambda: True) as bridge:
            initial = ctypes.create_unicode_buffer('tree ')
            answer = ctypes.c_size_t()
            if not bridge.u.SendMessageTimeoutW(ctx.focus, 0xC, 0, ctypes.addressof(initial),
                                                 3, 500, ctypes.byref(answer)):
                raise TimeoutError('manual-cycle-fixture-setup')
            bridge.msg(0xB1, 5, 5)
        manual_steps = [('tree ', 'ארקק '), ('ארקק ', 'екуу '), ('екуу ', 'tree ')]
        for source, destination in manual_steps:
            with NativeEditBridge(ctx, lambda: True) as bridge:
                outcome = replace_prefix(bridge, source, destination, lambda: True)
                actual, caret = bridge.snapshot()
                if (outcome.state != 'verified' or actual != destination.encode()
                        or caret != len(destination.encode())):
                    raise AssertionError('native-manual-cycle-' + outcome.state + '-' + outcome.reason)
        with NativeEditBridge(ctx, lambda: True) as bridge:
            outcome = replace_prefix(bridge, 'ארקק ', 'екуу ', lambda: True)
            if outcome.state != 'refused' or bridge.snapshot()[0] != b'tree ':
                raise AssertionError('native-manual-cycle-stale-source-mutated')
        # The production off-hook path admits longer words than the old 12-char
        # inline guard. Check actual RichEdit result and Undo on this private
        # inactive control, without installing a keyboard hook.
        worker_length_checks = []
        for source, destination in (
                ('abcdefghijklmno', 'абвгдежзийклмнопр'),
                ('qwertyuiopasdfg', 'אבגדהוזחטיכלמנס')):
            with NativeEditBridge(ctx, lambda: True) as bridge:
                initial = ctypes.create_unicode_buffer(source)
                answer = ctypes.c_size_t()
                if not bridge.u.SendMessageTimeoutW(ctx.focus, 0xC, 0,
                        ctypes.addressof(initial), 3, 500, ctypes.byref(answer)):
                    raise TimeoutError('long-worker-fixture-setup')
                bridge.msg(0xB1, len(source), len(source))
                bridge.msg(0xCD)
            with NativeEditBridge(ctx, lambda: True, off_hook=True) as bridge:
                outcome = replace_prefix(bridge, source, destination, lambda: True)
                if outcome.state != 'verified' or bridge.snapshot()[0] != destination.encode():
                    raise AssertionError('long-worker-result-' + outcome.state + '-' + outcome.reason)
                if not bridge.msg(0xC7) or bridge.snapshot(allow_selection=True)[0] != source.encode():
                    raise AssertionError('long-worker-undo-failed')
                worker_length_checks.append({'from_chars': len(source),
                                             'to_chars': len(destination),
                                             'undo_restores_source': True})
        # Read-only comparison of the current WM_CHAR transport with a single
        # EM_REPLACESEL transaction on a DISPOSABLE private desktop control.
        # Never substitute it in production until Undo/Redo agrees for RTL.
        bulk_probe = []
        for source, destination in [('ghbdtn', 'привет'), ('akuo', 'שלום')]:
            with NativeEditBridge(ctx, lambda: True) as bridge:
                initial = ctypes.create_unicode_buffer(source)
                answer = ctypes.c_size_t()
                if not bridge.u.SendMessageTimeoutW(ctx.focus, 0xC, 0,
                        ctypes.addressof(initial), 3, 500, ctypes.byref(answer)):
                    raise TimeoutError('bulk-probe-setup')
                bridge.msg(0xCD)  # EM_EMPTYUNDOBUFFER
                bridge.msg(0xB1, 0, len(source))
                replacement = ctypes.create_unicode_buffer(destination)
                ok = bool(bridge.u.SendMessageTimeoutW(ctx.focus, 0xC2, 1,
                    ctypes.addressof(replacement), 3, 500, ctypes.byref(answer)))
                changed = bridge.snapshot(allow_selection=True)[0] if ok else b''
                undo_ok = bool(bridge.msg(0xC7)) if ok else False
                restored = bridge.snapshot(allow_selection=True)[0] if ok else b''
                bulk_probe.append({'target': 'ru' if destination == 'привет' else 'he',
                                   'text_correct': changed == destination.encode(),
                                   'undo_restores_source': undo_ok and restored == source.encode()})
        with NativeEditBridge(ctx, lambda: True, pending_char='x') as bridge:
            bridge.msg(0xB1, 0, 2)
        # Fixture selection is not a mutation by the transaction under test.
        with NativeEditBridge(ctx, lambda: True, pending_char='x') as bridge:
            outcome = replace_prefix(bridge, 'xx', 'yy', lambda: True)
            if outcome.state != 'refused' or bridge.write_started:
                raise AssertionError('selection-not-refused')
        runtime = runtime_checks(ctx, ready['thread']) if include_runtime else []
        return {'range_checks_ok': True, 'native_cases': len(checks), 'selection_refused': True,
                'desktop': 'private-inactive', 'physical_input_tested': False,
                'clipboard_touched': False, 'hooks_installed': False,
                'undo_restores_source': all(check['undo_restores_source'] for check in checks),
                'integration_gate': ('BLOCKED_PENDING_PHYSICAL_INPUT' if all(check['undo_restores_source'] for check in checks)
                                     else 'BLOCKED_PENDING_UNDO_AND_PHYSICAL_INPUT'),
                'manual_cycle_native': 'three-verified-with-stale-source-refused',
                'offhook_long_word_checks': worker_length_checks,
                'bulk_replace_research_only': bulk_probe,
                'runtime_cases': runtime, 'checks': checks}
    finally:
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
        worker('EDIT' if '--edit-fixture' in sys.argv else 'RichEdit50W')
    elif '--self-test' in sys.argv or '--runtime-self-test' in sys.argv:
        report = self_test(include_runtime='--runtime-self-test' in sys.argv)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        # Successful range checks are not an admission pass. Make the unresolved
        # Undo gate visible to scripts as well as to readers of the JSON.
        if not report['undo_restores_source']:
            raise SystemExit(2)
    else:
        raise SystemExit('Use --self-test; never activates the private desktop.')
