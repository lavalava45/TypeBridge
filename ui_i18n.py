"""Local UI translations. Stored settings, user content and shortcut IDs stay canonical."""
import re
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox as _messagebox, filedialog as _filedialog
import weakref

from ui_translations import CATALOG

LANGUAGES = {'ru': 'Русский', 'he': 'עברית', 'en': 'English'}
_language = 'ru'
_windows = []
_variables = []
_patterns = []
for source, translated in CATALOG.items():
    names = re.findall(r'\{(v\d+)\}', source)
    if names:
        pattern = re.escape(source)
        for name in names:
            pattern = pattern.replace(re.escape('{' + name + '}'), f'(?P<{name}>.*?)', 1)
        _patterns.append((re.compile(pattern, re.DOTALL), translated))
_patterns.sort(key=lambda item: len(item[0].pattern), reverse=True)


def get_language():
    return _language


def tr(source, _depth=0):
    if _language == 'ru' or not isinstance(source, str) or not source:
        return source
    if source in CATALOG:
        return CATALOG[source][_language]
    for pattern, translated in _patterns:
        match = pattern.fullmatch(source)
        if match:
            values = match.groupdict()
            # Nested UI labels only; arbitrary user text is not translated.
            for key, value in values.items():
                if value in ('IDE и разработка', 'Графика, видео и звук', 'Игровые термины',
                             'Программирование: Python / JavaScript / C++'):
                    values[key] = tr(value)
                elif _depth < 3 and source.startswith(('Ошибка', 'Трей:', 'Трей недоступен:',
                                                       'Прямое переключение:', 'Не удалось')):
                    values[key] = tr(value, _depth + 1)
            return translated[_language].format(**values)
    for prefix in ('Дневник включён: снимок сохранён ', 'Примеры: '):
        if source.startswith(prefix):
            return tr(prefix) + source[len(prefix):]
    return source


def set_language(language):
    global _language
    if language not in LANGUAGES:
        raise ValueError('Unsupported interface language')
    _language = language
    for reference in list(_variables):
        variable = reference()
        if variable is not None:
            try:
                variable.refresh_language()
            except tk.TclError:
                pass
    for reference in list(_windows):
        window = reference()
        if window is not None:
            try:
                if window.winfo_exists():
                    localize_window(window)
            except tk.TclError:
                pass


class LocalizedStringVar(tk.StringVar):
    def __init__(self, master=None, value='', **kwargs):
        self.source = value
        super().__init__(master=master, value=tr(value), **kwargs)
        _variables.append(weakref.ref(self))

    def set(self, value):
        self.source = value
        super().set(tr(value))

    def refresh_language(self):
        tk.StringVar.set(self, tr(self.source))


_choices = {'Отключено': {'ru': 'Отключено', 'en': 'Disabled', 'he': 'מושבת'},
            'auto': {'ru': 'Авто', 'en': 'Auto', 'he': 'אוטומטי'}}
_reverse_choices = {display: canonical for canonical, values in _choices.items() for display in values.values()}


class ChoiceVar(tk.StringVar):
    """Display localized enum labels, return canonical values to the engine."""
    def __init__(self, master=None, value='', **kwargs):
        super().__init__(master=master, **kwargs)
        self.set(value)
        _variables.append(weakref.ref(self))

    def get(self):
        value = super().get()
        return _reverse_choices.get(value, value)

    def set(self, value):
        value = _reverse_choices.get(value, value)
        super().set(_choices.get(value, {}).get(_language, value))

    def refresh_language(self):
        self.set(self.get())


def _choice(value):
    return _choices.get(value, {}).get(_language, value)


def localize_window(window):
    """Update labels and layout in place; never recreate editors or alter their content."""
    if not any(reference() is window for reference in _windows):
        _windows.append(weakref.ref(window))
    rtl = _language == 'he'
    if not hasattr(window, '_i18n_title'):
        window._i18n_title = window.title()
    window.title(tr(window._i18n_title))
    if rtl:
        style = ttk.Style(window)
        def mirror(layout):
            result = []
            for element, options in layout:
                options = dict(options)
                if 'side' in options:
                    options['side'] = {'left': 'right', 'right': 'left'}.get(options['side'], options['side'])
                if 'children' in options:
                    options['children'] = mirror(options['children'])
                result.append((element, options))
            return result
        for base in ('TCheckbutton', 'TRadiobutton'):
            style.layout('I18nRTL.' + base, mirror(style.layout(base)))
            style.configure('I18nRTL.' + base, anchor='e', justify='right')

    def visit(widget):
        keys = widget.keys()
        if 'text' in keys and not ('textvariable' in keys and str(widget.cget('textvariable'))):
            if not hasattr(widget, '_i18n_text'):
                widget._i18n_text = widget.cget('text')
            widget.configure(text=tr(widget._i18n_text))
        if widget.winfo_class() in ('TLabel', 'Label'):
            if not hasattr(widget, '_i18n_alignment'):
                widget._i18n_alignment = (widget.cget('anchor'), widget.cget('justify'))
            anchor, justify = widget._i18n_alignment
            widget.configure(anchor='e' if rtl else anchor, justify='right' if rtl else justify)
        if widget.winfo_class() == 'TNotebook':
            if not hasattr(widget, '_i18n_tabs'):
                widget._i18n_tabs = {tab: widget.tab(tab, 'text') for tab in widget.tabs()}
            for tab, source in widget._i18n_tabs.items():
                widget.tab(tab, text=tr(source))
        if widget.winfo_class() == 'Treeview':
            if not hasattr(widget, '_i18n_headings'):
                widget._i18n_headings = {key: widget.heading(key, 'text') for key in widget.cget('columns')}
            for key, source in widget._i18n_headings.items():
                widget.heading(key, text=tr(source))
        if widget.winfo_class() == 'TCombobox':
            if not hasattr(widget, '_i18n_choices'):
                widget._i18n_choices = tuple(widget.cget('values'))
            widget.configure(values=tuple(_choice(value) for value in widget._i18n_choices))
        if widget.winfo_class() in ('TCheckbutton', 'TRadiobutton'):
            if not hasattr(widget, '_i18n_style'):
                widget._i18n_style = widget.cget('style')
            widget.configure(style=('I18nRTL.' + widget.winfo_class()) if rtl else widget._i18n_style)
        if widget.winfo_manager() == 'pack':
            if not hasattr(widget, '_i18n_pack'):
                widget._i18n_pack = widget.pack_info()
            original = widget._i18n_pack
            side, anchor = original['side'], original['anchor']
            if rtl:
                side = {'left': 'right', 'right': 'left'}.get(side, side)
                anchor = {'w': 'e', 'e': 'w', 'nw': 'ne', 'ne': 'nw', 'sw': 'se', 'se': 'sw'}.get(anchor, anchor)
            widget.pack_configure(side=side, anchor=anchor)
        for child in widget.winfo_children():
            if child.winfo_class() not in ('Toplevel', 'Tk'):
                visit(child)
    visit(window)
    callback = getattr(window, '_i18n_refresh', None)
    if callback:
        callback()


def set_text(widget, source):
    widget._i18n_text = source
    widget.configure(text=tr(source))


class _Dialogs:
    def __init__(self, module):
        self.module = module

    def __getattr__(self, name):
        function = getattr(self.module, name)
        def call(*args, **kwargs):
            args = tuple(tr(value) if isinstance(value, str) else value for value in args)
            for key in ('title', 'message', 'detail'):
                if key in kwargs:
                    kwargs[key] = tr(kwargs[key])
            if 'filetypes' in kwargs:
                kwargs['filetypes'] = tuple((tr(label), pattern) for label, pattern in kwargs['filetypes'])
            return function(*args, **kwargs)
        return call


messagebox = _Dialogs(_messagebox)
filedialog = _Dialogs(_filedialog)
