"""History picker: text stays in memory; no automatic paste on selection."""
import tkinter as tk
from tkinter import ttk
from ui_i18n import localize_window, set_text, messagebox


class ClipboardWindow:
    def __init__(self, root, tools, target=None):
        self.tools, self.target = tools, target
        self.window = tk.Toplevel(root)
        self.window.title('TypeBridge — История буфера обмена')
        self.window.geometry('740x520')
        self.window.minsize(600, 400)
        self.window.protocol('WM_DELETE_WINDOW', self.window.withdraw)
        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill='both', expand=True)
        self.status = ttk.Label(frame, wraplength=680)
        self.status.pack(anchor='w', pady=(0, 8))
        self.list = tk.Listbox(frame, exportselection=False, height=9)
        self.list.pack(fill='both', expand=True)
        self.list.bind('<<ListboxSelect>>', self.preview)
        self.list.bind('<Double-Button-1>', lambda event: self.paste())
        self.preview_text = tk.Text(frame, height=6, wrap='word', state='disabled')
        self.preview_text.pack(fill='both', expand=True, pady=8)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x')
        for title, action in (('Вставить', self.paste), ('Копировать', self.copy),
                              ('Удалить', self.delete), ('Очистить историю', self.clear)):
            ttk.Button(buttons, text=title, command=action).pack(side='left', padx=(0, 6))
        ttk.Label(frame, text='30 последних фрагментов, только до выхода из TypeBridge. '
                  'Вставка и копирование заменяют буфер выбранным чистым текстом.',
                  wraplength=680).pack(anchor='w', pady=(10, 0))
        self.snapshot = None
        self.refresh()
        localize_window(self.window)

    def show(self, target):
        self.target = target
        self.window.deiconify()
        self.window.lift()

    def selected(self):
        selection = self.list.curselection()
        return self.snapshot[selection[0]] if selection and selection[0] < len(self.snapshot or ()) else None

    def preview(self, event=None):
        self.preview_text.configure(state='normal')
        self.preview_text.delete('1.0', 'end')
        self.preview_text.insert('1.0', self.selected() or '')
        self.preview_text.configure(state='disabled')

    def refresh(self):
        if not self.window.winfo_exists():
            return
        items = tuple(self.tools.history.items)
        if items != self.snapshot:
            selected = self.selected()
            self.snapshot = items
            self.list.delete(0, 'end')
            for text in items:
                self.list.insert('end', text.replace('\r', ' ').replace('\n', ' ↵ ').replace('\t', '  ')[:180])
            if items:
                self.list.selection_set(items.index(selected) if selected in items else 0)
            self.preview()
        enabled = self.tools.settings.clipboard_history
        set_text(self.status, (f'История включена · фрагментов: {len(items)}' if enabled else
                              'История отключена. Включи её в настройках → «Буфер обмена».'))
        self.window.after(400, self.refresh)

    def paste(self):
        text = self.selected()
        if text is not None:
            try:
                self.tools.paste_item(self.target, text, self.window)
            except Exception as error:
                messagebox.showerror('Буфер обмена', str(error), parent=self.window)

    def copy(self):
        text = self.selected()
        if text is not None:
            try:
                self.tools.native.write_plain(text, self.window.winfo_id())
            except Exception:
                messagebox.showerror('Буфер обмена', 'Буфер занят; повтори команду', parent=self.window)

    def delete(self):
        text = self.selected()
        if text in self.tools.history.items:
            self.tools.delete_history(text)

    def clear(self):
        self.tools.clear_history()
