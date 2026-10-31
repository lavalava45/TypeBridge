"""R16 six-direction native EDIT acceptance recorder (opt-in only).

Default: print a plan; no window, input, hook, HKL change, focus change or file.

--observe: User physically presses keys in our isolated, empty Win32 EDIT.
           The application measures pre-Space text/HKL, Space, Undo.
--inject:  A SECOND, synthetic-only mode. Requires explicit confirmation and
           an already running *test-only* TypeBridge --headless --accept-injected.
           Uses SendInput exclusively while its own test EDIT has foreground.
           Not evidence of genuine physical typing or No01 release acceptance.

Neither mode creates a global hook, runs/stops TypeBridge, touches an existing
document, reads the clipboard, alters registry/settings, or installs layouts.
"""

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


CONFIRMATION = 'AUTHORIZE_TYPEBRIDGE_TEST_WINDOW_INPUT'
LANGIDS = {'ru': 0x0419, 'en': 0x0409, 'he': 0x040D}


@dataclass(frozen=True)
class Case:
    source: str
    physical: str
    target: str
    expected: str
    untranslated: str

    @property
    def name(self):
        return self.source.upper() + ' → ' + self.target.upper()


# Fixed, non-sensitive, existing six-direction engine regression fixtures.
# 'physical' names the physical English-position keys (NOT pasted text).
CASES = (
    Case('ru', 'hello', 'en', 'hello', 'руддщ'),
    Case('he', 'hello', 'en', 'hello', 'אקךךם'),
    Case('en', 'ghbdtn', 'ru', 'привет', 'ghbdtn'),
    Case('he', 'ghbdtn', 'ru', 'привет', 'עינגאמ'),
    Case('en', 'akuo', 'he', 'שלום', 'akuo'),
    Case('ru', 'akuo', 'he', 'שלום', 'флгщ'),
)


def diagnostic(value, expected):
    """Keep unexpected text out of reports (even from the test-only EDIT)."""
    return {'matches_expected': value == expected,
            'length': len(value),
            'sha256': hashlib.sha256(value.encode('utf-8')).hexdigest()}


def classify(case, *, pre_space, pre_langid, after_space, after_langid,
             after_undo, focus_safe=True):
    """Pure result classifier; never infer physical input from automation."""
    early = bool(pre_space == case.expected and pre_langid == LANGIDS[case.target])
    after = bool(after_space == case.expected + ' '
                 and after_langid == LANGIDS[case.target])
    # EDIT undo groups differ. A single Ctrl+Z that merely removes Space
    # is NOT counted as having undone the word correction.
    restored = after_undo in (case.untranslated, case.untranslated + ' ')
    return {'direction': case.name, 'before_space': early and focus_safe,
            'space_exact': after and focus_safe, 'undo_restored_source': restored,
            'undo_changed_text': after_undo != after_space,
            'focus_safe': bool(focus_safe),
            'pre_space_observation': diagnostic(pre_space, case.expected),
            'after_space_observation': diagnostic(after_space, case.expected + ' '),
            'after_undo_observation': diagnostic(after_undo, case.untranslated),
            'pre_langid': f'{pre_langid:04x}',
            'after_langid': f'{after_langid:04x}'}


def plan_lines():
    return [f'{i}. {c.name}: source HKL {LANGIDS[c.source]:04X}, '
            f'physical keys {c.physical!r}, before Space {c.expected!r}, '
            f'target HKL {LANGIDS[c.target]:04X}'
            for i, c in enumerate(CASES, 1)]


def assert_injected_trial_running():
    """Fail closed: default TypeBridge intentionally ignores injected input."""
    import psutil
    try:
        all_processes = [p for p in psutil.process_iter(['name', 'exe', 'cmdline'])
                         if (p.info.get('name') or '').casefold() == 'typebridge.exe']
        running = {p.pid for p in all_processes}
        roots = [p for p in all_processes
                 if p.ppid() not in running]
        if len(roots) != 1:
            raise RuntimeError('Synthetic run requires exactly one TypeBridge process tree')
        master = roots[0]
        command = master.info.get('cmdline') or ()
        if '--headless' not in command or '--accept-injected' not in command:
            raise RuntimeError('Synthetic run requires special TypeBridge '
                               '--headless --accept-injected; standard TypeBridge ignores injection')
        allowed_pids = {master.pid} | {p.pid for p in master.children(recursive=True)}
        if any(p.pid not in allowed_pids for p in all_processes):
            raise RuntimeError('Another TypeBridge is running; do not inject')
        return master.pid
    except (psutil.Error, OSError) as error:
        raise RuntimeError('Cannot verify special TypeBridge trial process') from error


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG),
                ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD),
                ('wParamH', wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('data', INPUT_UNION)]


def keyboard_events(vk, *, ctrl=False, scan_code=None):
    """Return balanced KEYDOWN/KEYUP packets. Sending is a separate action."""
    if not (0x41 <= vk <= 0x5A or vk == 0x20):
        raise ValueError('Only fixture Latin letters and Space may be injected')
    if scan_code is not None and (scan_code <= 0 or scan_code > 0xFF):
        raise ValueError('Invalid keyboard scan code')

    def item(key, up=False, sc=None):
        flags = (0x0008 if sc is not None else 0) | (0x0002 if up else 0)
        return INPUT(1, INPUT_UNION(ki=KEYBDINPUT(0 if sc is not None else key,
                                                 sc or 0, flags, 0, 0)))

    pair = [item(vk, sc=scan_code), item(vk, up=True, sc=scan_code)]
    if ctrl:
        return (item(0x11), *pair, item(0x11, up=True))
    return tuple(pair)


class WindowsReader:
    """Only our own native EDIT is read or modified."""

    def __init__(self):
        if sys.platform != 'win32':
            raise RuntimeError('Win32 native EDIT test requires Windows')
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        u = self.user32
        u.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR,
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
            wintypes.HINSTANCE, ctypes.c_void_p)
        u.CreateWindowExW.restype = wintypes.HWND
        u.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        u.GetWindowTextW.restype = ctypes.c_int
        u.SetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
        u.SetWindowTextW.restype = wintypes.BOOL
        u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.c_void_p)
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
        u.GetKeyboardLayout.restype = ctypes.c_void_p
        u.GetKeyboardLayoutList.argtypes = (ctypes.c_int, ctypes.c_void_p)
        u.GetKeyboardLayoutList.restype = ctypes.c_int
        u.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        u.GetAncestor.restype = wintypes.HWND
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetFocus.restype = wintypes.HWND
        u.SetFocus.argtypes = (wintypes.HWND,)
        u.SetFocus.restype = wintypes.HWND
        u.ActivateKeyboardLayout.argtypes = (ctypes.c_void_p, wintypes.UINT)
        u.ActivateKeyboardLayout.restype = ctypes.c_void_p
        u.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        u.MapVirtualKeyW.restype = wintypes.UINT
        u.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        u.SendInput.restype = wintypes.UINT

    def create_edit(self, parent, width, height):
        # Native Windows EDIT: not Tk Entry. This exercises the actual native
        # editor pathway in TypeBridge, including its WM_CHAR/readback timing.
        style = 0x40000000 | 0x10000000 | 0x00800000 | 0x80
        handle = self.user32.CreateWindowExW(0, 'EDIT', '', style, 2, 2,
                                              width - 4, height - 4,
                                              parent, None, None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    def text(self, handle):
        size = self.user32.GetWindowTextLengthW(handle)
        if size < 0 or size > 4096:
            raise RuntimeError('Unexpected length in test-only EDIT')
        value = ctypes.create_unicode_buffer(size + 1)
        self.user32.GetWindowTextW(handle, value, len(value))
        return value.value

    def clear(self, handle):
        if not self.user32.SetWindowTextW(handle, ''):
            raise ctypes.WinError(ctypes.get_last_error())

    def langid(self, handle):
        thread = self.user32.GetWindowThreadProcessId(handle, None)
        return int(self.user32.GetKeyboardLayout(thread) or 0) & 0xFFFF

    def foreground_is_own_edit(self, handle):
        top = self.user32.GetAncestor(handle, 2)  # GA_ROOT
        foreground = self.user32.GetForegroundWindow()
        focused = self.user32.GetFocus()
        return bool(top and foreground == top and focused == handle)

    def loaded(self):
        u = self.user32
        count = u.GetKeyboardLayoutList(0, None)
        if count < 1 or count > 200:
            raise RuntimeError('Invalid HKL enumeration')
        result = (ctypes.c_void_p * count)()
        found = u.GetKeyboardLayoutList(count, result)
        return {int(item) & 0xFFFF: int(item) for item in result[:found] if item}

    def select_own_layout(self, handle, code):
        installed = self.loaded().get(LANGIDS[code])
        if installed is None:
            raise RuntimeError(f'{code.upper()} layout is not loaded in Windows')
        # Only permitted in the interactive, explicitly authorized SYNTHETIC
        # mode. This is our GUI thread, never the user's other application.
        if not self.foreground_is_own_edit(handle):
            raise RuntimeError('Test field is not foreground/focused')
        selected = self.user32.ActivateKeyboardLayout(ctypes.c_void_p(installed), 0)
        if not selected or self.langid(handle) != LANGIDS[code]:
            raise RuntimeError('Test-only HKL selection was not confirmed')

    def inject(self, handle, events):
        if not self.foreground_is_own_edit(handle):
            raise RuntimeError('Focus left the test field; injection aborted')
        data = (INPUT * len(events))(*events)
        delivered = self.user32.SendInput(len(events), data, ctypes.sizeof(INPUT))
        if delivered != len(events):
            raise RuntimeError(f'SendInput incomplete: {delivered}/{len(events)}')


class AcceptanceWindow:
    def __init__(self, mode):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.mode = mode
        self.api = WindowsReader()
        self.root = tk.Tk()
        self.root.title('TypeBridge — R16 six-direction test (NO release claim)')
        self.root.geometry('760x420')
        self.root.minsize(660, 400)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.index = 0
        self.generation = 0  # Invalidates callbacks from reset/previous trial.
        self.phase = 'idle'
        self.early = None
        self.space = None
        self.undo = None
        self.last_pre = None  # Last *observed* no-Space text/HKL, including failures.
        self.focus_safe = True
        self.results = []
        self.report_path = None
        self.label = tk.StringVar()
        self.hint = tk.StringVar()
        self.status = tk.StringVar(value='Готово к проверке. Ничего не вводится автоматически.'
                                    if mode == 'observe' else
                                    'Синтетический режим. Ввод только после кнопки «Начать».')
        ttk.Label(self.root, textvariable=self.label, font=('Segoe UI', 14, 'bold')).pack(pady=(18, 8))
        ttk.Label(self.root, textvariable=self.hint, wraplength=700,
                  justify='center').pack(padx=20, pady=5)
        frame = ttk.Frame(self.root, width=640, height=54)
        frame.pack(pady=20)
        frame.pack_propagate(False)
        self.root.update_idletasks()
        self.edit = self.api.create_edit(frame.winfo_id(), 640, 54)
        ttk.Label(self.root, textvariable=self.status, wraplength=700,
                  justify='center').pack(padx=20, pady=5)
        controls = ttk.Frame(self.root)
        controls.pack(pady=12)
        self.start_btn = ttk.Button(controls, text='Начать текущий сценарий',
                                    command=self.start_case)
        self.start_btn.pack(side='left', padx=7)
        self.next_btn = ttk.Button(controls, text='Следующий сценарий',
                                   command=self.next_case, state='disabled')
        self.next_btn.pack(side='left', padx=7)
        ttk.Button(controls, text='Сбросить только это поле', command=self.reset_case).pack(
            side='left', padx=7)
        ttk.Button(controls, text='Остановить / закрыть', command=self.close).pack(
            side='left', padx=7)
        self._show_case()
        self.root.after(25, self.poll)

    def _show_case(self):
        if self.index >= len(CASES):
            self.label.set('Проверка завершена — 6 направлений')
            self.hint.set('Результат сохранён в отчёте. Это не автоматическая '
                          'физическая приёмка других редакторов.')
            self.start_btn.configure(state='disabled')
            self.next_btn.configure(state='disabled')
            return
        case = CASES[self.index]
        self.label.set(f'{self.index + 1}/6: {case.name}')
        self.hint.set(f'Выбери {case.source.upper()} в Windows, нажми «Начать», '
                      f'потом физически набери клавиши {case.physical.upper()} '
                      f'(НЕ вставляй текст). Выжди секунду до Space; затем Space, '
                      f'затем Ctrl+Z. Ожидаем до Space: {case.expected}. '
                      f'Режим: {"наблюдение" if self.mode == "observe" else "синтетический"}.')
        self.start_btn.configure(state='normal')
        self.next_btn.configure(state='disabled')
        self.status.set('Выбор языка — вручную; тест не меняет чужие раскладки.')

    def _focus_edit(self):
        self.api.user32.SetFocus(self.edit)
        if not self.api.foreground_is_own_edit(self.edit):
            raise RuntimeError('Test-only EDIT is not foreground/focused')

    def start_case(self):
        if self.phase not in ('idle', 'done', 'aborted') or self.index >= len(CASES):
            return
        case = CASES[self.index]
        try:
            if self.mode == 'inject':
                assert_injected_trial_running()
            self._focus_edit()
            if self.mode == 'inject':
                self.api.select_own_layout(self.edit, case.source)
            elif self.api.langid(self.edit) != LANGIDS[case.source]:
                self.status.set(f'Сначала выбери раскладку {case.source.upper()} '
                                f'(LANGID {LANGIDS[case.source]:04X}) и нажми «Начать» ещё раз.')
                return
            self.api.clear(self.edit)
            self.generation += 1
            self.phase = 'typing'
            self.early = self.space = self.undo = self.last_pre = None
            self.focus_safe = True
            self.start_btn.configure(state='disabled')
            self.status.set('Набирай заданные клавиши; перед Space выдержи паузу.')
            if self.mode == 'inject':
                self._inject_letters(0, self.generation)
        except (RuntimeError, OSError) as error:
            self.status.set(f'Стоп: {error}')

    def _inject_letters(self, at, generation):
        if self.phase != 'typing' or self.generation != generation:
            return
        try:
            assert_injected_trial_running()
            case = CASES[self.index]
            if at == len(case.physical):
                self.root.after(1100, self._inject_space, generation)
                return
            vk = ord(case.physical[at].upper())
            sc = self.api.user32.MapVirtualKeyW(vk, 0)
            self.api.inject(self.edit, keyboard_events(vk, scan_code=sc))
            self.root.after(220, self._inject_letters, at + 1, generation)
        except (RuntimeError, OSError) as error:
            self._abort(str(error))

    def _inject_space(self, generation):
        if self.phase != 'typing' or self.generation != generation:
            return
        try:
            assert_injected_trial_running()
            self.api.inject(self.edit, keyboard_events(0x20,
                scan_code=self.api.user32.MapVirtualKeyW(0x20, 0)))
            self.root.after(900, self._inject_undo, generation)
        except (RuntimeError, OSError) as error:
            self._abort(str(error))

    def _inject_undo(self, generation):
        if self.generation != generation:
            return
        if self.phase != 'space':
            self._abort('Space was not observed; refusing to inject Undo')
            return
        try:
            assert_injected_trial_running()
            # Ctrl+Z is deliberately sent only to our still-focused EDIT.
            self.api.inject(self.edit, keyboard_events(0x5A, ctrl=True))
            self.root.after(750, self._finish, generation)
        except (RuntimeError, OSError) as error:
            self._abort(str(error))

    def poll(self):
        if self.phase in ('typing', 'space'):
            try:
                if not self.api.foreground_is_own_edit(self.edit):
                    self.focus_safe = False
                    self._abort('Foreground/focus left test-only EDIT')
                else:
                    text = self.api.text(self.edit)
                    lang = self.api.langid(self.edit)
                    case = CASES[self.index]
                    if self.phase == 'typing':
                        if ' ' not in text:
                            # Do not reduce a failed pre-Space state to an empty
                            # observation. Preserve only a hash and length in
                            # the final report; never log arbitrary typed text.
                            self.last_pre = (text, lang)
                            if text == case.expected and lang == LANGIDS[case.target]:
                                self.early = (text, lang)
                                self.status.set('До Space: ожидаемое слово и целевая HKL подтверждены.')
                        if ' ' in text:
                            self.space = (text, lang)
                            self.phase = 'space'
                            self.status.set('После Space зафиксировано. Теперь один Ctrl+Z.')
                    elif self.phase == 'space' and text != self.space[0]:
                        self.undo = text
                        self._finish()
            except (RuntimeError, OSError) as error:
                self._abort(str(error))
        if self.phase != 'closed':
            self.root.after(25, self.poll)

    def _finish(self, generation=None):
        if (self.phase not in ('typing', 'space')
                or (generation is not None and generation != self.generation)):
            return
        case = CASES[self.index]
        pre = self.early or ('', 0)
        after = self.space or ('', 0)
        undone = self.undo if self.undo is not None else after[0]
        record = classify(case, pre_space=pre[0], pre_langid=pre[1],
                          after_space=after[0], after_langid=after[1],
                          after_undo=undone, focus_safe=self.focus_safe)
        last = self.last_pre or ('', 0)
        record['last_no_space_observation'] = diagnostic(last[0], case.expected)
        record['last_no_space_langid'] = f'{last[1]:04x}'
        record['input_mode'] = 'physical_user_keys' if self.mode == 'observe' else 'injected_SendInput'
        record['physical_case_pass'] = (self.mode == 'observe' and
                                        record['before_space'] and record['space_exact'] and
                                        record['undo_restored_source'])
        record['release_gate_blocked'] = True  # One native EDIT is not all editors.
        self.results.append(record)
        self.phase = 'done'
        self.start_btn.configure(state='disabled')
        self.next_btn.configure(state='normal')
        self._save_report()
        self.status.set('Итог сценария: раннее=' + str(record['before_space']) +
                        ', Space=' + str(record['space_exact']) +
                        ', Undo вернул исходное=' + str(record['undo_restored_source']) +
                        '. Нажми «Следующий».')
        if self.mode == 'inject':
            self.root.after(1250, self.next_case)

    def _abort(self, reason):
        self.generation += 1
        self.phase = 'aborted'
        self.start_btn.configure(state='normal')
        self.next_btn.configure(state='disabled')
        self.status.set('Испытание остановлено без дальнейших нажатий: ' + reason)

    def reset_case(self):
        # A pending synthetic callback must cease at its next guard; never
        # replay it into an unrelated application after an explicit reset.
        if self.phase == 'closed':
            return
        self.generation += 1
        self.phase = 'idle'
        self.early = self.space = self.undo = self.last_pre = None
        self.focus_safe = True
        self.api.clear(self.edit)
        self._show_case()

    def _save_report(self):
        if self.report_path is None:
            folder = Path(__file__).resolve().parent / 'build'
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            self.report_path = folder / f'r16-six-direction-{stamp}.json'
        document = {'schema': 1,
                    'input_mode': 'PHYSICAL_USER' if self.mode == 'observe' else 'SYNTHETIC_INJECTED',
                    'not_a_release_acceptance': True,
                    'results': self.results}
        content = (json.dumps(document, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
        # Single-test output only, not a personal settings file; no raw
        # unexpected words are recorded. Atomic update of this test's report.
        from tempfile import NamedTemporaryFile
        import os
        temporary = None
        try:
            with NamedTemporaryFile('wb', dir=self.report_path.parent,
                                    prefix='.trial-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.report_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def next_case(self):
        if self.phase != 'done':
            return
        self.generation += 1
        self.index += 1
        self.phase = 'idle'
        self._show_case()
        if self.mode == 'inject' and self.index < len(CASES):
            self.root.after(900, self._start_auto_next, self.generation)

    def _start_auto_next(self, generation):
        if self.generation == generation and self.phase == 'idle':
            self.start_case()

    def close(self):
        self.generation += 1
        self.phase = 'closed'
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--plan', action='store_true', help='safe default: show only six test cases')
    mode.add_argument('--observe', action='store_true',
                      help='opt-in own native EDIT window; user presses real keys')
    mode.add_argument('--inject', action='store_true',
                      help='opt-in synthetic SendInput; requires special TypeBridge test mode')
    parser.add_argument('--consent', help='explicit test-only approval token')
    args = parser.parse_args(argv)
    if not args.observe and not args.inject:
        print('R16 — six direction acceptance test PLAN ONLY. No OS input.')
        print('\n'.join(plan_lines()))
        print('Observe: real user keys, own empty native EDIT; --observe --consent TOKEN')
        print('Inject: synthetic only, needs TypeBridge --headless --accept-injected; '
              '--inject --consent TOKEN')
        return 0
    if args.consent != CONFIRMATION:
        parser.error('Live window/input disabled without explicit authorization token')
    if args.inject:
        assert_injected_trial_running()
    AcceptanceWindow('inject' if args.inject else 'observe').run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
