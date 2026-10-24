"""Scrollable settings pages and delayed, non-activating contextual help."""
import tkinter as tk
from tkinter import ttk

from ui_i18n import tr, get_language


class ScrollablePage(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, width=1, height=1)
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.body = ttk.Frame(self.canvas, padding=18)
        self.item = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', self._region)
        self.canvas.bind('<Configure>', self._width)
        root = self.winfo_toplevel()
        root.bind('<MouseWheel>', self._wheel, add='+')

    def _region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _width(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)

    def _wheel(self, event):
        widget = event.widget
        # Text, lists and dropdowns retain their own scrolling behavior.
        if widget.winfo_class() in ('Text', 'Listbox', 'Treeview', 'TCombobox'):
            return
        while widget is not None and widget is not self.body and widget is not self.canvas:
            widget = getattr(widget, 'master', None)
        if widget is None or self.body.winfo_reqheight() <= self.canvas.winfo_height():
            return
        if event.delta:
            self.canvas.yview_scroll(-max(1, abs(event.delta) // 120) * (1 if event.delta > 0 else -1), 'units')
            return 'break'


def window_size(content_width, content_height, available_width, available_height, line_height):
    """Reserve space for Windows borders/title bar; all dimensions are Tk units."""
    width = max(1, min(max(740, content_width), available_width - 24))
    height = max(1, min(content_height, available_height - 2 * line_height - 24))
    return width, height


def fit_settings_window(root, pages):
    from tkinter import font
    import win32api
    root.update_idletasks()
    try:
        monitor = win32api.MonitorFromWindow(root.winfo_id(), 2)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        available_width, available_height = right - left, bottom - top
    except Exception:
        available_width, available_height = root.winfo_screenwidth(), root.winfo_screenheight()
    line = font.nametofont('TkDefaultFont', root=root).metrics('linespace')
    chrome = root.winfo_reqheight() - max(page.winfo_reqheight() for page in pages)
    width = max(root.winfo_reqwidth(), max(page.body.winfo_reqwidth() for page in pages) + 56)
    height = chrome + max(page.body.winfo_reqheight() for page in pages)
    width, height = window_size(width, height, available_width, available_height, line)
    root.minsize(min(width, 640), min(height, max(320, line * 18)))
    root.geometry(f'{width}x{height}')


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.pending = self.window = None
        for event in ('<Enter>', '<FocusIn>'):
            widget.bind(event, self.schedule, add='+')
        for event in ('<Leave>', '<FocusOut>', '<ButtonPress>', '<Escape>', '<Destroy>'):
            widget.bind(event, self.hide, add='+')

    def schedule(self, _event=None):
        self.hide()
        self.pending = self.widget.after(500, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_viewable():
            return
        window = self.window = tk.Toplevel(self.widget)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes('-topmost', True)
        ttk.Label(window, text=tr(self.text), wraplength=400, padding=10,
                  justify='right' if get_language() == 'he' else 'left',
                  relief='solid', borderwidth=1).pack()
        window.update_idletasks()
        # A hover tip must not take focus from the editor/checkbox.
        import win32gui
        import win32api
        hwnd = win32gui.GetAncestor(window.winfo_id(), 2)
        style = win32gui.GetWindowLong(hwnd, -20)
        win32gui.SetWindowLong(hwnd, -20, style | 0x08000000 | 0x80)
        monitor = win32api.MonitorFromWindow(self.widget.winfo_id(), 2)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        x = max(left + 8, min(self.widget.winfo_rootx(), right - window.winfo_reqwidth() - 8))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + window.winfo_reqheight() > bottom - 8:
            y = max(top + 8, self.widget.winfo_rooty() - window.winfo_reqheight() - 6)
        window.deiconify()
        win32gui.SetWindowPos(hwnd, -1, x, y, 0, 0, 0x0001 | 0x0010)

    def hide(self, _event=None):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
