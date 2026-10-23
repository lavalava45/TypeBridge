"""Separate editor for literal replacement rules; persistence belongs to the caller."""
import tkinter as tk
from tkinter import ttk
from ui_i18n import tr, localize_window, LocalizedStringVar, messagebox
from snippets import Snippet, SnippetConfig


class SnippetsWindow:
    def __init__(self, parent, config, apply):
        self.apply = apply
        self.rules = list(config.rules)
        self.selected = None
        self._loading = False
        self._editor_baseline = ('', '', True, True)
        self.window = tk.Toplevel(parent)
        self.window.title('Автозамена и шаблоны')
        self.window.geometry('780x660'); self.window.minsize(700, 590)
        frame = ttk.Frame(self.window, padding=16); frame.pack(fill='both', expand=True)
        self.enabled = tk.BooleanVar(value=config.enabled)
        self.trigger = tk.StringVar()
        self.sensitive = tk.BooleanVar(value=True)
        self.immediate = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text='Включить автозамену и шаблоны', variable=self.enabled).pack(anchor='w')
        ttk.Label(frame, text='Например: //email → name@domain.com. Для работы включи автоисправление.\n'
                  'Вводи сокращение с начала поля или после пробела. После Backspace начни с пробела.',
                  wraplength=720).pack(anchor='w', pady=(6,12))
        self.table = ttk.Treeview(frame, columns=('trigger','text','mode','case'), show='headings', height=6,
                                 selectmode='browse')
        for key,title,width in (('trigger','Сокращение',135),('text','Текст',300),('mode','Когда',130),('case','Регистр',100)):
            self.table.heading(key,text=title); self.table.column(key,width=width,minwidth=65)
        self.table.pack(fill='both',expand=True)
        self.table.bind('<<TreeviewSelect>>',self.select)
        row=ttk.Frame(frame); row.pack(fill='x',pady=(12,6))
        ttk.Label(row,text='Сокращение:').pack(side='left')
        ttk.Entry(row,textvariable=self.trigger).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(row,text='Новое правило',command=self.new).pack(side='right')
        ttk.Label(frame,text='Фраза или шаблон (можно несколько строк):').pack(anchor='w')
        self.text=tk.Text(frame,height=7,wrap='word',undo=True)
        self.text.pack(fill='both',expand=True,pady=(4,8))
        ttk.Checkbutton(frame,text='Учитывать регистр сокращения',variable=self.sensitive).pack(anchor='w')
        ttk.Checkbutton(frame,text='Разворачивать сразу, без пробела',variable=self.immediate).pack(anchor='w',pady=4)
        ttk.Label(frame,text='Если галка снята — разворачивать после пробела, Enter, Tab или . , ! ? ; :',
                  wraplength=720).pack(anchor='w')
        row=ttk.Frame(frame); row.pack(fill='x',pady=10)
        ttk.Button(row,text='Добавить / обновить правило',command=self.upsert).pack(side='left')
        ttk.Button(row,text='Удалить выбранное',command=self.delete).pack(side='left',padx=8)
        row=ttk.Frame(frame); row.pack(fill='x',pady=(6,0))
        ttk.Button(row,text='Сохранить и применить',command=self.save).pack(side='left')
        ttk.Button(row,text='Закрыть',command=self.close).pack(side='right')
        self.status=LocalizedStringVar(value='Изменения применяются кнопкой «Сохранить и применить».')
        ttk.Label(frame,textvariable=self.status,wraplength=720).pack(anchor='w',pady=(8,0))
        self.dirty=False
        self.trigger.trace_add('write',self.changed)
        self.sensitive.trace_add('write',self.changed)
        self.immediate.trace_add('write',self.changed)
        self.enabled.trace_add('write',self.changed)
        self.text.bind('<<Modified>>',self.text_changed)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.refresh()
        self.window._i18n_refresh = self.refresh_language
        localize_window(self.window)

    def refresh_language(self):
        selected = self.table.selection()
        self.refresh()
        for item in selected:
            if self.table.exists(item):
                self.table.selection_set(item)

    def changed(self,*args):
        if not self._loading: self.dirty=True

    def editor_values(self):
        return (self.trigger.get(),self.text.get('1.0','end-1c'),self.sensitive.get(),self.immediate.get())

    def allow_discard_editor(self):
        return (self.editor_values()==self._editor_baseline or
                messagebox.askyesno('Автозамена','Отбросить несохранённые изменения текущего правила?',parent=self.window))

    def text_changed(self,event=None):
        if self.text.edit_modified():
            self.changed(); self.text.edit_modified(False)

    def refresh(self):
        self.table.delete(*self.table.get_children())
        for i,r in enumerate(self.rules):
            preview=r.text.replace('\n',' ↵ ').replace('\t',' ⇥ ')
            self.table.insert('', 'end', iid=str(i), values=(r.trigger, preview[:90],
                              tr('Сразу' if r.immediate else 'После разделителя'),
                              tr('Точный' if r.sensitive else 'Любой')))

    def new(self, force=False):
        if not force and not self.allow_discard_editor(): return
        self._loading=True
        self.selected=None
        self.table.selection_remove(*self.table.selection())
        self.trigger.set(''); self.text.delete('1.0','end')
        self.sensitive.set(True); self.immediate.set(True)
        self.text.edit_modified(False)
        self._editor_baseline=self.editor_values(); self._loading=False

    def select(self,event=None):
        selection=self.table.selection()
        if not selection: return
        index=int(selection[0])
        if index==self.selected or index>=len(self.rules): return
        if not self.allow_discard_editor():
            if self.selected is not None: self.table.selection_set(str(self.selected))
            else: self.table.selection_remove(*selection)
            return
        self._loading=True
        self.selected=int(selection[0]); rule=self.rules[self.selected]
        self.trigger.set(rule.trigger); self.text.delete('1.0','end'); self.text.insert('1.0',rule.text)
        self.sensitive.set(rule.sensitive); self.immediate.set(rule.immediate)
        self.text.edit_modified(False)
        self._editor_baseline=self.editor_values(); self._loading=False

    def upsert(self):
        try:
            rule=Snippet(self.trigger.get(),self.text.get('1.0','end-1c'),self.sensitive.get(),self.immediate.get())
            rules=list(self.rules)
            if self.selected is None: rules.append(rule)
            else: rules[self.selected]=rule
            SnippetConfig(self.enabled.get(),tuple(rules)).validate()
            self.rules=rules; self.refresh(); self.new(force=True); self.dirty=True
            self.status.set('Правило подготовлено. Нажми «Сохранить и применить».')
            return True
        except ValueError as error:
            messagebox.showerror('Автозамена',str(error),parent=self.window); return False

    def delete(self):
        selection=self.table.selection()
        if not selection: return
        del self.rules[int(selection[0])]
        self.refresh(); self.new(force=True); self.dirty=True

    def save(self):
        if self.trigger.get() or self.text.get('1.0','end-1c'):
            if not self.upsert(): return False
        try:
            config=SnippetConfig(self.enabled.get(),tuple(self.rules)).validate()
            self.apply(config)
            self.dirty=False; self.status.set('Автозамены сохранены и применены.')
            return True
        except (ValueError,OSError) as error:
            messagebox.showerror('Автозамена',str(error),parent=self.window); return False

    def close(self):
        if self.dirty or self.editor_values()!=self._editor_baseline:
            answer=messagebox.askyesnocancel('Автозамена','Сохранить изменения перед закрытием?',parent=self.window)
            if answer is None or (answer and not self.save()): return False
        self.window.destroy()
        return True
