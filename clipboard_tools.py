"""Opt-in, memory-only text history and explicit plain-text paste commands.

No hooks, files, clipboard writes or focus changes at import/initialization.
Native access is confined to the background reader or explicit user commands.
"""
import os
import queue
import threading
import time
from contextlib import contextmanager

from direct_hotkeys import parse_chord
from app_exclusions import effective_exclusions, target_allowed

MAX_TEXT = 100_000
MAX_TOTAL = 1_000_000


class ClipboardWriteUncertain(OSError):
    """EmptyClipboard succeeded, but the final clipboard data is uncertain.

    The observed sequence is only a conditional rollback target. It is NOT
    proof that SetClipboardData completed successfully.
    """
    def __init__(self, sequence):
        super().__init__('Временная запись в буфер не подтверждена')
        self.sequence = sequence


def plain_paste_release_inputs(events, accepted):
    """Release ONLY the unmatched accepted downs from one Ctrl+V attempt.

    Returning keyups alone avoids re-injecting a potentially accepted paste.
    The caller still must confirm that Windows accepted the cleanup packet.
    """
    from input_transport import unmatched_keydowns
    from win32_input import Input, KeyInput, OWN_INPUT_MARKER
    dangling = unmatched_keydowns(events, accepted)
    release = (Input * len(dangling))()
    for dest, (vk, scan, flags) in zip(release, reversed(dangling)):
        dest.type = 1
        dest.u.ki = KeyInput(vk, scan, flags | 2, 0, OWN_INPUT_MARKER)
    return release


class TextHistory:
    def __init__(self, limit=30):
        self.limit = limit
        self.items = []

    def add(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or '\0' in text:
            return False
        self.items[:] = [item for item in self.items if item != text]
        self.items.insert(0, text)
        del self.items[self.limit:]
        while sum(map(len, self.items)) > MAX_TOTAL:
            self.items.pop()
        return True

    def clear(self):
        self.items.clear()


def clipboard_bindings(settings):
    result = {}
    for enabled, name, action in (
        (settings.clipboard_history, settings.clipboard_history_key, 'history'),
        (settings.plain_paste, settings.plain_paste_key, 'plain-paste'),
        (getattr(settings, 'quick_search', False), getattr(settings, 'quick_search_key', 'Ctrl+Alt+G'), 'quick-search'),
    ):
        chord = parse_chord(name)  # Validate even disabled settings.
        if chord and (not chord.modifiers or chord.vk == 0x7B):
            raise ValueError('Для поиска и команд буфера выбери сочетание с Ctrl/Alt/Shift, без F12')
        if enabled and chord:
            if chord in result:
                raise ValueError('Одно сочетание назначено нескольким командам поиска или буфера')
            result[chord] = action
    return result


class NativeClipboard:
    def __init__(self):
        import win32clipboard as cb
        self.cb = cb
        self.private_formats = [cb.RegisterClipboardFormat(name) for name in (
            'ExcludeClipboardContentFromMonitorProcessing', 'Clipboard Viewer Ignore')]
        self.history_format = cb.RegisterClipboardFormat('CanIncludeInClipboardHistory')
        self.cloud_format = cb.RegisterClipboardFormat('CanUploadToCloudClipboard')

    def sequence(self):
        return self.cb.GetClipboardSequenceNumber()

    def read(self, allowed, *, history=False):
        """Return (sequence, text); busy clipboard raises, caller retries later."""
        import win32process
        cb = self.cb
        cb.OpenClipboard()
        try:
            owner = cb.GetClipboardOwner()
            pid = win32process.GetWindowThreadProcessId(owner)[1] if owner else None
            # Automatic retention needs an identifiable source. Explicit plain
            # paste may still use ownerless text at the user's request.
            if (history and not pid) or not allowed(pid):
                return self.sequence(), None
            try:
                self._require_public_content()
            except OSError:
                if history:
                    return self.sequence(), None
                raise
            if not cb.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT only.
                return self.sequence(), None
            text = cb.GetClipboardData(13)
            if not isinstance(text, str) or len(text) > MAX_TEXT or '\0' in text:
                text = None
            return self.sequence(), text
        finally:
            cb.CloseClipboard()

    def _require_public_content(self):
        """Called with the clipboard open, before reading OR discarding formats."""
        cb = self.cb
        if any(cb.IsClipboardFormatAvailable(fmt) for fmt in self.private_formats):
            raise OSError('Приватный буфер: вставка без форматирования и история отключены; используй обычный Ctrl+V')
        for fmt in (self.history_format, self.cloud_format):
            if cb.IsClipboardFormatAvailable(fmt):
                value = cb.GetClipboardData(fmt)
                if isinstance(value, (bytes, bytearray, memoryview)):
                    value = bytes(value)
                    if len(value) != 4:
                        raise OSError('Не удалось проверить признаки приватности буфера')
                    value = int.from_bytes(value, 'little')
                if type(value) is not int or value not in (0, 1):
                    raise OSError('Не удалось проверить признаки приватности буфера')
                if value == 0:
                    raise OSError('Приватный буфер: вставка без форматирования и история отключены; используй обычный Ctrl+V')

    def write_plain(self, text, hwnd, expected_sequence=None, *, allow_write=None):
        cb = self.cb
        cb.OpenClipboard(hwnd)
        emptied = False
        try:
            if expected_sequence is not None and self.sequence() != expected_sequence:
                raise OSError('Буфер изменился; повтори команду')
            self._require_public_content()
            # OpenClipboard may have been blocked by an unresponsive owner
            # until well after the request expired or the app was closed.
            if allow_write is not None and not allow_write():
                raise OSError('Вставка отменена до изменения буфера')
            cb.EmptyClipboard()
            emptied = True
            cb.SetClipboardData(13, text)
            return self.sequence()
        except Exception as error:
            if emptied:
                # Known exact owner HWND and current sequence after our
                # EmptyClipboard; a later rollback still must revalidate both.
                try:
                    observed = self.sequence()
                except Exception:
                    observed = None
                if observed is not None:
                    raise ClipboardWriteUncertain(observed) from error
            raise
        finally:
            cb.CloseClipboard()

    def prepare_plain_rollback(self, target, expected_sequence):
        """Back up all supported original formats BEFORE destroying any.

        Explicit paste cannot safely discard a rich/user clipboard while the
        foreground could still change before Ctrl+V. Unsupported or unbounded
        clipboard payloads refuse the paste rather than silently lose data.
        """
        return PlainPasteRollback(target, expected_sequence,
                                  privacy_check=self._require_public_content)


class PlainPasteRollback:
    def __init__(self, target, expected_sequence, *, privacy_check=None):
        from clipboard_selection import NativeCopy
        self.reader = NativeCopy(target, verify_copy_target=False)
        try:
            self.snapshot, self.sequence = self.reader.snapshot(
                strict=True, preflight=privacy_check)
            if expected_sequence is not None and self.sequence != expected_sequence:
                raise OSError('Буфер изменился до подготовки вставки')
            self.reader.protect_snapshot(self.snapshot)
        except Exception:
            self.reader.close()
            raise

    def restore(self, written_sequence, owner_hwnd):
        # Only call before SendInput, or after a confirmed zero-event send.
        return self.reader.restore(self.snapshot, written_sequence, None,
                                   owner_hwnd=owner_hwnd)

    def close(self):
        self.reader.close()

    def mark_write_started(self):
        self.reader._mutation_possible = True

    def commit(self):
        self.reader.discard_checkpoint()
        self.reader._mutation_possible = False


class ClipboardTools:
    def __init__(self, root, notify, native=None):
        self.root, self.notify = root, notify
        self.native = native or NativeClipboard()
        self.history = TextHistory()
        self.settings = None
        self.generation = 0
        self.closed = False
        self.pending = False
        # A delayed-rendering provider can block the sole reader indefinitely.
        # Never accumulate unbounded paste jobs behind that native call.
        self.requests = queue.Queue(maxsize=1)
        self._paste_serial = 0
        self._pending_paste_token = None
        self._pending_started = None
        self._paste_timeout_queued = None
        self.worker = None
        self.history_paused = False
        # One explicit paste at a time. A stalled clipboard provider must
        # neither block Tk nor spawn an unbounded number of paste threads.
        self._paste_worker = None

    def _start_reader(self):
        if self.worker is None:
            self.worker = threading.Thread(target=self._reader, name='TypeBridgeClipboard', daemon=True)
            self.worker.start()

    def configure(self, settings):
        self.settings = settings
        self.generation += 1
        # Any queued preparation from an older configuration is cancelled.
        # Its late result must never clear a later request's pending state.
        self.pending = False
        self._pending_paste_token = None
        self._pending_started = None
        self._paste_timeout_queued = None
        # Start with future copies; never import an old clipboard on enable.
        self.sequence = self.native.sequence()
        if not settings.clipboard_history:
            self.history.clear()
        else:
            self._start_reader()

    def clear_history(self):
        self.generation += 1
        self.pending = False
        self._pending_paste_token = None
        self._pending_started = None
        self._paste_timeout_queued = None
        self.sequence = self.native.sequence()
        self.history.clear()

    def delete_history(self, text):
        self.generation += 1
        self.pending = False
        self._pending_paste_token = None
        self._pending_started = None
        self._paste_timeout_queued = None
        self.sequence = self.native.sequence()
        if text in self.history.items:
            self.history.items.remove(text)

    def allowed_pid(self, pid):
        if not pid:
            return True
        try:
            import psutil
            return psutil.Process(pid).name().casefold() not in effective_exclusions(self.settings)
        except Exception:
            return False

    def _reader(self):
        while not self.closed:
            try:
                task = self.requests.get(timeout=.25)
            except queue.Empty:
                task = None
            if self.closed:
                return
            settings, generation = self.settings, self.generation
            try:
                if task:
                    requested_generation, target, started, token = task
                    if (requested_generation != generation or not settings.plain_paste
                            or token != self._pending_paste_token):
                        self.notify(("cancelled", requested_generation, token))
                        continue
                    seq, text = self.native.read(self.allowed_pid)
                    self.notify(('prepared', generation, target, started, seq, text, token))
                elif (settings and settings.clipboard_history
                      and not self.history_paused and not self.pending):
                    seq = self.native.sequence()
                    if seq != self.sequence:
                        actual, text = self.native.read(self.allowed_pid, history=True)
                        self.notify(('captured', generation, actual, text))
            except Exception:
                if task:
                    self.notify(('paste-error', 'Буфер недоступен; повтори команду',
                                 requested_generation, token))
                # History retries busy clipboard on the next polling interval.

    def receive(self, message):
        if self.closed:
            return
        kind = message[0]
        if kind in ('cancelled', 'prepared', 'paste-error'):
            # The reader/Win32 worker may return long after a newer request
            # started, even within the same settings generation.
            token = (message[2] if kind == 'cancelled' and len(message) >= 3
                     else message[6] if kind == 'prepared' and len(message) >= 7
                     else message[3] if kind == 'paste-error' and len(message) >= 4
                     else None)
            reported_generation = (message[1] if kind == 'cancelled'
                                   else message[1] if kind == 'prepared'
                                   else message[2] if len(message) >= 4
                                   else None)
            if (token != self._pending_paste_token
                    or (reported_generation is not None
                        and reported_generation != self.generation)):
                return
        if kind == 'cancelled':
            self.pending = False
            self._pending_paste_token = None
            self._pending_started = None
            self._paste_timeout_queued = None
            return
        if kind == 'paste-error':
            self.pending = False
            self._pending_paste_token = None
            self._pending_started = None
            self._paste_timeout_queued = None
            raise OSError(message[1])
        if message[1] != self.generation or self.closed:
            return
        if kind == 'captured':
            self.sequence = message[2]
            if self.settings.clipboard_history:
                self.history.add(message[3])
        elif kind == 'prepared':
            _, _, target, started, seq, text = message[:6]
            if not text:
                self.pending = False
                self._pending_paste_token = None
                self._pending_started = None
                self._paste_timeout_queued = None
                raise OSError('В буфере нет доступного текста')
            self._paste_when_released(
                target, text, started, seq, self.generation,
                _request_token=self._pending_paste_token)

    def request_plain(self, target):
        if self.history_paused:
            raise OSError('Буфер временно занят поиском')
        if self._paste_worker is not None and self._paste_worker.is_alive():
            raise OSError('Предыдущая вставка ещё завершается')
        if self.closed or not self.settings.plain_paste or self.pending:
            return
        if not target:
            raise OSError('Сначала выбери поле для вставки')
        self.pending = True
        self._paste_serial += 1
        self._pending_paste_token = self._paste_serial
        self._pending_started = time.monotonic()
        self._paste_timeout_queued = None
        try:
            self.requests.put_nowait((self.generation, target, self._pending_started,
                                      self._pending_paste_token))
        except queue.Full:
            self.pending = False
            self._pending_paste_token = None
            self._pending_started = None
            raise OSError('Буфер занят предыдущей операцией; повтори позже') from None
        try:
            self._start_reader()
        except Exception:
            self.pending = False
            self._pending_paste_token = None
            self._pending_started = None
            raise

    def paste_item(self, target, text, window):
        """Only called by an explicit Paste button/double click in our window."""
        import win32gui
        if self.history_paused:
            raise OSError('Буфер временно занят поиском')
        if self._paste_worker is not None and self._paste_worker.is_alive():
            raise OSError('Предыдущая вставка ещё завершается')
        from tray_layouts import WindowsLayouts
        if self.pending:
            return
        layouts = WindowsLayouts()
        if not layouts.valid(target) or target.pid == os.getpid():
            raise OSError('Исходное окно закрыто; скопируй запись кнопкой «Копировать»')
        foreground = win32gui.GetForegroundWindow()
        if win32gui.GetAncestor(foreground, 2) != win32gui.GetAncestor(window.winfo_id(), 2):
            raise OSError('Активное окно изменилось; повтори выбор')
        window.withdraw()
        win32gui.SetForegroundWindow(target.top)
        self.pending = True
        self._paste_serial += 1
        self._pending_paste_token = self._paste_serial
        self._pending_started = time.monotonic()
        self._paste_timeout_queued = None
        self._paste_when_released(
            target, text, self._pending_started, None, self.generation,
            _request_token=self._pending_paste_token)

    def _paste_when_released(self, target, text, started, sequence, generation,
                             *, _worker=False, _owner_hwnd=None,
                             _request_token=None):
        import ctypes
        import win32api
        import win32gui
        from tray_layouts import WindowsLayouts
        from win32_input import Input, KeyInput, OWN_INPUT_MARKER
        if not _worker and isinstance(self.native, NativeClipboard):
            # All potentially blocking clipboard APIs, including snapshot,
            # delayed rendering, write and rollback, run off the Tk thread.
            # Only capture the owner HWND through Tk while on the GUI thread.
            if self._paste_worker is not None and self._paste_worker.is_alive():
                return  # An older worker still owns the transaction.
            if self.closed or generation != self.generation:
                if _request_token == self._pending_paste_token:
                    self.pending = False
                    self._pending_paste_token = None
                    self._pending_started = None
                return
            owner = self.root.winfo_id()
            worker = threading.Thread(
                target=self._paste_when_released,
                args=(target, text, started, sequence, generation),
                kwargs={'_worker': True, '_owner_hwnd': owner,
                        '_request_token': _request_token},
                daemon=True, name='TypeBridgePlainPaste')
            self._paste_worker = worker
            try:
                worker.start()
            except RuntimeError:
                self._paste_worker = None
                self.notify(('paste-error', 'Не удалось запустить безопасную вставку',
                             generation, _request_token))
            return
        rollback = None
        changed_sequence = None
        send_attempted = False
        try:
            if self.closed or generation != self.generation or time.monotonic() - started > 2:
                raise OSError('Вставка отменена: клавиши не отпущены вовремя')
            layouts = WindowsLayouts()
            if not layouts.valid(target) or layouts.capture() != target:
                raise OSError('Вставка отменена: поле ввода изменилось')
            if not target_allowed(target, self.settings) or win32gui.GetWindowLong(target.focus, -16) & 0x20:
                raise OSError('Вставка в это поле отключена')
            def keys_pressed():
                return any(win32api.GetAsyncKeyState(vk) & 0x8000 for vk in
                           (0x10, 0x11, 0x12, 0x5B, 0x5C))
            if _worker:
                # Never call Tk from the worker. Keep the original two-second
                # deadline while waiting for the physical hotkey to be released.
                while keys_pressed():
                    if (self.closed or generation != self.generation
                            or time.monotonic() - started > 2):
                        raise OSError('Вставка отменена: клавиши не отпущены вовремя')
                    time.sleep(.03)
            elif keys_pressed():
                self.root.after(30, lambda: self._paste_when_released(
                    target, text, started, sequence, generation,
                    _request_token=_request_token))
                return
            # Back up rich formats before the destructive conversion. This
            # can fail closed for unsupported clipboard types; no Ctrl+V is
            # attempted and the user's original clipboard remains untouched.
            rollback = self.native.prepare_plain_rollback(target, sequence)
            if self.closed or generation != self.generation or time.monotonic() - started > 2:
                raise OSError('Вставка отменена: превышено время подготовки')
            if not layouts.valid(target) or layouts.capture() != target:
                raise OSError('Вставка отменена: поле ввода изменилось')
            write_kwargs = {}
            if isinstance(self.native, NativeClipboard):
                write_kwargs['allow_write'] = lambda: (
                    not self.closed and generation == self.generation
                    and time.monotonic() - started <= 2
                    and layouts.valid(target) and layouts.capture() == target
                    and not keys_pressed())
            rollback.mark_write_started()
            written = self.native.write_plain(
                text, _owner_hwnd if _worker else self.root.winfo_id(),
                rollback.sequence, **write_kwargs)
            changed_sequence = written
            if (not layouts.valid(target) or layouts.capture() != target
                    or self.native.sequence() != written
                    or self.closed or generation != self.generation
                    or time.monotonic() - started > 2):
                raise OSError("Вставка отменена: окно или буфер изменились")
            # Ordinary Ctrl+V preserves native multi-line/Undo behavior. Only
            # Unicode text is offered; rich formats are intentionally removed.
            events = (Input * 4)()
            for item, (vk, flags) in zip(events, ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2))):
                item.type = 1
                item.u.ki = KeyInput(vk, 0, flags, 0, OWN_INPUT_MARKER)
            api = ctypes.WinDLL('user32', use_last_error=True)
            api.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(Input), ctypes.c_int]
            api.SendInput.restype = ctypes.c_uint
            if (not layouts.valid(target) or layouts.capture() != target
                    or self.native.sequence() != written
                    or time.monotonic() - started > 2
                    or any(win32api.GetAsyncKeyState(vk) & 0x8000
                           for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))):
                raise OSError('Вставка отменена: условия изменились перед отправкой')
            # Once a nonzero INPUT prefix may have reached an editor, we
            # cannot restore the old clipboard: Ctrl+V may still be pending.
            send_attempted = True
            sent = api.SendInput(4, events, ctypes.sizeof(Input))
            if sent != 4:
                # A short SendInput can accept CtrlDown + VDown without any
                # matching releases. Send only KEYUP for each accepted,
                # unmatched KEYDOWN; never reissue Ctrl+V (it may have pasted).
                if not 0 <= sent <= 4:
                    raise OSError('Неверное число событий SendInput; вставка не подтверждена')
                if sent == 0:
                    send_attempted = False
                release = plain_paste_release_inputs(events, int(sent))
                if len(release):
                    released = api.SendInput(len(release), release, ctypes.sizeof(Input))
                    if released != len(release):
                        self.notify(('paste-error', 'Отпускание клавиш после неполной вставки не подтверждено'))
                raise OSError('Вставка не подтверждена; повтор автоматически отменён')
            rollback.commit()  # Plain clipboard is the explicitly requested result.
            if _request_token == self._pending_paste_token:
                self.pending = False
                self._pending_paste_token = None
                self._pending_started = None
                self._paste_timeout_queued = None
        except Exception as error:
            if changed_sequence is None and isinstance(error, ClipboardWriteUncertain):
                changed_sequence = error.sequence
            if rollback is not None and not send_attempted:
                try:
                    current_sequence = self.native.sequence()
                    if current_sequence == rollback.sequence and changed_sequence is None:
                        restored = True  # Refused before EmptyClipboard.
                    elif changed_sequence is not None:
                        restored = rollback.restore(
                            changed_sequence,
                            _owner_hwnd if _worker else self.root.winfo_id())
                    else:
                        # Another clipboard change without evidence of a
                        # mutation by this transaction: never overwrite it.
                        restored = False
                except Exception:
                    restored = False
                if not restored:
                    error = OSError('Вставка отменена; исходный буфер не удалось восстановить '
                                    '(содержимое могло измениться). Зашифрованная копия сохранена; '
                                    'выбери в трее «Восстановить резервную копию буфера…»')
                else:
                    rollback.commit()
            if _request_token is None and self._pending_paste_token is None:
                # Legacy direct/model invocation has no queued GUI result
                # ownership to resolve. Live requests retain their token
                # until receive() consumes the matching error.
                self.pending = False
            self.notify(('paste-error', str(error), generation, _request_token))
        finally:
            if rollback is not None:
                try:
                    rollback.close()
                except Exception:
                    self.notify(('paste-error', 'Ошибка освобождения резервной копии буфера',
                                 generation, _request_token))

    def close(self):
        self.closed = True
        self.generation += 1
        self.pending = False
        self._pending_paste_token = None
        self._pending_started = None
        self._paste_timeout_queued = None
        self.history.clear()
        try:
            self.requests.put_nowait(None)
        except queue.Full:
            # Reader wakes from its current call or queue.get(timeout=.25)
            # and observes self.closed; GUI shutdown never waits for it.
            pass

    def expire_pending(self):
        """GUI deadline for a stuck reader/provider, without killing Win32 I/O."""
        token = self._pending_paste_token
        if (self.closed or not self.pending or token is None
                or self._pending_started is None
                or time.monotonic() - self._pending_started <= 2
                or self._paste_timeout_queued == token):
            return False
        self._paste_timeout_queued = token
        self.notify(('paste-error', 'Вставка отменена: время ожидания буфера истекло',
                     self.generation, token))
        return True

    @contextmanager
    def selection_copy(self):
        if (self.pending or self.history_paused or self.closed
                or (self._paste_worker is not None and self._paste_worker.is_alive())):
            from clipboard_selection import SelectionUnavailable
            raise SelectionUnavailable('Поиск отменён: буфер занят другой командой')
        self.history_paused = True
        self.generation += 1  # Discard queued or in-flight history captures.
        try:
            yield
        finally:
            try:
                self.sequence = self.native.sequence()
            finally:
                self.generation += 1
                self.history_paused = False
