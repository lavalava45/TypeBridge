"""Selection search: UI Automation first, explicit Copy/preview fallback if unsupported."""
import base64
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import quote, urlsplit
import webbrowser
from contextlib import nullcontext, contextmanager
from clipboard_selection import SearchPreview, SelectionUnavailable, copy_selection

DEFAULT_ENGINES = (("Google", "https://www.google.com/search?q={query}"),)
MAX_QUERY = 2000
REQUEST_TIMEOUT = 8  # One budget across stages; blocking APIs are checked on return.


def validate_engines(engines, selected):
    if not isinstance(selected, str):
        raise ValueError('Выбери поисковик из списка')
    if not isinstance(engines, (list, tuple)) or not 1 <= len(engines) <= 30:
        raise ValueError('Добавь от 1 до 30 поисковиков')
    result = []
    for row in engines:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError('Укажи название и URL поисковика')
        name, url = row
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError('Укажи название поисковика (до 80 символов)')
        if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError('Укажи HTTP/HTTPS URL с меткой {query}')
        parts = urlsplit(url)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None
                or parts.password is not None or '{' in parts.netloc or '}' in parts.netloc
                or url.count('{query}') != 1 or '{' in url.replace('{query}', '')
                or '}' in url.replace('{query}', '') or '\\' in url):
            raise ValueError('Укажи HTTP/HTTPS URL с меткой {query}')
        # Force validation of malformed ports and IPv6 hosts too.
        parts.port
        name = name.strip()
        if any(existing.casefold() == name.casefold() for existing, _ in result):
            raise ValueError('Названия поисковиков должны различаться')
        result.append((name, url))
    if selected not in dict(result):
        raise ValueError('Выбери поисковик из списка')
    return tuple(result)


def search_url(settings, text):
    engines = validate_engines(settings.quick_search_engines, settings.quick_search_engine)
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_QUERY or '\0' in text:
        raise ValueError('Выдели текст длиной от 1 до 2000 символов')
    return dict(engines)[settings.quick_search_engine].replace('{query}', quote(text.strip(), safe=''))


# Run out of process so a hung accessibility provider cannot block the GUI or hook.
# TextPattern only: never fall back to ValuePattern (whole field). A distinct
# unsupported-pattern exit allows the guarded generic Copy/preview fallback.
SELECTION_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$e = [System.Windows.Automation.AutomationElement]::FocusedElement
if ($null -eq $e -or $e.Current.ProcessId -ne TARGET_PID) { exit 2 }
$walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
$ancestor = $e
$safe = $false
$inTarget = $false
for ($i=0; $i -lt 64; $i++) {
    if ($null -eq $ancestor) { $safe = $true; break }
    if ($ancestor.Current.IsPassword) { exit 2 }
    if ($ancestor.Current.NativeWindowHandle -eq TARGET_HWND) { $inTarget = $true }
    $ancestor = $walker.GetParent($ancestor)
}
if (-not $safe -or -not $inTarget) { exit 2 }
$p = $null
if (-not $e.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$p)) { exit 11 }
$ranges = $p.GetSelection()
if ($ranges.Length -ne 1) { exit 2 }
$text = $ranges[0].GetText(2001)
if ([string]::IsNullOrWhiteSpace($text) -or $text.Length -gt 2000) { exit 2 }
if (-not $e.Equals([System.Windows.Automation.AutomationElement]::FocusedElement)) { exit 2 }
ConvertTo-Json -InputObject $text -Compress
'''


def read_selection(target, guard=nullcontext, *, deadline=None):
    remaining = REQUEST_TIMEOUT if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
    script = SELECTION_SCRIPT.replace('TARGET_PID', str(int(target.pid))).replace('TARGET_HWND', str(int(target.top)))
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-STA', '-EncodedCommand', encoded],
                            capture_output=True, encoding='utf-8-sig', timeout=min(6, remaining),
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode == 11:
        return copy_selection(target, guard, deadline=deadline)
    if result.returncode:
        raise ValueError('Нет доступного выделения: выдели текст в поддерживаемом поле')
    return json.loads(result.stdout)


class QuickSearch:
    def __init__(self, notify, reader=None, opener=webbrowser.open, copy_guard=nullcontext):
        self.notify, self.reader, self.opener = notify, reader, opener
        self.copy_guard = copy_guard
        self.busy = False
        self.generation = 0
        self.request_deadline = None

    def configure(self):
        self.generation += 1  # A settings change cancels in-flight requests, even off/on.

    @contextmanager
    def guarded_copy(self, generation):
        if (generation != self.generation or self.request_deadline is None
                or time.monotonic() >= self.request_deadline):
            raise SelectionUnavailable('Поиск отменён: настройки изменились')
        with self.copy_guard():
            yield lambda: (generation == self.generation and
                           self.request_deadline is not None and
                           time.monotonic() < self.request_deadline)

    def request(self, target, settings, layouts):
        from app_exclusions import target_allowed
        if (self.busy or not settings.quick_search or not layouts.valid(target)
                or layouts.capture() != target or not target_allowed(target, settings)):
            return
        self.busy = True
        generation = self.generation
        deadline = self.request_deadline = time.monotonic() + REQUEST_TIMEOUT

        def run():
            try:
                selected = (self.reader(target) if self.reader is not None else
                            read_selection(target, guard=lambda: self.guarded_copy(generation),
                                           deadline=deadline))
                if time.monotonic() >= deadline:
                    raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
                url = (SearchPreview(selected.text, search_url(settings, selected.text)) if isinstance(selected, SearchPreview)
                       else search_url(settings, selected))
                if time.monotonic() >= deadline:
                    raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
                self.notify((generation, target, settings, url, None))
            except SelectionUnavailable as error:
                self.notify((generation, target, settings, None, str(error)))
            except Exception:
                # Never put the selection, URL or provider error output into logs.
                self.notify((generation, target, settings, None, 'Нет доступного выделения: выдели текст в поддерживаемом поле'))
        try:
            threading.Thread(target=run, daemon=True, name='selection-search').start()
        except RuntimeError:
            # Complete through the same GUI result path as a worker failure.
            # Keep busy until receive() consumes it, so an old failure cannot
            # clear the busy flag of a newer request. Do not expose exception
            # text (which may contain provider or user data).
            self.notify((generation, target, settings, None, 'Не удалось запустить поиск'))

    def receive(self, result, settings, layouts, closing=False):
        from app_exclusions import target_allowed
        self.busy = False
        generation, target, original, url, error = result
        deadline = self.request_deadline
        self.request_deadline = None
        if deadline is None or time.monotonic() >= deadline:
            return 'Поиск отменён: истёк срок запроса'
        fields = ('quick_search', 'quick_search_key', 'quick_search_engines', 'quick_search_engine')
        if (closing or generation != self.generation or not settings.quick_search or any(getattr(settings, f) != getattr(original, f) for f in fields)
                or not layouts.valid(target) or layouts.capture() != target or not target_allowed(target, settings)):
            return
        if time.monotonic() >= deadline:
            return 'Поиск отменён: истёк срок запроса'
        if error:
            return error
        if isinstance(url, SearchPreview):
            return url
        try:
            if time.monotonic() >= deadline:
                return 'Поиск отменён: истёк срок запроса'
            if self.opener(url):
                return 'Поиск открыт в браузере'
        except Exception:
            pass
        return 'Не удалось открыть браузер'
