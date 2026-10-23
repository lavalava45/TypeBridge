"""Search providers and explicit selection-search shortcut."""
import tkinter as tk
from tkinter import ttk
from direct_hotkeys import SUGGESTIONS
from quick_search import validate_engines
from ui_i18n import ChoiceVar, localize_window, messagebox


class QuickSearchWindow:
    def __init__(self, root, app):
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title('TypeBridge — Быстрый поиск')
        self.window.geometry('740x580')
        self.window.minsize(680, 540)
        settings = app.search_settings
        self.engines = list(settings.quick_search_engines)
        self.enabled = tk.BooleanVar(value=settings.quick_search)
        self.key = ChoiceVar(value=settings.quick_search_key)
        self.selected = tk.StringVar(value=settings.quick_search_engine)
        self.name, self.url = tk.StringVar(), tk.StringVar()
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
        ttk.Label(frame, text='Выдели слово или фразу и нажми сочетание. Текст отправляется выбранному сервису '
                  'только по этой команде. Буфер не меняется. Парольные и исключённые приложения пропускаются; '
                  'нужна поддержка выделения Windows UI Automation.', wraplength=680).pack(anchor='w', pady=8)
        ttk.Button(frame, text='Сохранить настройки', command=self.save).pack(anchor='w')
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
        self.refresh()

    def save(self):
        try:
            self.app.apply_search_settings(self.enabled.get(), self.key.get().strip(), self.selected.get(),
                                           validate_engines(self.engines, self.selected.get()))
        except (ValueError, OSError) as error:
            messagebox.showerror('Быстрый поиск', str(error), parent=self.window)
