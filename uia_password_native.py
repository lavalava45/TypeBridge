"""Isolated, persistent, read-only native UIAutomation password metadata worker.

COM is initialized only inside the spawned child. No UIA calls occur at import.
The wire format is the existing seven-field PasswordFieldGuard protocol; no
document, selection, ValuePattern.Value, or password text is ever requested.
"""
import ctypes
from ctypes import wintypes
import os
import time

TICKS_EPOCH = 621355968000000000
NATIVE_EDIT = frozenset(('edit', 'richedit20w', 'richedit50w', 'richeditd2dpt'))
WEB_PREFIXES = ('chrome_', 'chromium', 'cef', 'webview', 'mozilla', 'ieframe',
                'internet explorer_server')
WEB_PROCESSES = frozenset(('chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe',
                           'opera.exe', 'vivaldi.exe', 'msedgewebview2.exe',
                           'webview2.exe', 'cefclient.exe',
                           'cefsharp.browsersubprocess.exe'))


def native_style_state(name, style):
    return -1 if name.casefold() not in NATIVE_EDIT else int(bool(style & 0x20))


def editable_metadata(kind, enabled, focused, value, value_readonly,
                      text, text_readonly, shared, *, edit=50004,
                      document=50030, combo=50003, custom=50025, pane=50033):
    """Keep the prior browser ComboBox / Document and readonly policy."""
    if not enabled or not focused:
        return False
    standard = kind in (edit, document)
    if shared:
        if kind == combo:
            return value and not value_readonly
        return standard and ((not value_readonly) if value else text and text_readonly == 0)
    if not standard and kind not in (custom, pane, combo):
        return False
    return (not value_readonly) if value else text and text_readonly == 0


def wire_line(top, focus, state, started, reason, identity='', addressable=False):
    ticks = TICKS_EPOCH + int(started * 10_000_000)
    # Strings are fixed reason codes, never arbitrary provider text.
    return f'{top}|{focus}|{state}|{ticks}|{reason}|{identity}|{int(bool(addressable))}'


class WinScope:
    def __init__(self):
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        class Rect(ctypes.Structure):
            _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                        ('right', wintypes.LONG), ('bottom', wintypes.LONG)]
        class GUI(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD),
                        ('hwndActive', wintypes.HWND), ('hwndFocus', wintypes.HWND),
                        ('hwndCapture', wintypes.HWND), ('hwndMenuOwner', wintypes.HWND),
                        ('hwndMoveSize', wintypes.HWND), ('hwndCaret', wintypes.HWND),
                        ('rcCaret', Rect)]
        self.GUI = GUI
        self.u.GetForegroundWindow.restype = wintypes.HWND
        self.u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.u.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.u.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUI)]
        self.u.GetGUIThreadInfo.restype = wintypes.BOOL
        self.u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.u.GetClassNameW.restype = ctypes.c_int
        self.u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.k.OpenProcess.restype = wintypes.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                      wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.k.CloseHandle.argtypes = [wintypes.HANDLE]

    def scope(self):
        top = int(self.u.GetForegroundWindow() or 0)
        pid = wintypes.DWORD()
        tid = self.u.GetWindowThreadProcessId(top, ctypes.byref(pid)) if top else 0
        info = self.GUI()
        info.cbSize = ctypes.sizeof(info)
        if not tid or not self.u.GetGUIThreadInfo(tid, ctypes.byref(info)):
            return None
        return (top, int(info.hwndFocus or top))

    def cls(self, hwnd):
        out = ctypes.create_unicode_buffer(128)
        return out.value.casefold() if self.u.GetClassNameW(hwnd, out, 128) else ''

    def native_state(self, pair):
        name = self.cls(pair[1])
        if name not in NATIVE_EDIT:
            return -1
        ctypes.set_last_error(0)
        style = self.u.GetWindowLongPtrW(pair[1], -16)
        return -1 if style == 0 and ctypes.get_last_error() else native_style_state(name, style)

    def process(self, hwnd):
        pid = wintypes.DWORD()
        if not self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return None
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if not self.k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return os.path.basename(buffer.value).casefold()
        finally:
            self.k.CloseHandle(handle)

    def shared(self, pair):
        for hwnd in pair:
            if self.cls(hwnd).startswith(WEB_PREFIXES):
                return True
            name = self.process(hwnd)
            if name is None or name in WEB_PROCESSES:
                return True
        return False


class PasswordProbe:
    def __init__(self, automation, uia, win):
        self.a, self.uia, self.win = automation, uia, win
        self.walker = automation.RawViewWalker

    def prop(self, element, key):
        return element.GetCurrentPropertyValueEx(key, True)

    def same(self, one, two):
        # comtypes returns a NULL interface pointer at the UIA tree root;
        # unlike None it is a false-y object that raises on property access.
        return bool(one and two and self.a.CompareElements(one, two))

    def editable(self, element, shared):
        u = self.uia
        kind = self.prop(element, u.UIA_ControlTypePropertyId)
        enabled = self.prop(element, u.UIA_IsEnabledPropertyId)
        focused = self.prop(element, u.UIA_HasKeyboardFocusPropertyId)
        if type(kind) is not int or type(enabled) is not bool or type(focused) is not bool:
            return False
        if shared and kind not in (u.UIA_EditControlTypeId, u.UIA_DocumentControlTypeId,
                                   u.UIA_ComboBoxControlTypeId):
            return False
        value, value_readonly, text, text_readonly = False, True, False, -1
        try:
            value = element.GetCurrentPattern(u.UIA_ValuePatternId)
            if value:
                value_readonly = bool(value.QueryInterface(u.IUIAutomationValuePattern).CurrentIsReadOnly)
        except Exception:
            value = False
        if not value:
            try:
                pattern = element.GetCurrentPattern(u.UIA_TextPatternId)
                if pattern:
                    ranges = pattern.QueryInterface(u.IUIAutomationTextPattern).GetSelection()
                    if ranges and ranges.Length == 1:
                        status = ranges.GetElement(0).GetAttributeValue(u.UIA_IsReadOnlyAttributeId)
                        if type(status) is bool:
                            text_readonly = int(status)
                    text = True
            except Exception:
                text = False
        return editable_metadata(kind, enabled, focused, bool(value), value_readonly,
                                 text, text_readonly, shared)

    def query(self):
        started = time.time()
        pair = self.win.scope()
        if pair is None:
            return wire_line(0, 0, 'unknown', started, 'missing-scope')
        top, focus = pair
        try:
            native, shared = self.win.native_state(pair), self.win.shared(pair)
            if native >= 0 and not shared:
                blocked = bool(native)
                try:
                    native_element = self.a.ElementFromHandle(focus)
                    blocked |= self.prop(native_element, self.uia.UIA_IsPasswordPropertyId) is True
                except Exception:
                    pass  # Native ES_PASSWORD remains authoritative.
                if self.win.scope() != pair or self.win.native_state(pair) != native:
                    return wire_line(0, 0, 'unknown', started, 'focus-change')
                return wire_line(top, focus, 'password' if blocked else 'safe', started,
                                 'native-password' if blocked else 'native-edit-style',
                                 f'win32:{focus}', not blocked)
            element = self.a.GetFocusedElement()
            if element is None:
                return wire_line(top, focus, 'unknown', started, 'no-focused-element')
            flag = self.prop(element, self.uia.UIA_IsPasswordPropertyId)
            blocked = flag is True
            # The focused input itself must explicitly report IsPassword=False.
            # Chrome may not implement this property on unrelated ancestors
            # (including its root nodes). Still inspect *every* ancestor for
            # positive password evidence; unsupported ancestor metadata alone
            # cannot turn an explicitly unprotected, addressable leaf unsafe.
            password_property_unknown = type(flag) is not bool
            ancestor, in_scope, reached_root = element, False, False
            for _ in range(64):
                if not ancestor:
                    reached_root = True
                    break
                ancestor_password = self.prop(ancestor, self.uia.UIA_IsPasswordPropertyId)
                blocked |= ancestor_password is True
                hwnd = self.prop(ancestor, self.uia.UIA_NativeWindowHandlePropertyId)
                in_scope |= type(hwnd) is int and hwnd in pair
                ancestor = self.walker.GetParentElement(ancestor)
            if not in_scope or not reached_root or self.win.scope() != pair or not self.same(element, self.a.GetFocusedElement()):
                return wire_line(0, 0, 'unknown', started, 'focus-change')
            try:
                rid = element.GetRuntimeId()
                numbers = tuple(int(n) for n in rid) if rid is not None else ()
            except Exception:
                numbers = ()
            # Positive password evidence scoped to the still-focused element
            # must survive a provider that refuses the optional Runtime ID.
            if blocked and (not numbers or len(numbers) > 32):
                return wire_line(top, focus, 'password', started, 'password')
            if not numbers or len(numbers) > 32:
                return wire_line(top, focus, 'unknown', started, 'missing-runtime-id')
            identity = 'uia:' + ','.join(map(str, numbers))
            # A missing IsPassword property on any traversed ancestor cannot
            # certify the editor as safe, even when the leaf says False.
            # Positive password evidence must still win over unknown values.
            state = 'password' if blocked else 'unknown' if password_property_unknown else 'safe'
            addressable = state == 'safe' and self.editable(element, shared)
            return wire_line(top, focus, state, started,
                             'password' if blocked else 'password-property-unsupported' if state == 'unknown'
                             else 'checked' if addressable else 'text-target-unaddressable',
                             identity, addressable)
        except Exception:
            return wire_line(top, focus, 'unknown', started, 'provider-error')
