"""Explicit opt-in settings and recovery viewer for encrypted editor snapshots."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox


class JournalWindow:
    def __init__(self, root, app):
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title('TypeBridge — Дневник ввода')
        self.window.geometry('800x760')
        self.window.minsize(720, 680)
        self.limit = 200
        self.rows = {}
        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, textvariable=app.journal_status,
                  font=('Segoe UI', 11, 'bold'), foreground='#a12b20').pack(anchor='w')
        self.enabled = tk.BooleanVar(value=app.journal_enabled.get())
        ttk.Checkbutton(frame, text='Включить дневник текста', variable=self.enabled).pack(anchor='w', pady=8)
        row = ttk.Frame(frame)
        row.pack(fill='x')
        ttk.Label(row, text='Автоочистка: удалять записи старше').pack(side='left')
        self.days = tk.StringVar(value=app.journal_days.get())
        ttk.Spinbox(row, from_=1, to=365, textvariable=self.days, width=6).pack(side='left', padx=6)
        ttk.Label(row, text='дней').pack(side='left')
        ttk.Label(frame, text='Разрешённые приложения (.exe через запятую):').pack(anchor='w', pady=(8, 3))
        self.apps = tk.StringVar(value=app.journal_apps.get())
        ttk.Entry(frame, textvariable=self.apps).pack(fill='x')
        ttk.Label(frame, text='Снимки активного текстового поля раз в 2 секунды; сохраняются также '
                  'вставки и исправления. По умолчанию — Блокнот и Notepad++ (до 64 КБ в UTF-8). '
                  'Другие приложения требуют поддерживаемого обычного текстового поля. '
                  'Парольные, неизвестные поля и исключённые приложения пропускаются. '
                  'Браузеры пока не поддерживаются.', wraplength=750).pack(anchor='w', pady=8)
        ttk.Label(frame, text='Хранение локальное, с шифрованием для текущего пользователя Windows. '
                  'Последний ввод между снимками может не сохраниться. Это история снимков, '
                  'а не точная копия документа с оформлением. Отключение полностью останавливает '
                  'чтение и запись; старые записи остаются до очистки.', wraplength=750).pack(anchor='w')
        ttk.Button(frame, text='Сохранить настройки', command=self.save).pack(anchor='w', pady=8)
        self.list = ttk.Treeview(frame, columns=('time', 'app'), show='headings', height=6, selectmode='browse')
        self.list.heading('time', text='Время снимка')
        self.list.heading('app', text='Приложение')
        self.list.pack(fill='both', expand=True)
        self.list.bind('<<TreeviewSelect>>', self.select)
        self.text = tk.Text(frame, height=7, wrap='word', state='disabled')
        self.text.pack(fill='both', expand=True, pady=8)
        row = ttk.Frame(frame)
        row.pack(fill='x')
        for title, action in (('Обновить', self.refresh), ('Показать ещё', self.more),
                              ('Копировать текст', self.copy), ('Удалить все записи', self.clear)):
            ttk.Button(row, text=title, command=action).pack(side='left', padx=(0, 5))
        ttk.Label(frame, text='База: %LOCALAPPDATA%\\TypeBridge\\journal\\journal.sqlite3. '
                  'Не более 50 МБ зашифрованных данных / 20 000 снимков.', wraplength=750).pack(anchor='w', pady=(8, 0))
        self.refresh()

    def save(self):
        try:
            days = int(self.days.get())
            apps = tuple(x.strip().casefold() for x in self.apps.get().split(',') if x.strip())
            self.app.apply_journal_settings(self.enabled.get(), days, apps)
        except (ValueError, OSError) as error:
            messagebox.showerror('Дневник', str(error), parent=self.window)

    def refresh(self):
        self.rows.clear()
        self.list.delete(*self.list.get_children())
        try:
            for identifier, created, app in self.app.journal_service.store.list(self.limit):
                row = self.list.insert('', 'end', values=(datetime.fromtimestamp(created).strftime('%Y-%m-%d %H:%M:%S'), app))
                self.rows[row] = identifier
        except Exception:
            messagebox.showerror('Дневник', 'Не удалось прочитать записи дневника.', parent=self.window)

    def more(self):
        self.limit = min(20_000, self.limit + 200)
        self.refresh()

    def select(self, event=None):
        selected = self.list.selection()
        text = ''
        if selected and selected[0] in self.rows:
            try:
                text = self.app.journal_service.store.text(self.rows[selected[0]])
            except Exception:
                messagebox.showerror('Дневник', 'Не удалось расшифровать запись в этой учётной записи Windows.', parent=self.window)
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self.text.configure(state='disabled')

    def copy(self):
        text = self.text.get('1.0', 'end-1c')
        if text:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)

    def clear(self):
        if messagebox.askyesno('Дневник', 'Удалить все сохранённые записи дневника?', parent=self.window):
            try:
                self.app.journal_service.clear()
                self.refresh()
                self.select()
            except Exception:
                messagebox.showerror('Дневник', 'Не удалось очистить дневник.', parent=self.window)
