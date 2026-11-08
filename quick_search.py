"""Selection search: UI Automation first, explicit Copy/preview fallback if unsupported."""
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


def read_selection(target, guard=nullcontext, *, deadline=None):
    """Read selection through persistent native UIA COM, never PowerShell.

    Unsupported TextPattern alone enables the existing guarded Copy/preview
    fallback; provider/target/identity failures fail closed without Ctrl+C.
    """
    remaining = REQUEST_TIMEOUT if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
    from uia_native_worker import get_uia_service
    sample = get_uia_service().request(
        {'command': 'selection', 'top': int(target.top),
         'focus': int(target.focus), 'pid': int(target.pid)},
        valid=lambda: deadline is None or time.monotonic() < deadline)
    if sample is None or not isinstance(sample, dict):
        raise ValueError('Нет доступного выделения: проверка поля не выполнена')
    if sample.get('state') == 'unsupported' and set(sample) == {'state'}:
        return copy_selection(target, guard, deadline=deadline)
    if set(sample) != {'state', 'text'} or sample.get('state') != 'ok':
        raise ValueError('Нет доступного выделения: выдели текст в поддерживаемом поле')
    text = sample['text']
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_QUERY or '\0' in text:
        raise ValueError('Нет доступного выделения: неподтверждённый текст')
    return text


class QuickSearch:
    def __init__(self, notify, reader=None, opener=webbrowser.open, copy_guard=nullcontext):
        self.notify, self.reader, self.opener = notify, reader, opener
        self.copy_guard = copy_guard
        self.busy = False
        self.generation = 0
        self.request_deadline = None
        self._serial = 0
        self._active_token = None
        self._active_request = None
        self._timeout_queued = None
        self._workers = set()
        self._workers_lock = threading.Lock()
        # Only one generic Ctrl+C/clipboard restoration can be in progress,
        # including a timed-out worker that has not yet returned from Win32.
        self._copy_lock = threading.Lock()

    def configure(self):
        self.generation += 1  # A settings change cancels in-flight requests, even off/on.
        self.busy = False
        self.request_deadline = None
        self._active_token = None
        self._active_request = None
        self._timeout_queued = None

    def expire_pending(self):
        """GUI timer: report timeout without waiting for a hung provider.

        Old workers may remain in-flight; their results are ignored. The
        clipboard lock continues to serialize any old/new Copy operations.
        """
        if (not self.busy or self._active_request is None or
                self.request_deadline is None
                or time.monotonic() < self.request_deadline
                or self._timeout_queued == self._active_token):
            return False
        token, generation, target, settings = self._active_request
        self._timeout_queued = token
        self.notify((generation, target, settings, None,
                     'Поиск отменён: истёк срок запроса', token))
        return True

    @contextmanager
    def guarded_copy(self, generation, token=None):
        deadline = self.request_deadline
        if (generation != self.generation or deadline is None
                or (token is not None and token != self._active_token)
                or time.monotonic() >= deadline):
            raise SelectionUnavailable('Поиск отменён: настройки изменились')
        # Acquiring a lock never extends the original deadline. An old
        # stalled provider keeps ownership; a new request fails safely.
        if not self._copy_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise SelectionUnavailable('Поиск отменён: буфер занят предыдущим запросом')
        try:
            if (generation != self.generation or
                    (token is not None and token != self._active_token) or
                    time.monotonic() >= deadline):
                raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
            with self.copy_guard():
                yield lambda: (generation == self.generation and
                               (token is None or token == self._active_token) and
                               self.request_deadline == deadline and
                               time.monotonic() < deadline)
        finally:
            self._copy_lock.release()

    def request(self, target, settings, layouts):
        from app_exclusions import target_allowed
        self.expire_pending()
        if (self.busy or not settings.quick_search or not layouts.valid(target)
                or layouts.capture() != target or not target_allowed(target, settings)):
            return
        self.busy = True
        generation = self.generation
        self._serial += 1
        token = self._active_token = self._serial
        deadline = self.request_deadline = time.monotonic() + REQUEST_TIMEOUT
        self._active_request = (token, generation, target, settings)
        self._timeout_queued = None
        with self._workers_lock:
            saturated = len(self._workers) >= 2
            if not saturated:
                self._workers.add(token)
        if saturated:
            self.notify((generation, target, settings, None,
                         'Поиск недоступен: предыдущие запросы ещё выполняются', token))
            return

        def run():
            try:
                selected = (self.reader(target) if self.reader is not None else
                            read_selection(target, guard=lambda: self.guarded_copy(generation, token),
                                           deadline=deadline))
                if time.monotonic() >= deadline:
                    raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
                url = (SearchPreview(selected.text, search_url(settings, selected.text)) if isinstance(selected, SearchPreview)
                       else search_url(settings, selected))
                if time.monotonic() >= deadline:
                    raise SelectionUnavailable('Поиск отменён: истёк срок запроса')
                self.notify((generation, target, settings, url, None, token))
            except SelectionUnavailable as error:
                self.notify((generation, target, settings, None, str(error), token))
            except Exception:
                # Never put the selection, URL or provider error output into logs.
                self.notify((generation, target, settings, None,
                             'Нет доступного выделения: выдели текст в поддерживаемом поле', token))
            finally:
                with self._workers_lock:
                    self._workers.discard(token)
        try:
            threading.Thread(target=run, daemon=True, name='selection-search').start()
        except RuntimeError:
            with self._workers_lock:
                self._workers.discard(token)
            # Complete through the same GUI result path as a worker failure.
            # Keep busy until receive() consumes it, so an old failure cannot
            # clear the busy flag of a newer request. Do not expose exception
            # text (which may contain provider or user data).
            self.notify((generation, target, settings, None, 'Не удалось запустить поиск', token))

    def receive(self, result, settings, layouts, closing=False):
        from app_exclusions import target_allowed
        if len(result) == 6:
            generation, target, original, url, error, token = result
            if token != self._active_token:
                return  # Late completion from a cancelled/expired request.
        else:
            generation, target, original, url, error = result
        self.busy = False
        deadline = self.request_deadline
        self.request_deadline = None
        self._active_token = None
        self._active_request = None
        self._timeout_queued = None
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
