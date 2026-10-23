"""Opt-in live trial, limited to its own empty RichEdit field. Not the main app.

Opening this file installs no hooks. Pressing Start explicitly installs the
existing hook on a worker; context() refuses every other window and field.
"""
import ctypes
from ctypes import wintypes as w
import queue
import threading
import tkinter as tk
from tkinter import ttk

from engine import LanguageEngine, source_data_dir, LANGS
from typebridge_app import LiveHook, Settings, FocusContext, GuiThreadInfo, WM_INPUTLANGCHANGEREQUEST
from incremental_recognition import suggest_live_early, prepare_early
from early_native_edit import NativeEditBridge
from early_scintilla import replace_prefix
from early_tom import NativeUndoGroup


class TrialHook(LiveHook):
    def __init__(self, engine, top, field, emit):
        settings = Settings(early_correction=True, manual_f8=False, cycle_key='Отключено',
                            direct_ru='Отключено', direct_en='Отключено', direct_he='Отключено')
        super().__init__(engine, settings, emit)
        self.trial_top, self.trial_field = top, field

    def run(self):
        # Bind the marshalled object on this thread before installing the hook.
        # COM calls are not a proven bounded production transport; trial only.
        self.undo_group = NativeUndoGroup(self.trial_field)
        try:
            super().run()
        finally:
            self.undo_group.close()

    def context(self):
        if int(self.user32.GetForegroundWindow() or 0) != self.trial_top:
            return None
        pid = w.DWORD()
        thread = int(self.user32.GetWindowThreadProcessId(self.trial_top, ctypes.byref(pid)))
        gui = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        if not self.user32.GetGUIThreadInfo(thread, ctypes.byref(gui)) or gui.hwndFocus != self.trial_field:
            return None
        language = int(self.user32.GetKeyboardLayout(thread) or 0) & 0xFFFF
        name = next((key for key, value in LANGS.items() if value == language), None)
        if name is None:
            return None
        return FocusContext(self.trial_top, self.trial_field, thread, name,
                            'typebridge-trial', 'isolated live trial', self.trial_field)

    def _correct(self, *args, **kwargs):
        self.clear_token(preserve_snippets=True)
        return False  # This trial measures early replacement only, no boundary fallback.

    def _try_early(self, context):
        if self.paused or getattr(self, 'early_done', False) or getattr(self, 'early_disabled', False):
            return False
        decision = suggest_live_early(self.engine, self.keys, context.language)
        if decision is None:
            return False
        held = self.keys[-1][0]
        def valid():
            current = self.context()
            return (current is not None and self._word_scope(current) == self._word_scope(context)
                    and current.language == context.language
                    and not any(self.pressed(vk) for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C)))
        bridge = NativeEditBridge(context, valid, pending_char=decision.typed[-1],
                                  undo_group=getattr(self, 'undo_group', None))
        try:
            with bridge:
                outcome = replace_prefix(bridge, decision.typed[:-1], decision.replacement, valid)
        except Exception as error:
            self.early_disabled = True
            self.emit('error', 'Стенд отключён: ' + type(error).__name__)
            return False
        if bridge.timeout or bridge.budget_exceeded:
            self.early_disabled = True
            self.emit('error', 'Стенд отключён: поле не ответило вовремя')
        if outcome.state == 'refused':
            return False
        self.suppressed_keyups.add(held)
        if outcome.state != 'verified':
            self.early_disabled = True
            self.clear_token()
            self.token_blocked = True
            self.emit('error', 'Неопределённый результат: повтор отключён')
            return True
        hkl = int(self.engine.mapper.layouts[decision.language])
        answer = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(context.layout_window, WM_INPUTLANGCHANGEREQUEST,
                                        0, hkl, 3, 30, ctypes.byref(answer))
        observed = int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFF
        if observed != LANGS[decision.language]:
            self.early_disabled = True
            self.clear_token()
            self.token_blocked = True
            self.emit('error', 'Текст подтверждён, смена языка не подтверждена')
        else:
            self.last_language = decision.language
            self.early_done = True
            self.emit('ok', f'До пробела: {context.language.upper()} → {decision.language.upper()}')
        return True


def main():
    root = tk.Tk()
    root.title('TypeBridge — отдельный стенд раннего исправления')
    root.geometry('760x460')
    frame = ttk.Frame(root, padding=14)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Стенд исправляет только собственное поле ниже. Другие окна не обрабатываются.\n'
              'Перед проверкой отключи автоисправление в основном TypeBridge, чтобы два\n'
              'переключателя не обрабатывали одно и то же слово. Здесь текст не сохраняется.\n'
              'Undo пока не прошёл приёмку — вводи только тестовый текст.').pack(anchor='w')
    host = ttk.Frame(frame, height=220)
    host.pack(fill='both', expand=True, pady=12)
    root.update_idletasks()
    import win32gui
    import win32api
    win32api.LoadLibrary('Msftedit.dll')
    field = win32gui.CreateWindowEx(0x200, 'RichEdit50W', '', 0x40000000 | 0x10000000 | 4 | 0x1000 | 0x10000,
                                    0, 0, host.winfo_width(), host.winfo_height(), host.winfo_id(), 0, 0, None)
    top = win32gui.GetAncestor(field, 2)
    host.bind('<Configure>', lambda event: win32gui.MoveWindow(field, 0, 0, event.width, event.height, True))
    events = queue.Queue()
    status = tk.StringVar(value='Остановлен. Нажми «Включить стенд», затем щёлкни поле ввода.')
    ttk.Label(frame, textvariable=status, wraplength=710).pack(anchor='w')
    state = {'hook': None, 'worker': None, 'closing': False}
    def start():
        if state['closing']:
            return
        if state['worker'] and state['worker'].is_alive():
            return
        try:
            engine = LanguageEngine(source_data_dir())
            prepare_early(engine)
            hook = TrialHook(engine, top, field, lambda kind, text: events.put(text))
            state['hook'] = hook
            def run():
                try: hook.run()
                except Exception as error: events.put('Ошибка стенда: ' + type(error).__name__)
            state['worker'] = threading.Thread(target=run, daemon=True)
            state['worker'].start()
        except Exception as error:
            status.set('Не удалось запустить: ' + type(error).__name__)
    def stop():
        if state['hook']: state['hook'].request_stop()
    row = ttk.Frame(frame)
    row.pack(fill='x', pady=(10, 0))
    ttk.Button(row, text='Включить стенд', command=start).pack(side='left')
    ttk.Button(row, text='Остановить стенд', command=stop).pack(side='left', padx=8)
    def close():
        state['closing'] = True
        stop()
        if state['worker'] and state['worker'].is_alive():
            root.after(50, close)
        else:
            root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    def drain():
        while not events.empty(): status.set(events.get_nowait())
        if not state['closing']: root.after(50, drain)
    drain()
    root.mainloop()


if __name__ == '__main__':
    main()
