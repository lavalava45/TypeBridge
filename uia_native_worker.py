"""Persistent, read-only STA UI Automation caret reader (no PowerShell)."""
import ctypes
from ctypes import wintypes
import json
import multiprocessing
import threading
import time


class UiaService:
    """Bounded, serialized IPC; one persistent process, no provider on caller."""

    def __init__(self, *, context=None, timeout=2.5, synthetic=False):
        self.context = context or multiprocessing.get_context('spawn')
        self.timeout = timeout
        self.synthetic = synthetic
        self.lock = threading.Lock()
        self.process = None
        self.connection = None
        self.retry_at = 0.0

    def _close_locked(self):
        connection, self.connection = self.connection, None
        process, self.process = self.process, None
        if connection is not None:
            connection.close()
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
                process.join(.2)
                if process.is_alive():
                    process.kill()
                    process.join(.2)
                if not process.is_alive():
                    process.close()
                else:
                    # Never spawn a second child while the old provider is alive.
                    self.process = process
            except (OSError, ValueError, AssertionError):
                self.process = process if process.pid is not None else None

    def close(self):
        with self.lock:
            self._close_locked()

    def _start_locked(self):
        if self.process is not None:
            return self.connection is not None
        parent, child = self.context.Pipe(duplex=True)
        self.connection = parent
        try:
            self.process = self.context.Process(target=worker_main,
                                                args=(child, self.synthetic), daemon=True)
            self.process.start()
        except (OSError, RuntimeError, AssertionError):
            # A refused spawn has no live child. Never retain an unstarted
            # Process: is_alive() and join() may themselves raise AssertionError.
            self.connection = None
            parent.close()
            process, self.process = self.process, None
            if process is not None:
                try:
                    process.close()
                except (OSError, ValueError, AssertionError):
                    pass
            raise
        finally:
            child.close()
        if not parent.poll(self.timeout) or parent.recv_bytes(256) != b'ready':
            raise TimeoutError('uia-startup')
        return True

    def request(self, command, *, valid=lambda: True, timeout=None, on_busy=None):
        if not isinstance(command, dict) or not valid():
            return None
        try:
            payload = json.dumps(command, ensure_ascii=False).encode('utf-8')
        except (TypeError, ValueError):
            return None
        if len(payload) > 1024:
            return None
        # Real browser metadata reads can occupy the shared COM worker for
        # ~70-90 ms. A 50 ms acquisition budget therefore rejects an ordinary
        # caret recovery despite a healthy provider. Allow a longer, STILL
        # bounded wait only for the off-hook caret command; periodic metadata
        # queries retain their shorter budget and always fail closed.
        lock_budget = (.125 if command.get('command', command.get('cmd'))
                       in ('caret', 'selected-word') else .05)
        if not self.lock.acquire(timeout=lock_budget):
            # A stalled UIA provider belongs to the background child; callers
            # must not queue behind its COM call for the full provider timeout.
            # Only lock contention yields on_busy; child/provider failure still
            # yields None. This allows the password watcher to retain an
            # EXISTING fresh lease, without extending its 500 ms expiry.
            return on_busy
        try:
            if time.monotonic() < self.retry_at or not valid():
                return None
            try:
                if not self._start_locked() or not valid():
                    return None
                self.connection.send_bytes(payload)
                if not self.connection.poll(self.timeout if timeout is None else min(self.timeout, max(.01, timeout))):
                    raise TimeoutError('uia-read')
                response = json.loads(self.connection.recv_bytes(12000))
                if not valid() or not isinstance(response, dict):
                    return None
                return response.get('sample') if set(response) == {'sample'} else response
            except (EOFError, OSError, TimeoutError, ValueError, TypeError,
                    AssertionError, RuntimeError):
                self._close_locked()
                self.retry_at = time.monotonic() + 1
                return None
        finally:
            self.lock.release()


_service = None
_service_lock = threading.Lock()


def get_uia_service():
    global _service
    with _service_lock:
        if _service is None:
            _service = UiaService()
        return _service


class _Rect(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]


class _Gui(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('active', wintypes.HWND), ('focus', wintypes.HWND),
                ('capture', wintypes.HWND), ('menu', wintypes.HWND),
                ('move', wintypes.HWND), ('caret', wintypes.HWND),
                ('rect', _Rect)]


def _scope():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_Gui)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    info = _Gui()
    info.size = ctypes.sizeof(info)
    if not user32.GetGUIThreadInfo(0, ctypes.byref(info)):
        return None
    return int(user32.GetForegroundWindow() or 0), int(info.focus or 0)


def _runtime_id(element):
    return 'uia:' + ','.join(str(int(value)) for value in element.GetRuntimeId())


def _safe_target(automation, element, request, uia):
    simple_mode = request.get('simple_mode') is True
    if _scope() != (request['top'], request['focus']):
        return False
    if not automation.CompareElements(element, automation.GetFocusedElement()):
        return False
    if _runtime_id(element) != request['element_id']:
        return False
    if not simple_mode and element.GetCurrentPropertyValue(uia.UIA_IsPasswordPropertyId) is not False:
        return False
    ancestor = element
    inside = False
    walker = automation.RawViewWalker
    for _ in range(64):
        if not ancestor:
            return inside
        # Chromium may not implement IsPassword on non-editable ancestors.
        # A positively protected ancestor ALWAYS vetoes the operation, but
        # an unsupported ancestor alone is not positive password evidence.
        # The focused leaf above must explicitly report False; provider
        # exceptions here still fail closed through the caller's catch.
        if not simple_mode and ancestor.GetCurrentPropertyValue(uia.UIA_IsPasswordPropertyId) is True:
            return False
        handle = ancestor.GetCurrentPropertyValue(uia.UIA_NativeWindowHandlePropertyId)
        if type(handle) is int and handle == request['top']:
            inside = True
        ancestor = walker.GetParentElement(ancestor)
    return False


def read_native_caret(automation, request, uia, *, reason_out=None):
    """Two selection/text samples bound to one focused element; fail closed."""
    def refused(reason):
        # No text, keystrokes, HWND or UIA Runtime ID in diagnostic results.
        if reason_out is not None:
            reason_out.append(reason)
        return None

    base = {'top', 'focus', 'element_id'}
    if (not isinstance(request, dict)
            or set(request) not in (base, base | {'simple_mode'})
            or ('simple_mode' in request and request['simple_mode'] is not True)):
        return refused('invalid-request')
    if any(type(request[key]) is not int or request[key] <= 0 for key in ('top', 'focus')):
        return refused('invalid-scope')
    expected = request['element_id']
    if not isinstance(expected, str) or len(expected) > 256 or (expected and not expected.startswith('uia:')):
        return refused('invalid-element-id')
    try:
        element = automation.GetFocusedElement()
        if element is None:
            return refused('no-focused-element')
        if not expected:
            request = dict(request, element_id=_runtime_id(element))
        if not _safe_target(automation, element, request, uia):
            return refused('target-unverified')
        unknown = element.GetCurrentPattern(uia.UIA_TextPatternId)
        if not unknown:
            return refused('text-pattern-unsupported')
        pattern = unknown.QueryInterface(uia.IUIAutomationTextPattern)
        selections = pattern.GetSelection()
        if selections.Length != 1:
            return refused('selection-count-unverified')
        caret = selections.GetElement(0).Clone()
        start, end = uia.TextPatternRangeEndpoint_Start, uia.TextPatternRangeEndpoint_End
        if caret.CompareEndpoints(start, caret, end) != 0:
            return refused('noncollapsed-selection')
        def bounded_span(endpoint, direction, side):
            # A Chromium UIA provider can expand a Character endpoint into
            # more UTF-16 units than the parser's 81-unit budget (for example
            # when the range includes surrogate pairs or line boundaries).
            # Instead of trimming GetText, which could discard the characters
            # nearest the caret, retry with a *smaller range anchored at that
            # same caret*. The parser still proves a complete word boundary.
            for width in (81, 64, 48):
                span = caret.Clone()
                span.MoveEndpointByUnit(endpoint, uia.TextUnit_Character,
                                        direction * width)
                if not _safe_target(automation, element, request, uia):
                    return None, None, 'target-changed-during-range'
                value = span.GetText(163)  # bounded provider read, never entire document
                if not isinstance(value, str):
                    # Do not mislabel a malformed provider result as a long
                    # range; a shorter request cannot validate a non-string.
                    return None, None, 'range-' + side + '-invalid-text'
                if len(value.encode('utf-16-le')) <= 162:
                    return span, value, None
            # Side-only diagnostic; never log provider text or field content.
            return None, None, 'range-' + side + '-content-out-of-bounds'

        left, before, left_error = bounded_span(start, -1, 'before')
        if left_error:
            return refused(left_error)
        right, after, right_error = bounded_span(end, 1, 'after')
        if right_error:
            return refused(right_error)
        at_start = left.CompareEndpoints(start, pattern.DocumentRange, start) == 0
        at_end = right.CompareEndpoints(end, pattern.DocumentRange, end) == 0
        again = pattern.GetSelection()
        if (again.Length != 1
                or again.GetElement(0).CompareEndpoints(start, caret, start) != 0
                or again.GetElement(0).CompareEndpoints(end, caret, end) != 0
                or not _safe_target(automation, element, request, uia)
                or left.GetText(163) != before or right.GetText(163) != after
                or not _safe_target(automation, element, request, uia)):
            return refused('range-changed-or-target-unverified')
        return {'before': before, 'after': after, 'at_start': at_start,
                'at_end': at_end, 'element_id': request['element_id']}
    except Exception:
        return refused('provider-exception')


class FocusEventMonitor:
    """Count UIA focus/password events even between metadata polling samples.

    Event callbacks never inspect or log editor content. COM subscriptions and
    provider operations are confined to the dedicated worker process.
    """

    def __init__(self, automation, uia, comtypes_module):
        self.automation, self.uia = automation, uia
        self.guard = threading.Lock()
        self.generation = 0
        self.pending_refresh = True
        self.ready = False
        self.element = None
        self.property_events_available = False
        self.retry_at = 0.0

        monitor = self

        class FocusHandler(comtypes_module.COMObject):
            _com_interfaces_ = [uia.IUIAutomationFocusChangedEventHandler]

            def HandleFocusChangedEvent(self, sender):
                with monitor.guard:
                    monitor.generation += 1
                    monitor.pending_refresh = True
                return 0

        class PropertyHandler(comtypes_module.COMObject):
            _com_interfaces_ = [uia.IUIAutomationPropertyChangedEventHandler]

            def HandlePropertyChangedEvent(self, sender, propertyId, newValue):
                if propertyId == uia.UIA_IsPasswordPropertyId:
                    with monitor.guard:
                        monitor.generation += 1
                return 0

        self.focus_handler = FocusHandler()
        self.property_handler = PropertyHandler()
        # comtypes.COMObject itself is not always accepted as the method's
        # typed IUIAutomation*EventHandler pointer (notably after a focus
        # callback). Keep explicit QueryInterface pointers alive across all
        # registration/removal calls.
        self.focus_interface = self.focus_handler.QueryInterface(
            uia.IUIAutomationFocusChangedEventHandler)
        self.property_interface = self.property_handler.QueryInterface(
            uia.IUIAutomationPropertyChangedEventHandler)

    def start(self):
        self.automation.AddFocusChangedEventHandler(None, self.focus_interface)
        try:
            if not self.refresh():
                raise OSError('uia-property-event-registration-failed')
        except Exception:
            self.automation.RemoveFocusChangedEventHandler(self.focus_interface)
            raise
        self.ready = True

    def refresh(self):
        """Rebind password-property watcher after focus events, fail closed."""
        if time.monotonic() < self.retry_at:
            return False
        with self.guard:
            started_generation = self.generation
            self.pending_refresh = True
        self.ready = False
        try:
            focused = self.automation.GetFocusedElement()
            if self.element is not None:
                self.automation.RemovePropertyChangedEventHandler(self.element, self.property_interface)
            self.element = None
            self.property_events_available = False
            if focused is not None:
                # NativeArray avoids the generated SAFEARRAY marshaling error;
                # the COM pointer is explicitly QueryInterface-typed.
                property_ids = (ctypes.c_long * 1)(self.uia.UIA_IsPasswordPropertyId)
                self.automation.AddPropertyChangedEventHandlerNativeArray(
                    focused, self.uia.TreeScope_Element, None,
                    self.property_interface, property_ids, 1)
                self.element = focused
                self.property_events_available = True
        except Exception:
            # Focus/provider transitions can race event re-registration.
            # Do not crash/restart the child or authorize stale metadata;
            # remain unavailable until a later bounded re-registration.
            self.retry_at = time.monotonic() + .5
            return False
        with self.guard:
            # A focus callback arriving DURING the provider call must not be
            # swallowed by this refresh. It needs another full rebind.
            self.pending_refresh = self.generation != started_generation
        self.ready = True
        self.retry_at = 0.0
        return True

    def current(self):
        with self.guard:
            return self.generation, self.pending_refresh

    def stop(self):
        self.ready = False
        try:
            if self.element is not None:
                self.automation.RemovePropertyChangedEventHandler(self.element, self.property_interface)
        finally:
            self.element = None
            self.automation.RemoveFocusChangedEventHandler(self.focus_interface)


def _metadata_with_generation(probe, monitor, pump):
    """Never authorize a probe that straddled an observed focus/password event.

    The callback generation is observed on BOTH sides of the metadata query.
    The next stable poll may recover; this one cannot certify a stable target.
    No document text or arbitrary provider exception enters diagnostics.
    """
    before, pending = monitor.current()
    if pending or not monitor.ready:
        return None
    result = probe()
    pump(.001)
    after, pending = monitor.current()
    if pending or not monitor.ready or before != after:
        return None
    if not isinstance(result, str) or result.count('|') != 6:
        return None
    return result + '|' + str(after)


def worker_main(connection, synthetic=False):
    """Single STA process serves bounded IPC requests until parent disconnects."""
    comtypes = None
    monitor = None
    initialized = False
    try:
        if not synthetic:
            import comtypes
            from comtypes.client import CreateObject
            from comtypes.gen import UIAutomationClient as uia
            from uia_selection_native import (read_native_selection as selection_reader,
                                              read_native_selected_word,
                                              SelectedWordLeases)
            from uia_password_native import PasswordProbe, WinScope
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
            initialized = True
            automation = CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
            # Start the password/focus subscription lazily. The user-requested
            # SIMPLE switcher has no password metadata watcher and its one-shot
            # selected-word reads must not depend on password subscriptions.
            # Strict legacy metadata and selected-word requests still start it.
            selection_leases = SelectedWordLeases()
        connection.send_bytes(b'ready')
        while True:
            if monitor is not None:
                # Connection.poll alone blocks STA callbacks. Pump Windows/COM
                # messages frequently while bounding the idle scheduling
                # latency of each caret IPC request. The 2ms budget is NOT a
                # measured bound on COM provider execution or Windows timers.
                comtypes.client.PumpEvents(.002)
                if monitor.current()[1]:
                    monitor.refresh()
            if not connection.poll(.003):
                continue
            payload = connection.recv_bytes(1024)
            if payload == b'stop':
                break
            try:
                request = json.loads(payload)
                if synthetic:
                    # IPC/process lifecycle test only: never initialize COM,
                    # inspect focus, or read any user document.
                    result = None
                elif not isinstance(request, dict):
                    result = None
                elif request.get('command', request.get('cmd', 'caret')) == 'caret':
                    want_reason = request.get('diagnostic') is True
                    refusal_reasons = [] if want_reason else None
                    result = read_native_caret(automation,
                                               {k: v for k, v in request.items() if k not in ('command', 'cmd', 'diagnostic')},
                                               uia, reason_out=refusal_reasons)
                    if want_reason and result is None:
                        result = {'state': 'unavailable',
                                  'reason': (refusal_reasons[-1]
                                             if refusal_reasons else 'unknown')}
                elif request.get('command', request.get('cmd')) == 'selection':
                    result = selection_reader(automation,
                                                   {k: v for k, v in request.items() if k not in ('command', 'cmd')}, uia)
                elif request.get('command', request.get('cmd')) == 'selected-word':
                    # Must be read-only. A selection is categorically different
                    # from a collapsed caret: no Backspace-prefix fallback.
                    want_reason = (request.get('diagnostic') is True
                                   and request.get('mode') == 'read')
                    selection_reasons = [] if want_reason else None
                    selected_request = {k: v for k, v in request.items()
                                        if k not in ('command', 'cmd', 'diagnostic')}
                    if request.get('simple_mode') is True:
                        # No UIA_IsPassword property inspection/subscription.
                        # Identity + exact range endpoints are checked twice
                        # by the selection reader and its one-shot lease.
                        result = read_native_selected_word(
                            automation, selected_request, uia, generation=0,
                            leases=selection_leases, reason_out=selection_reasons)
                    else:
                        if monitor is None:
                            monitor = FocusEventMonitor(automation, uia, comtypes)
                            monitor.start()
                        generation, pending = monitor.current()
                        if pending or not monitor.ready:
                            result = {'state': 'refused'}
                            selection_leases.clear()
                        else:
                            result = read_native_selected_word(
                                automation, selected_request, uia,
                                generation=generation, leases=selection_leases,
                                reason_out=selection_reasons)
                            comtypes.client.PumpEvents(.001)
                            if monitor.current() != (generation, False):
                                result = {'state': 'refused'}
                                selection_leases.clear()
                    if (want_reason and isinstance(result, dict)
                            and result.get('state') in ('refused', 'unsupported')):
                        result = {'state': result['state'],
                                  'reason': (selection_reasons[-1]
                                             if selection_reasons else 'selection-unverified')}
                elif request.get('command', request.get('cmd')) == 'metadata':
                    if monitor is None:
                        monitor = FocusEventMonitor(automation, uia, comtypes)
                        monitor.start()
                    if monitor.current()[1]:
                        monitor.refresh()
                    result = _metadata_with_generation(
                        lambda: PasswordProbe(automation, uia, WinScope()).query(),
                        monitor, comtypes.client.PumpEvents)
                else:
                    # Reserved for the password/selection helpers. No unknown
                    # command can turn into an unsafe caret read.
                    result = None
                if (not synthetic and isinstance(request, dict)
                        and request.get('command', request.get('cmd')) in ('selection', 'selected-word', 'metadata')):
                    if request.get('command', request.get('cmd')) == 'metadata':
                        result = {'sample': result}
                    connection.send_bytes(json.dumps(result, ensure_ascii=False).encode('utf-8'))
                else:
                    connection.send_bytes(json.dumps({'sample': result}, ensure_ascii=False).encode('utf-8'))
            except (ValueError, TypeError, OSError):
                connection.send_bytes(b'{"sample":null}')
    except (EOFError, OSError, BrokenPipeError):
        pass
    finally:
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                pass
        connection.close()
        if initialized:
            comtypes.CoUninitialize()
