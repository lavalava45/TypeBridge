"""Dictionary import UI: bundled examples in one click, custom files previewed."""
import tkinter as tk
from tkinter import ttk
from ui_i18n import tr, localize_window, LocalizedStringVar, ChoiceVar, filedialog, messagebox

from dictionary_import import BUILTINS, PACK_DIR, read_import


class DictionaryImportWindow:
    def __init__(self, root, app):
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title('TypeBridge — Импорт словарей исключений')
        self.window.geometry('760x640')
        self.window.minsize(680, 580)
        self.preview = None
        self.file_path = None
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Готовые примеры · CSpell, MIT',
                  font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        for filename, title in BUILTINS:
            ttk.Button(frame, text=f'Добавить: {title}',
                       command=lambda name=filename: self.add_builtin(name)).pack(anchor='w', pady=5)
        ttk.Label(frame, text='Добавляют исключения одним нажатием. Существующие слова сохраняются, '
                  'повторный импорт не создаёт дублей. Работают без интернета.',
                  wraplength=690).pack(anchor='w', pady=(3, 12))
        custom = ttk.LabelFrame(frame, text='Свой словарь: TXT или JSON', padding=10)
        custom.pack(fill='x')
        row = ttk.Frame(custom)
        row.pack(fill='x')
        ttk.Label(row, text='Язык TXT:').pack(side='left')
        self.language = ChoiceVar(value='auto')
        picker = ttk.Combobox(row, textvariable=self.language, values=('auto', 'ru', 'en', 'he'),
                              state='readonly', width=8)
        picker.pack(side='left', padx=8)
        picker.bind('<<ComboboxSelected>>', lambda event: self.reload())
        self.sensitive = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text='Учитывать регистр TXT', variable=self.sensitive,
                        command=self.reload).pack(side='left')
        ttk.Button(custom, text='Выбрать файл и посмотреть…', command=self.choose).pack(anchor='w', pady=8)
        ttk.Label(custom, text='UTF-8, одно слово на строку; auto определяет RU/EN/HE. '
                  'JSON хранит язык и регистр каждой записи. До 2 МБ / 50 000 записей.',
                  wraplength=660).pack(anchor='w')
        self.summary = LocalizedStringVar(value='Выбери пример или свой файл.')
        ttk.Label(frame, textvariable=self.summary, wraplength=690).pack(anchor='w', pady=12)
        self.details = tk.Text(frame, height=9, wrap='word', state='disabled')
        self.details.pack(fill='both', expand=True)
        ttk.Label(frame, text='Импортируются отдельные слова, а не правила синтаксиса. '
                  'Составные идентификаторы, цифры и символы не преобразуются в другие слова. '
                  'Для медицины и юриспруденции можно загрузить свой список терминов.',
                  wraplength=690).pack(anchor='w', pady=10)
        row = ttk.Frame(frame)
        row.pack(fill='x')
        self.apply_button = ttk.Button(row, text='Добавить исключения из файла', command=self.apply,
                                       state='disabled')
        self.apply_button.pack(side='left')
        ttk.Button(row, text='Закрыть', command=self.window.destroy).pack(side='right')
        self.window._i18n_refresh = self.render_details
        localize_window(self.window)

    def choose(self):
        path = filedialog.askopenfilename(parent=self.window, title='Словарь исключений',
                    filetypes=(('Словари TXT / JSON', '*.txt *.json'), ('Все файлы', '*.*')))
        if path:
            self.file_path = path
            self.reload()

    def display(self, preview):
        self.preview = preview
        self.summary.set(f'{preview.name}: слов — {len(preview.entries)}, '
                         f'повторов внутри файла — {preview.duplicates}, пропущено — {len(preview.skipped)}.')
        self.render_details()

    def render_details(self):
        preview = self.preview
        if preview is None:
            return
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('end', tr('Примеры: ') + ', '.join(entry.word for entry in preview.entries[:60]) + '\n')
        for index, reason in preview.skipped[:40]:
            self.details.insert('end', tr(f'Строка/запись {index}: {tr(reason)}\n'))
        if len(preview.skipped) > 40:
            self.details.insert('end', tr('Показаны первые 40 пропусков.\n'))
        self.details.configure(state='disabled')

    def reload(self):
        if not self.file_path:
            return
        self.preview = None
        self.apply_button.configure(state='disabled')
        try:
            preview = read_import(self.file_path, language=self.language.get(), sensitive=self.sensitive.get())
            self.display(preview)
            self.apply_button.configure(state='normal' if preview.entries else 'disabled')
        except (ValueError, OSError) as error:
            self.summary.set(str(error))
            messagebox.showerror('Импорт словаря', str(error), parent=self.window)

    def add_builtin(self, filename):
        try:
            preview = read_import(PACK_DIR / filename)
            self.display(preview)
            self.apply_button.configure(state='disabled')
            self.publish(preview)
        except (ValueError, OSError) as error:
            messagebox.showerror('Импорт словаря', str(error), parent=self.window)

    def publish(self, preview):
        added, existing = self.app.import_exclusion_preview(preview)
        self.summary.set(f'Добавлено: {added}. Уже были: {existing}. '
                         f'Повторов в файле: {preview.duplicates}. Пропущено: {len(preview.skipped)}. '
                         'Исключения сохранены и применены.')

    def apply(self):
        if self.preview and self.preview.entries:
            try:
                self.publish(self.preview)
            except (ValueError, OSError) as error:
                messagebox.showerror('Импорт словаря', str(error), parent=self.window)
