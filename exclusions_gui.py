"""Explicit application exclusions editor; changes apply on Save only."""
import tkinter as tk
from tkinter import ttk

from app_exclusions import CATEGORIES, PASSWORD_PROCESSES


class ExclusionsWindow:
    def __init__(self, root, app):
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title('TypeBridge — Исключения приложений')
        self.window.geometry('780x730')
        self.window.minsize(700, 660)
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='В этих приложениях TypeBridge пропускает клавиши и не исправляет текст. '
                  'Команды буфера и сохранение его истории также отключаются для исключённых приложений.',
                  wraplength=710).pack(anchor='w', pady=(0, 12))
        passwords = ttk.LabelFrame(frame, text='Хранители паролей и системный вход — всегда исключены', padding=8)
        passwords.pack(fill='x', pady=4)
        self._names(passwords, PASSWORD_PROCESSES, 2)
        self.categories = {}
        for name, (title, processes) in CATEGORIES.items():
            box = ttk.LabelFrame(frame, padding=8)
            box.pack(fill='x', pady=4)
            variable = tk.BooleanVar(value=app.excluded_categories[name].get())
            self.categories[name] = variable
            ttk.Checkbutton(box, text=f'Исключать: {title}', variable=variable).pack(anchor='w')
            self._names(box, processes, 4)
        ttk.Label(frame, text='Дополнительные исключения: имена .exe через запятую или с новой строки').pack(
            anchor='w', pady=(12, 4))
        self.custom = tk.Text(frame, height=4, wrap='word')
        self.custom.pack(fill='both', expand=True)
        self.custom.insert('1.0', app.excluded.get())
        ttk.Label(frame, text='IDE и творческие программы исключаются по умолчанию. Чтобы разрешить '
                  'в них обработку, сними галочку категории. Для отдельных приложений используй '
                  'дополнительный список. Неизвестные программы автоматически не распознаются.',
                  wraplength=710).pack(anchor='w', pady=10)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Сохранить настройки', command=self.save).pack(side='left')
        ttk.Button(buttons, text='Отмена', command=self.window.destroy).pack(side='right')

    @staticmethod
    def _names(parent, names, height):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(4, 0))
        text = tk.Text(row, height=height, wrap='word', relief='flat')
        scroll = ttk.Scrollbar(row, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(side='left', fill='x', expand=True)
        text.insert('1.0', ', '.join(names))
        text.configure(state='disabled')

    def save(self):
        old_text = self.app.excluded.get()
        old_categories = {name: var.get() for name, var in self.app.excluded_categories.items()}
        self.app.excluded.set(self.custom.get('1.0', 'end-1c').replace('\n', ','))
        for name, variable in self.categories.items():
            self.app.excluded_categories[name].set(variable.get())
        if self.app.save() is None:
            self.app.excluded.set(old_text)
            for name, value in old_categories.items():
                self.app.excluded_categories[name].set(value)
        else:
            self.window.destroy()
