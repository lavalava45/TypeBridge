"""Explicit opt-in settings and recovery viewer for encrypted editor snapshots."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk
from ui_i18n import localize_window, messagebox, LocalizedStringVar


class JournalWindow:
    def __init__(self, root, app):
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title('TypeBridge — Дневник ввода')
        self.window.geometry('800x760')
        self.window.minsize(720, 680)
        self.limit = 200
        self.rows = {}
        self.loaded = 0
        self.loading = False
        self.view_serial = 0
        self.text_serial = 0
        self.list_request = self.text_request = None
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
                  'чтение и добавление снимков; автоочистка старых записей продолжает работать.', wraplength=750).pack(anchor='w')
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
        self.base_status = ('База: TypeBridge-data\\journal\\journal.sqlite3. '
                  'До 50 МБ зашифрованных данных / 20 000 снимков. Срок в днях — верхняя граница; '
                  'при достижении объёма или количества старые снимки удаляются раньше.')
        self.operation_status = LocalizedStringVar(master=self.window, value=self.base_status)
        ttk.Label(frame, textvariable=self.operation_status, wraplength=750).pack(anchor='w', pady=(8, 0))
        self.refresh()
        localize_window(self.window)

    def save(self):
        try:
            days = int(self.days.get())
            apps = tuple(x.strip().casefold() for x in self.apps.get().split(',') if x.strip())
            self.app.apply_journal_settings(self.enabled.get(), days, apps)
        except (ValueError, OSError) as error:
            messagebox.showerror('Дневник', str(error), parent=self.window)

    def _alive(self):
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def refresh(self):
        self.view_serial += 1
        self.text_serial += 1
        if self.text_request is not None:
            self.app.journal_io.cancel(self.text_request)
            self.text_request = None
        if self.list_request is not None:
            self.app.journal_io.cancel(self.list_request)
        self.rows.clear()
        self.loaded = 0
        self.list.delete(*self.list.get_children())
        self._display_text('')
        self._load_page(0)

    def _load_page(self, offset):
        serial = self.view_serial
        self.loading = True
        self.operation_status.set('Загрузка записей…')
        def complete(rows, error):
            if not self._alive() or serial != self.view_serial:
                return
            self.loading = False
            self.operation_status.set(self.base_status)
            if error:
                messagebox.showerror('Дневник', 'Не удалось прочитать записи дневника.', parent=self.window)
                return
            # One page, never re-render all 20,000 rows on every More click.
            for identifier, created, app in rows:
                row = self.list.insert('', 'end', values=(datetime.fromtimestamp(created).strftime('%Y-%m-%d %H:%M:%S'), app))
                self.rows[row] = identifier
            self.loaded += len(rows)
        try:
            self.list_request = self.app.journal_io.submit('list', (200, offset), complete)
        except OSError:
            self.loading = False
            self.operation_status.set('Очередь дневника занята; повтори команду позже')

    def more(self):
        if not self.loading and self.loaded < 20_000:
            self._load_page(self.loaded)

    def _display_text(self, text):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self.text.configure(state='disabled')

    def select(self, event=None):
        self.text_serial += 1
        serial = self.text_serial
        if self.text_request is not None:
            self.app.journal_io.cancel(self.text_request)
        self._display_text('')
        selected = self.list.selection()
        if not selected or selected[0] not in self.rows:
            return
        identifier = self.rows[selected[0]]
        self.operation_status.set('Чтение записи…')
        def complete(text, error):
            if not self._alive() or serial != self.text_serial:
                return
            self.operation_status.set(self.base_status)
            if error:
                messagebox.showerror('Дневник', 'Не удалось расшифровать запись в этой учётной записи Windows.', parent=self.window)
            else:
                self._display_text(text)
        try:
            self.text_request = self.app.journal_io.submit('text', identifier, complete)
        except OSError:
            self.operation_status.set('Очередь дневника занята; повтори команду позже')

    def copy(self):
        text = self.text.get('1.0', 'end-1c')
        if text:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)

    def clear(self):
        if not messagebox.askyesno('Дневник', 'Удалить все сохранённые записи дневника?', parent=self.window):
            return
        self.operation_status.set('Удаление записей…')
        def complete(result, error):
            if not self._alive():
                return
            self.operation_status.set(self.base_status)
            if error:
                messagebox.showerror('Дневник', 'Не удалось очистить дневник.', parent=self.window)
            else:
                self.refresh()
        try:
            self.app.journal_io.submit('clear', None, complete)
        except OSError:
            self.operation_status.set('Очередь дневника занята; повтори команду позже')
