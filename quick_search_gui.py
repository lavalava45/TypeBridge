"""Search providers and explicit selection-search shortcut."""
import tkinter as tk
from tkinter import ttk
from direct_hotkeys import SUGGESTIONS
from quick_search import validate_engines
from ui_i18n import ChoiceVar, LocalizedStringVar, localize_window, messagebox


def show_search_preview(root, preview, app):
    old = getattr(app, 'search_preview_window', None)
    if old is not None and old.winfo_exists():
        old.destroy()
    window = tk.Toplevel(root)
    app.search_preview_window = window
    window.title('TypeBridge — Подтверждение поиска')
    window.geometry('660x300')
    frame = ttk.Frame(window, padding=14)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Приложение не отдало выделение через Windows API. Получен текст '
              'штатной командой копирования; проверь его перед отправкой. Некоторые редакторы '
              'без выделения копируют целую строку.', wraplength=620).pack(anchor='w')
    text = tk.Text(frame, height=6, wrap='word')
    text.insert('1.0', preview.text)
    text.configure(state='disabled')
    text.pack(fill='both', expand=True, pady=10)
    settings = app.clipboard_tools.settings
    generation = app.quick_search.generation

    def confirm():
        if app.closing or not app.clipboard_tools.settings.quick_search or generation != app.quick_search.generation:
            window.destroy()
            return
        try:
            if not app.quick_search.opener(preview.url):
                raise OSError('Не удалось открыть браузер')
        except Exception:
            messagebox.showerror('Быстрый поиск', 'Не удалось открыть браузер', parent=window)
            return
        window.destroy()
    row = ttk.Frame(frame)
    row.pack(fill='x')
    window.provider_name = tk.StringVar(value=settings.quick_search_engine)
    ttk.Label(row, textvariable=window.provider_name).pack(side='left', padx=(0, 10))
    ttk.Button(row, text='Искать в браузере', command=confirm).pack(side='left')
    ttk.Button(row, text='Отмена', command=window.destroy).pack(side='right')
    window.bind('<Escape>', lambda event: window.destroy())
    localize_window(window)
    return window


class QuickSearchWindow:
    def __init__(self, root, app):
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title('TypeBridge — Быстрый поиск')
        self.window.geometry('740x640')
        self.window.minsize(680, 600)
        settings = app.search_settings
        self.engines = list(settings.quick_search_engines)
        self.enabled = tk.BooleanVar(value=settings.quick_search)
        self.key = ChoiceVar(value=settings.quick_search_key)
        self.selected = tk.StringVar(value=settings.quick_search_engine)
        self.name, self.url = tk.StringVar(), tk.StringVar()
        self.saved_identity = (settings.quick_search, settings.quick_search_key, settings.quick_search_engine, tuple(self.engines))
        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill='both', expand=True)
        ttk.Checkbutton(frame, text='Включить быстрый поиск выделения', variable=self.enabled).pack(anchor='w')
        ttk.Label(frame, text='Горячая клавиша').pack(anchor='w', pady=(10, 2))
        ttk.Combobox(frame, textvariable=self.key,
                     values=('Ctrl+Alt+G', *(key for key in SUGGESTIONS if not key.endswith('F12'))),
                     width=28).pack(anchor='w')
        ttk.Label(frame, text='Искать через').pack(anchor='w', pady=(10, 2))
        self.provider = ttk.Combobox(frame, textvariable=self.selected, state='readonly')
        self.provider.pack(fill='x')
        self.list = ttk.Treeview(frame, columns=('name', 'url'), show='headings', height=5, selectmode='browse')
        self.list.heading('name', text='Название')
        self.list.heading('url', text='URL поиска')
        self.list.column('name', width=130, stretch=False)
        self.list.pack(fill='both', expand=True, pady=10)
        self.list.bind('<<TreeviewSelect>>', self.select)
        ttk.Label(frame, text='Название').pack(anchor='w')
        ttk.Entry(frame, textvariable=self.name).pack(fill='x')
        ttk.Label(frame, text='URL поиска (вместо текста — {query})').pack(anchor='w', pady=(8, 2))
        ttk.Entry(frame, textvariable=self.url).pack(fill='x')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=8)
        for title, command in (('Добавить', self.upsert), ('Обновить выбранный', lambda: self.upsert(update=True)), ('Удалить', self.remove)):
            ttk.Button(row, text=title, command=command).pack(side='left', padx=(0, 8))
        ttk.Label(frame, text='Выдели слово или фразу и нажми сочетание. Сначала используется Windows UI Automation; '
                  'если поле не поддерживает чтение выделения — копирование с восстановлением буфера и '
                  'предпросмотром перед поиском. Парольные и исключённые приложения пропускаются.',
                  wraplength=680).pack(anchor='w', pady=8)
        ttk.Button(frame, text='Сохранить настройки', command=self.save).pack(anchor='w')
        self.save_status = LocalizedStringVar(value='Сохранено: поиск включён' if settings.quick_search else 'Сохранено: поиск выключен')
        ttk.Label(frame, textvariable=self.save_status, wraplength=680).pack(anchor='w', pady=(8, 0))
        for variable in (self.enabled, self.key, self.selected):
            variable.trace_add('write', self.mark_dirty)
        self.window._i18n_refresh = self.refresh_provider
        self.refresh()
        localize_window(self.window)

    def refresh(self):
        self.list.delete(*self.list.get_children())
        for i, row in enumerate(self.engines):
            self.list.insert('', 'end', iid=str(i), values=row)
        self.refresh_provider()

    def refresh_provider(self):
        # Provider names are user data, not localized enum labels or cached choices.
        self.provider.configure(values=[name for name, _ in self.engines])

    def identity(self):
        return self.enabled.get(), self.key.get().strip(), self.selected.get(), tuple(self.engines)

    def mark_dirty(self, *_):
        self.save_status.set('Есть несохранённые изменения' if self.identity() != self.saved_identity else
                             ('Сохранено: поиск включён' if self.enabled.get() else 'Сохранено: поиск выключен'))

    def select(self, event=None):
        selection = self.list.selection()
        if selection:
            name, url = self.engines[int(selection[0])]
            self.name.set(name)
            self.url.set(url)

    def upsert(self, update=False):
        name, url = self.name.get().strip(), self.url.get().strip()
        selection = self.list.selection()
        rows = list(self.engines)
        if update and not selection:
            return
        index = int(selection[0]) if update else len(rows)
        old_name = rows[index][0] if index < len(rows) else None
        if index < len(rows):
            rows[index] = (name, url)
        else:
            rows.append((name, url))
        chosen = name if self.selected.get() == old_name else self.selected.get()
        try:
            self.engines = list(validate_engines(rows, chosen))
        except ValueError as error:
            messagebox.showerror('Быстрый поиск', str(error), parent=self.window)
            return
        self.selected.set(chosen)
        self.mark_dirty()
        self.refresh()
        self.name.set('')
        self.url.set('')

    def remove(self):
        selection = self.list.selection()
        if not selection:
            return
        if len(self.engines) == 1:
            messagebox.showerror('Быстрый поиск', 'Оставь хотя бы один поисковик', parent=self.window)
            return
        name, _ = self.engines.pop(int(selection[0]))
        if self.selected.get() == name:
            self.selected.set(self.engines[0][0])
        self.mark_dirty()
        self.refresh()

    def save(self):
        try:
            self.app.apply_search_settings(self.enabled.get(), self.key.get().strip(), self.selected.get(),
                                           validate_engines(self.engines, self.selected.get()))
            self.saved_identity = self.identity()
            self.save_status.set('Сохранено: поиск включён' if self.enabled.get() else 'Сохранено: поиск выключен')
        except (ValueError, OSError) as error:
            self.save_status.set('Настройки поиска не сохранены')
            messagebox.showerror('Быстрый поиск', str(error), parent=self.window)
        except Exception:
            import sys
            self.save_status.set('Настройки поиска не сохранены')
            self.app.root.report_callback_exception(*sys.exc_info())
