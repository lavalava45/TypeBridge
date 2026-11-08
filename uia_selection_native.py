"""Read one verified UIA selection in the isolated native COM worker.

No clipboard, global input or whole-document ValuePattern access. Imported
only by the worker when a QuickSearch selection is explicitly requested.
"""
import hashlib
import secrets
import time


class SelectedWordLeases:
    """At most one short-lived STA-owned COM text range, never serialized.

    Invalid requests also consume their lease: a failed pre-send verification
    cannot be retried against an old browser selection.
    """

    def __init__(self, *, lifetime=3.0, clock=time.monotonic):
        self.lifetime = lifetime
        self.clock = clock
        self.entry = None

    def issue(self, selected, *, top, focus, element_id, generation, digest):
        self.entry = None
        saved = selected.Clone()  # Exact UIA endpoints, retained in this STA.
        token = secrets.token_hex(16)
        self.entry = (token, self.clock(), saved, top, focus,
                      element_id, generation, digest)
        return token

    def consume(self, token):
        entry, self.entry = self.entry, None
        if (entry is None or type(token) is not str or token != entry[0]
                or self.clock() - entry[1] < 0
                or self.clock() - entry[1] > self.lifetime):
            return None
        return entry

    def clear(self):
        self.entry = None


def read_native_selected_word(automation, request, uia, *, generation, leases,
                              reason_out=None):
    """Read/verify ONE selected word; no caret fallback or text mutation.

    `mode=read` returns a short selection and opaque worker-local range lease.
    `mode=verify` consumes the lease, checks its exact original endpoints,
    then compares the current selection without returning any text.
    Both modes require the same focused Runtime ID and observed UIA generation.
    A caller must independently recheck immediately before SendInput; this
    cannot make a browser SendInput transaction atomic with its DOM target.
    """
    from uia_native_worker import _safe_target

    refused = {'state': 'refused'}
    def deny(reason):
        # Diagnostic codes are fixed strings: never expose the selection,
        # UIA Runtime ID, HWND, document or COM exception text.
        if reason_out is not None:
            reason_out.append(reason)
        return refused
    if not isinstance(leases, SelectedWordLeases):
        return deny('lease-service-unavailable')
    if isinstance(request, dict) and request.get('mode') == 'read':
        # Starting a new read attempt revokes any previous selection lease,
        # even if the new selection cannot be independently verified.
        leases.clear()
    # A verify consumes its token on entry, even if the supplied target, hash,
    # generation, or COM provider later fails. One shot by construction.
    previous = (leases.consume(request.get('lease'))
                if isinstance(request, dict) and request.get('mode') == 'verify'
                else None)
    simple_mode = isinstance(request, dict) and request.get('simple_mode') is True
    supplied_id = request.get('element_id') if isinstance(request, dict) else None
    allow_capture = simple_mode and request.get('mode') == 'read' and supplied_id == ''
    if (not isinstance(request, dict)
            or request.get('mode') not in ('read', 'verify')
            or not isinstance(supplied_id, str)
            or (not allow_capture and (not supplied_id.startswith('uia:') or not supplied_id[4:]))
            or len(supplied_id) > 256
            or type(request.get('top')) is not int or request['top'] <= 0
            or type(request.get('focus')) is not int or request['focus'] <= 0
            or (request['mode'] == 'verify' and (
                type(request.get('generation')) is not int
                or request['generation'] != generation))
            or (request['mode'] == 'read' and 'generation' in request
                and (type(request['generation']) is not int
                     or request['generation'] != generation))):
        return deny('invalid-selection-request')
    if request['mode'] == 'verify' and (
            not isinstance(request.get('digest'), str)
            or len(request['digest']) != 64
            or any(c not in '0123456789abcdef' for c in request['digest'])
            or not isinstance(request.get('lease'), str)
            or len(request['lease']) != 32
            or previous is None):
        return deny('invalid-verification-token')
    extra = {'simple_mode'} if simple_mode else set()
    if not (set(request) in ({'mode', 'top', 'focus', 'element_id'} | extra,
                             {'mode', 'top', 'focus', 'element_id', 'generation'} | extra)
            if request['mode'] == 'read' else
            set(request) == {'mode', 'top', 'focus', 'element_id', 'generation', 'digest', 'lease'} | extra):
        return deny('invalid-request-fields')
    if request['mode'] == 'verify' and (
            previous[3:] != (request['top'], request['focus'],
                             request['element_id'], request['generation'],
                             request['digest'])):
        return deny('selection-lease-mismatch')
    try:
        element = automation.GetFocusedElement()
        if not element:
            return deny('focused-element-unavailable')
        if allow_capture:
            from uia_native_worker import _runtime_id
            actual_id = _runtime_id(element)
            if (len(actual_id) > 256 or not actual_id.startswith('uia:')
                    or not actual_id[4:]):
                return deny('element-identity-unavailable')
            request = {**request, 'element_id': actual_id}
        if not _safe_target(automation, element, request, uia):
            return deny('selection-target-unverified')
        unknown = element.GetCurrentPattern(uia.UIA_TextPatternId)
        if not unknown:
            if reason_out is not None:
                reason_out.append('text-pattern-unsupported')
            return {'state': 'unsupported'}
        pattern = unknown.QueryInterface(uia.IUIAutomationTextPattern)
        ranges = pattern.GetSelection()
        if not ranges or ranges.Length != 1:
            return deny('selection-count-unverified')
        selected = ranges.GetElement(0).Clone()
        start, end = uia.TextPatternRangeEndpoint_Start, uia.TextPatternRangeEndpoint_End
        if selected.CompareEndpoints(start, selected, end) == 0:
            return deny('selection-collapsed')  # A caret is NOT a selection.
        # Read just 41 UTF-16 characters and reject truncation; never inspect
        # the whole document or ValuePattern.Value.
        text = selected.GetText(41)
        if not isinstance(text, str):
            return deny('selected-text-invalid-type')
        if not 1 <= len(text) <= 40 or len(text.encode('utf-16-le')) > 80:
            return deny('selected-text-out-of-bounds')
        if any(not c.isprintable() or c.isspace() for c in text) or '\0' in text:
            return deny('selected-text-invalid-characters')
        digest = hashlib.sha256(text.encode('utf-16-le')).hexdigest()
        repeat = pattern.GetSelection()
        if (not repeat or repeat.Length != 1
                or repeat.GetElement(0).CompareEndpoints(start, selected, start) != 0
                or repeat.GetElement(0).CompareEndpoints(end, selected, end) != 0
                or selected.GetText(41) != text
                or not _safe_target(automation, element, request, uia)):
            return deny('selection-changed-or-target-unverified')
        if request['mode'] == 'verify':
            original = previous[2]
            return ({'state': 'matched'}
                    if (digest == request['digest']
                        and original.CompareEndpoints(start, selected, start) == 0
                        and original.CompareEndpoints(end, selected, end) == 0)
                    else deny('selection-lease-recheck-failed'))
        lease = leases.issue(selected, top=request['top'], focus=request['focus'],
                             element_id=request['element_id'],
                             generation=generation, digest=digest)
        return {'state': 'ok', 'text': text, 'element_id': request['element_id'],
                'generation': generation, 'digest': digest, 'lease': lease}
    except Exception:
        return deny('selection-provider-exception')


def read_native_selection(automation, request, uia):
    """Return a small selection or a bounded reason; never mutate the editor."""
    if (not isinstance(request, dict)
            or set(request) != {'top', 'focus', 'pid'}
            or any(type(request[key]) is not int or request[key] <= 0
                   for key in ('top', 'focus', 'pid'))):
        return {'state': 'refused'}
    from uia_native_worker import _runtime_id, _safe_target

    try:
        element = automation.GetFocusedElement()
        if element is None:
            return {'state': 'refused'}
        process_id = element.GetCurrentPropertyValue(uia.UIA_ProcessIdPropertyId)
        if type(process_id) is not int or process_id != request['pid']:
            return {'state': 'refused'}
        expected = {**request, 'element_id': _runtime_id(element)}
        if not _safe_target(automation, element, expected, uia):
            return {'state': 'refused'}
        unknown = element.GetCurrentPattern(uia.UIA_TextPatternId)
        if not unknown:
            return {'state': 'unsupported'}
        pattern = unknown.QueryInterface(uia.IUIAutomationTextPattern)
        selections = pattern.GetSelection()
        if selections is None or selections.Length != 1:
            return {'state': 'refused'}
        selected = selections.GetElement(0).Clone()
        start, end = uia.TextPatternRangeEndpoint_Start, uia.TextPatternRangeEndpoint_End
        if selected.CompareEndpoints(start, selected, end) == 0:
            return {'state': 'refused'}
        text = selected.GetText(2001)
        if (not isinstance(text, str) or not text.strip()
                or len(text) > 2000 or '\0' in text):
            return {'state': 'refused'}
        again = pattern.GetSelection()
        if (again is None or again.Length != 1
                or again.GetElement(0).CompareEndpoints(start, selected, start) != 0
                or again.GetElement(0).CompareEndpoints(end, selected, end) != 0
                or not _safe_target(automation, element, expected, uia)
                or element.GetCurrentPropertyValue(uia.UIA_ProcessIdPropertyId) != request['pid']
                or selected.GetText(2001) != text):
            return {'state': 'refused'}
        return {'state': 'ok', 'text': text}
    except Exception:
        # An unknown provider failure is not "unsupported": do not trigger
        # Ctrl+C as a fallback when a required safety check failed.
        return {'state': 'refused'}
