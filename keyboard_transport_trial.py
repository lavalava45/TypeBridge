"""Opt-in SendInput experiment in TWO disposable native fields.

Opening installs no hooks and sends no input. The Run button installs a guard
and sends a held Unicode trigger which the guard consumes; the correction batch
is then submitted FROM the low-level callback. Recognition is deliberately a
fixed fixture, not a new dictionary. Delivery uses identical SendInput packets
for EDIT and RichEdit. WM_SETTEXT is used only to prepare disposable fixtures;
WM_GETTEXT/EM_GETSEL are read-only measurement, not correction transports.
"""
import ctypes
from ctypes import wintypes as w
import json
import threading
import time
import tkinter as tk
from tkinter import ttk

from early_keyboard_probe import (Field, Key, Transaction, plan, input_batch,
                                  decode_marker, MARKER_MASK, REPLAY_MARKER)
from early_native_edit import utf16_position
from typebridge_app import Input, KbdHook, GuiThreadInfo
from engine import LayoutMapper, LANGS

KICK = 0x5444000000000000
TAIL = 0x5445000000000000
CLEANUP = 0x5446000000000000
CASES = (
    ('ru', 'руддщ', 'hello', 'en'), ('he', 'יקךךם', 'hello', 'en'),
    ('en', 'ghbdtn', 'привет', 'ru'), ('he', 'עינגאמ', 'привет', 'ru'),
    ('en', 'akuo', 'שלום', 'he'), ('ru', 'флгщ', 'שלום', 'he'))


class Trial:
    def __init__(self):
        import win32gui, win32api
        self.gui = win32gui
        self.root = tk.Tk()
        self.root.title('TypeBridge — общий клавиатурный способ / отдельный опыт')
        self.root.geometry('850x570')
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.u.GetForegroundWindow.restype = w.HWND
        self.u.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GuiThreadInfo)]
        self.u.GetKeyboardLayout.argtypes, self.u.GetKeyboardLayout.restype = [w.DWORD], w.HANDLE
        self.u.SendInput.argtypes, self.u.SendInput.restype = [w.UINT, ctypes.POINTER(Input), ctypes.c_int], w.UINT
        self.u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, ctypes.c_ssize_t,
                                               w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.u.SendMessageTimeoutW.restype = ctypes.c_size_t
        self.u.GetAsyncKeyState.argtypes, self.u.GetAsyncKeyState.restype = [ctypes.c_int], ctypes.c_short
        self.u.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, w.HINSTANCE, w.DWORD]
        self.u.SetWindowsHookExW.restype = w.HANDLE
        self.u.CallNextHookEx.argtypes = [w.HANDLE, ctypes.c_int, w.WPARAM, w.LPARAM]
        self.u.CallNextHookEx.restype = ctypes.c_ssize_t
        self.u.UnhookWindowsHookEx.argtypes = [w.HANDLE]
        self.u.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
        self.u.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        self.u.GetMessageW.restype = ctypes.c_int
        self.k.GetModuleHandleW.argtypes, self.k.GetModuleHandleW.restype = [w.LPCWSTR], w.HMODULE
        self.gui_thread = self.k.GetCurrentThreadId()
        self.mapper = None
        self.transaction = None
        self.worker = self.callback = self.hook = None
        self.thread = 0
        self.ready = threading.Event()
        self.sequence = 0
        self.closing = False
        self.kick_seen = 0
        self.kick_count = 0
        self.replay_seen = set()
        self.replay_keys_down = set()
        self.replay_count = 0
        self.replaying = False
        self.extra = []
        self.trace = []
        self.results = []
        self.running = False
        self.cleanup_started = False
        self.cleanup_count = self.cleanup_seen = 0
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Исследовательский стенд. Только собственные пустые поля.\n'
                  'Не печатай и не переключай окна во время опыта. Hooks включаются кнопкой.\n'
                  'Рабочий TypeBridge должен быть выключен. Ошибки не исправляются вслепую.').pack(anchor='w')
        self.kind = tk.StringVar(value='EDIT')
        self.case = tk.StringVar(value='ru → he')
        options = ttk.Frame(frame); options.pack(fill='x', pady=8)
        ttk.Combobox(options, textvariable=self.kind, values=('EDIT', 'RichEdit50W'), state='readonly', width=16).pack(side='left')
        ttk.Combobox(options, textvariable=self.case,
                     values=tuple(f'{a} → {d}' for a, b, c, d in CASES), state='readonly', width=16).pack(side='left', padx=8)
        self.tail = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text='Поставить пробел + F в очередь ПЕРЕД заменой (стресс порядка)', variable=self.tail).pack(anchor='w')
        self.fields = {}
        win32api.LoadLibrary('Msftedit.dll')
        for name in ('EDIT', 'RichEdit50W'):
            ttk.Label(frame, text=name).pack(anchor='w', pady=(10, 0))
            host = ttk.Frame(frame, height=90); host.pack(fill='x')
            self.root.update_idletasks()
            hwnd = win32gui.CreateWindowEx(0x200, name, '', 0x40000000|0x10000000|4|0x1000|0x10000,
                                           0, 0, 810, 90, host.winfo_id(), 0, 0, None)
            host.bind('<Configure>', lambda event, hwnd=hwnd: win32gui.MoveWindow(hwnd, 0, 0, event.width, event.height, True))
            self.fields[name] = hwnd
        self.top = win32gui.GetAncestor(next(iter(self.fields.values())), 2)
        self.status = tk.StringVar(value='Остановлен; ввод и hooks не включены.')
        ttk.Label(frame, textvariable=self.status, wraplength=810).pack(anchor='w', pady=12)
        self.run_button = ttk.Button(frame, text='Проверить SendInput', command=self.run_case)
        self.run_button.pack(anchor='w')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(5, self.poll)

    def msg(self, command, wp=0, lp=0):
        answer = ctypes.c_size_t()
        if not self.u.SendMessageTimeoutW(self.field, command, wp, lp, 3, 30, ctypes.byref(answer)):
            raise TimeoutError('trial-field-message')
        return answer.value

    def scope(self):
        if int(self.u.GetForegroundWindow() or 0) != self.top: return ()
        gui = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        if not self.u.GetGUIThreadInfo(self.gui_thread, ctypes.byref(gui)) or gui.hwndFocus != self.field: return ()
        return (self.top, self.field, self.gui_thread)

    def observe(self):
        start, end = w.DWORD(), w.DWORD()
        self.msg(0xB0, ctypes.addressof(start), ctypes.addressof(end))
        length = self.msg(0xE)
        if length > 32768: raise ValueError('trial-document-limit')
        value = ctypes.create_unicode_buffer(length+1)
        self.msg(0xD, length+1, ctypes.addressof(value))
        text = value.value.replace('\r\n', '\r')
        caret = len(text.encode()[:utf16_position(text, end.value)].decode())
        language_id = int(self.u.GetKeyboardLayout(self.gui_thread) or 0) & 0xFFFF
        language = next((key for key, code in LANGS.items() if code == language_id), '')
        return Field(self.scope(), text, caret, language, start.value != end.value)

    def send(self, keys, sequence=None, tag=None):
        batch = input_batch(keys, sequence=sequence)
        if tag is not None:
            for item in batch: item.u.ki.dwExtraInfo = tag
        return int(self.u.SendInput(len(batch), batch, ctypes.sizeof(Input)))

    def guard(self, code, message, pointer):
        if code < 0: return self.u.CallNextHookEx(self.hook, code, message, pointer)
        event = ctypes.cast(pointer, ctypes.POINTER(KbdHook)).contents
        extra = int(event.dwExtraInfo)
        tx = self.transaction
        if extra == CLEANUP:
            self.cleanup_seen += 1
            if message not in (0x101,0x105): return 1
            identity = ((0,int(event.scanCode),4) if event.vkCode == 0xE7
                        else (int(event.vkCode),0,0))
            if tx: tx.keys_down.discard(identity)
            self.replay_keys_down.discard(int(event.vkCode))
            return self.u.CallNextHookEx(self.hook,code,message,pointer)
        if extra == KICK:
            self.kick_seen += 1
            if message in (0x100, 0x104) and tx is not None:
                if self.scope() != tx.plan.source.scope or self.closing:
                    tx.submitted(0)
                else:
                    # Deliberately queue a following token before the correction.
                    # This is synthetic stress input, never claimed physical.
                    if self.with_tail:
                        keys = (Key(vk=32), Key(vk=32, flags=2), Key(vk=70), Key(vk=70, flags=2))
                        self.send(keys, tag=TAIL)
                    tx.submitted(self.send(tx.plan.events, sequence=self.sequence))
            return 1
        own = decode_marker(extra)
        if own:
            if tx is None or own[0] != self.sequence: return 1
            modifiers = any(self.u.GetAsyncKeyState(vk) & 0x8000 for vk in (16,17,18,91,92))
            forward = tx.own_event(own[1], self.scope(), modifiers)
            self.trace.append(('own', own[1], forward))
            return self.u.CallNextHookEx(self.hook, code, message, pointer) if forward else 1
        if extra & MARKER_MASK == REPLAY_MARKER:
            self.replay_seen.add(extra & 0xFFFF)
            up = message in (0x101,0x105)
            vk = int(event.vkCode)
            cleanup = up and vk in self.replay_keys_down
            modifiers = any(self.u.GetAsyncKeyState(key)&0x8000 for key in (16,17,18,91,92))
            allowed = cleanup or (tx and not tx.cancelled and self.scope() == tx.plan.source.scope
                                  and not self.closing and not modifiers)
            if allowed:
                if up: self.replay_keys_down.discard(vk)
                else: self.replay_keys_down.add(vk)
                return self.u.CallNextHookEx(self.hook,code,message,pointer)
            if tx: tx.abort('tail-context-or-modifier-changed')
            return 1
        if tx is not None and self.running:
            raw = Key(vk=int(event.vkCode), scan=int(event.scanCode),
                      flags=(2 if message in (0x101,0x105) else 0) | (int(event.flags)&1))
            if raw.vk not in (8,32) and not 0x30 <= raw.vk <= 0x5A:
                tx.abort('control-input-during-probe')
                return self.u.CallNextHookEx(self.hook,code,message,pointer)
            if self.replaying and self.scope() == tx.plan.source.scope:
                self.extra.append(raw); return 1
            if tx.physical_event(raw, self.scope()):
                self.trace.append(('tail', raw.vk, raw.flags)); return 1
        return self.u.CallNextHookEx(self.hook, code, message, pointer)

    def hook_loop(self):
        self.thread = self.k.GetCurrentThreadId()
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM)
        def guarded(code, message, pointer):
            try:
                return self.guard(code, message, pointer)
            except Exception as error:
                if self.transaction: self.transaction.abort('guard-'+type(error).__name__)
                if code >= 0:
                    extra = int(ctypes.cast(pointer, ctypes.POINTER(KbdHook)).contents.dwExtraInfo)
                    if extra in (KICK,TAIL,CLEANUP) or decode_marker(extra) or extra & MARKER_MASK == REPLAY_MARKER:
                        return 1
                return self.u.CallNextHookEx(self.hook,code,message,pointer)
        self.callback = callback_type(guarded)
        self.hook = self.u.SetWindowsHookExW(13, self.callback, self.k.GetModuleHandleW(None), 0)
        self.ready.set()
        if not self.hook: return
        try:
            message = w.MSG()
            while self.u.GetMessageW(ctypes.byref(message), None, 0, 0) > 0: pass
        finally:
            self.u.UnhookWindowsHookEx(self.hook); self.hook = None

    def run_case(self):
        if self.running or self.closing: return
        try:
            if self.mapper is None: self.mapper = LayoutMapper()
            if self.worker is None:
                self.worker = threading.Thread(target=self.hook_loop, daemon=True)
                self.worker.start()
                if not self.ready.wait(2) or not self.hook: raise OSError('guard-hook-unavailable')
            self.field = self.fields[self.kind.get()]
            self.source, typed, replacement, self.target = next(row for row in CASES if f'{row[0]} → {row[3]}' == self.case.get())
            value = ctypes.create_unicode_buffer('начало\r'+typed[:-1]+' конец')
            self.msg(0xC, 0, ctypes.addressof(value))
            caret = 7 + len(typed)-1
            self.msg(0xB1, caret, caret)
            self.gui.SetFocus(self.field)
            self.msg(0x50, 0, int(self.mapper.layouts[self.source]))
            observed = self.observe()
            if not observed.scope or observed.language != self.source: raise ValueError('trial-focus-or-layout')
            self.transaction = Transaction(plan(observed, typed, replacement, self.target))
            self.sequence += 1
            self.trace = []; self.extra = []; self.replay_seen = set(); self.replay_keys_down = set()
            self.kick_seen = self.kick_count = self.replay_count = 0
            self.cleanup_started = False
            self.cleanup_count = self.cleanup_seen = 0
            self.with_tail = self.tail.get()
            self.replaying = False; self.running = True
            self.run_button.configure(state='disabled')
            self.deadline = time.perf_counter()+1
            self.kick_count = self.send((Key(scan=ord(typed[-1]), flags=4), Key(scan=ord(typed[-1]), flags=6)), tag=KICK)
            if self.kick_count == 0 and self.transaction.accepted is None:
                self.transaction.submitted(0)
            self.status.set('Пачка отправляется из low-level callback; ожидается подтверждение поля…')
        except Exception as error:
            self.status.set('Ошибка подготовки: '+type(error).__name__+' / '+str(error))

    def finish(self, ok, reason):
        self.results.append({'field':self.kind.get(),'source':self.source,'target':self.target,
                             'ok':ok,'reason':reason,'trace':list(self.trace),
                             'pending_input':len(self.transaction.buffer)+len(self.extra)})
        self.running = False
        self.status.set(('Подтверждено: ' if ok else 'НЕ ПОДТВЕРЖДЕНО: ')+reason)
        self.run_button.configure(state='normal')

    def poll(self):
        tx = self.transaction
        try:
            if self.running and tx:
                if self.scope() != tx.plan.source.scope: tx.abort('focus-changed')
                if self.replaying and not tx.cancelled:
                    if len(self.replay_seen) >= self.replay_count:
                        if self.extra:
                            batch,self.extra=tuple(self.extra),[]
                            self.replay_seen=set(); self.replay_count=self.send(batch)
                            if self.replay_count != len(batch): tx.abort('partial-tail-replay')
                        else:
                            actual = self.observe()
                            first = tx.plan.expected.caret
                            suffix = ' '+self.mapper.char(70,self.target,False) if self.with_tail else ''
                            expected = tx.plan.expected.text[:first]+suffix+tx.plan.expected.text[first:]
                            if actual.text == expected:
                                self.finish(True,'SendInput + отложенный хвост; реальный текст совпал')
                elif tx.state == 'verifying':
                    actual = self.observe()
                    if (actual.scope == tx.plan.expected.scope and actual.text == tx.plan.expected.text
                            and actual.caret == tx.plan.expected.caret and not actual.selection):
                        self.msg(0x50,0,int(self.mapper.layouts[self.target]))
                        if tx.confirm(self.observe()):
                            self.replaying=True
                            buffered=tx.release(self.scope())
                            self.replay_count=self.send(buffered) if buffered else 0
                            if self.replay_count != len(buffered): tx.abort('partial-tail-replay')
                if tx.state in ('uncertain','refused') and tx.drained:
                    releases = tx.pending_keyups()+tuple(Key(vk=vk,flags=2) for vk in self.replay_keys_down)
                    if releases and not self.cleanup_started:
                        self.cleanup_started=True
                        self.cleanup_count=self.send(releases,tag=CLEANUP)
                    if self.cleanup_seen>=self.cleanup_count:
                        self.finish(False,tx.reason+'; повтор/откат не выполнялся')
                elif self.running and time.perf_counter()>self.deadline:
                    tx.abort('timeout')
                    if tx.drained: self.finish(False,'timeout; ввод сохранён в памяти, повтор запрещён')
            if self.closing:
                drained = ((tx is None or tx.drained) and self.kick_seen>=self.kick_count
                           and len(self.replay_seen)>=self.replay_count and self.cleanup_seen>=self.cleanup_count)
                if drained:
                    if self.thread: self.u.PostThreadMessageW(self.thread,0x12,0,0)
                    if self.worker and self.worker.is_alive():
                        self.root.after(5,self.poll); return
                    self.root.destroy(); return
        except Exception as error:
            if tx: tx.abort(type(error).__name__)
            self.status.set('Проверка прервана: '+type(error).__name__)
        self.root.after(5,self.poll)

    def close(self):
        self.closing=True
        if self.transaction and self.running: self.transaction.abort('closing')
        self.status.set('Закрытие: ожидается завершение своих событий, затем снятие hook…')


if __name__ == '__main__':
    app=Trial()
    app.root.mainloop()
    # Contains only disposable fixture observations, not other apps' text.
    if app.results:
        from pathlib import Path
        destination=Path(__file__).with_name('build')/'keyboard-live-report.json'
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(app.results,ensure_ascii=False,indent=2),encoding='utf-8')
