"""Real installed Notepad++, disposable profile on an INACTIVE desktop.

No user documents, hooks, physical keys, clipboard, foreground or HKL changes.
Only the exact child PID launched here may be read/modified/closed.
"""
import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.layout_quality_20261106 import FrozenMapper, KEYMAP
from experiments.r10_private_scintilla_20261107 import apis
from scintilla_selection import ScintillaRangeTransport, ScintillaSelectionAdapter
from selection_cycle import SelectionCycle, replace_selection


def probe(executable):
    mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
    u, k = apis()
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    u.EnumDesktopWindows.argtypes = [w.HANDLE, callback_type, w.LPARAM]
    u.EnumChildWindows.argtypes = [w.HWND, callback_type, w.LPARAM]
    u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    original = u.GetThreadDesktop(k.GetCurrentThreadId())
    name = 'TypeBridge-NppRange-' + uuid.uuid4().hex
    desktop = u.CreateDesktopW(name, None, None, 0, 0x1FF, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    process, attached, context = None, False, None
    profile = ROOT / 'build' / name
    profile.mkdir(parents=True)
    cases = []
    try:
        if not u.SetThreadDesktop(desktop):
            raise ctypes.WinError(ctypes.get_last_error())
        attached = True
        startup = subprocess.STARTUPINFO()
        startup.lpDesktop = 'winsta0\\' + name
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen([str(executable), '-multiInst', '-nosession', '-noPlugin',
                                    '-settingsDir=' + str(profile)], startupinfo=startup,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        def owned(hwnd):
            pid = w.DWORD()
            tid = u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return tid if pid.value == process.pid else 0
        def class_name(hwnd):
            text = ctypes.create_unicode_buffer(128)
            u.GetClassNameW(hwnd, text, len(text))
            return text.value
        deadline = time.monotonic() + 10
        top, focus = None, None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('fixture-exited-during-startup')
            windows, inventory = [], []
            @callback_type
            def collect(hwnd, unused):
                pid = w.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                inventory.append((class_name(hwnd), pid.value))
                if owned(hwnd):
                    windows.append(hwnd)
                return True
            u.EnumDesktopWindows(desktop, collect, 0)
            tops = [hwnd for hwnd in windows if class_name(hwnd) == 'Notepad++']
            if tops:
                top = tops[0]
                windows.clear()
                u.EnumChildWindows(top, collect, 0)
                controls = [hwnd for hwnd in windows if class_name(hwnd) == 'Scintilla']
                if controls:
                    focus = controls[0]
                    break
            time.sleep(.05)
        if not focus:
            raise RuntimeError(('fixture-no-scintilla-window',
                                process.pid, inventory))
        context = SimpleNamespace(top=top, focus=focus, process_name='notepad++.exe')
        def transport():
            if not owned(context.top) or not owned(context.focus):
                raise RuntimeError('fixture-pid-changed')
            return ScintillaRangeTransport(context)
        def seed(text, anchor, caret):
            with transport() as native:
                payload = text.encode('utf-8') + b'\0'
                address = native.k.VirtualAllocEx(native.handle, None, len(payload), 0x3000, 4)
                if not address:
                    raise OSError('fixture-seed-allocation')
                try:
                    native.write_memory(address, payload)
                    native.msg(2037, 65001)
                    native.msg(2012, 1)
                    native.msg(2181, 0, address)
                    native.msg(2160, anchor, caret)
                    native.msg(2175)
                finally:
                    if not native.timeout:
                        native.k.VirtualFreeEx(native.handle, address, 0, 0x8000)
        def read():
            with transport() as native:
                length = native.msg(2006)
                return native.read_range(0, length).decode('utf-8')
        for source in ('en', 'ru', 'he'):
            for target in ('en', 'ru', 'he'):
                if source == target:
                    continue
                active = (source, target, next(lang for lang in ('en', 'ru', 'he')
                                                if lang not in (source, target)))
                for backward in (False, True):
                    selected = mapper.word(mapper.keys_for('ghbdtn', 'en'), source) + '\r\n\t🙂'
                    prefix, suffix = 'PREFIX 🙂 ', ' SUFFIX'
                    text = prefix + selected + suffix
                    first = len(prefix.encode('utf-8'))
                    last = first + len(selected.encode('utf-8'))
                    seed(text, last if backward else first, first if backward else last)
                    cycle, states = None, [text]
                    for step in range(3):
                        with transport() as native:
                            adapter = ScintillaSelectionAdapter(native, ('owned', focus), 1, lambda: True)
                            if cycle is None:
                                cycle = SelectionCycle(adapter.snapshot(), source, mapper, active)
                            result = replace_selection(cycle, adapter, lambda: True)
                            if result.state != 'verified':
                                raise AssertionError((source, target, backward, step, result))
                        observed = read()
                        assert observed == prefix + cycle.snapshot.text + suffix
                        states.append(observed)
                    assert states[-1] == text
                    for wanted in reversed(states[:-1]):
                        with transport() as native:
                            native.msg(2176)
                        assert read() == wanted
                    for wanted in states[1:]:
                        with transport() as native:
                            native.msg(2011)
                        assert read() == wanted
                    cases.append(dict(source=source, target=target, backward=backward,
                                      cycles=3, undo=3, redo=3, passed=True))
        assert process.poll() is None
        return dict(passed=len(cases), cases=cases, executable=str(executable),
                    executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                    desktop='private-inactive', physical_keys=False, SendInput=False,
                    clipboard=False, foreground_or_hkl_changed=False)
    finally:
        if process is not None and process.poll() is None:
            try:
                if context is not None and owned(context.top):
                    with ScintillaRangeTransport(context) as native:
                        native.msg(2014)  # Mark only our disposable synthetic buffer saved.
                    u.PostMessageW(context.top, 0x10, 0, 0)
                process.wait(timeout=5)
            except Exception:
                process.terminate()  # Exact own disposable child; never user Notepad++.
                process.wait(timeout=3)
        if attached:
            u.SetThreadDesktop(original)
        u.CloseDesktop(desktop)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = probe(Path(r'C:\Program Files\Notepad++\notepad++.exe'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
