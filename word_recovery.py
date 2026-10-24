"""Recover a skipped prefix only from a verified plain editor, never from keys."""
import ctypes
from ctypes import wintypes

from early_native_edit import NativeEditBridge, supports_native_edit
from early_scintilla import ScintillaEarlyBridge


class ReadOnlyEditBridge(NativeEditBridge):
    """Use standard EDIT read messages; never extend the writer's capabilities."""
    def check_identity(self):
        ctx = self.context
        name = ctypes.create_unicode_buffer(128)
        self.u.GetClassNameW(ctx.focus, name, len(name))
        pid = wintypes.DWORD()
        thread = self.u.GetWindowThreadProcessId(ctx.focus, ctypes.byref(pid))
        identity = int(thread), int(pid.value)
        if (name.value.casefold() != 'edit' or not all(identity)
                or not self.u.IsWindow(ctx.top) or not self.u.IsWindow(ctx.focus)
                or (ctx.focus != ctx.top and not self.u.IsChild(ctx.top, ctx.focus))
                or self.u.GetWindowLongPtrW(ctx.focus, -16) & (0x20 | 0x800)
                or (hasattr(self, 'identity') and self.identity != identity)):
            raise ValueError('unverified-edit-field')
        self.identity = identity

    def prepare(self, *args):
        raise ValueError('read-only-prefix-reader')

    def replace(self, *args):
        raise ValueError('read-only-prefix-reader')


def recover_prefix(context, mapper, length, valid):
    if not 0 < length <= 40 or not valid():
        return None
    name = ctypes.create_unicode_buffer(128)
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    api.GetClassNameW(context.focus, name, len(name))
    if supports_native_edit(name.value):
        adapter = NativeEditBridge(context, valid)
    elif name.value.casefold() == 'edit':
        adapter = ReadOnlyEditBridge(context, valid)
    elif name.value == 'Scintilla' and context.process_name == 'notepad++.exe':
        adapter = ScintillaEarlyBridge(context)
    else:
        return None
    try:
        with adapter as bridge:
            raw, caret = bridge.snapshot()
            before = raw[:caret].decode('utf-8', 'strict')
            after = raw[caret:].decode('utf-8', 'strict')
            word = before.split()[-1] if before and not before[-1].isspace() else ''
            if len(word) != length or (after and not after[0].isspace()):
                return None
            reverse = {}
            for shift in (False, True):
                for vk in (*range(65, 91), 0xBA, 0xBB, 0xBC, 0xBD, 0xBE,
                           0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE):
                    character = mapper.char(vk, context.language, shift)
                    if len(character) == 1:
                        reverse.setdefault(character, (vk, shift))
            keys = [reverse.get(character) for character in word]
            if any(key is None for key in keys) or not valid():
                return None
            # Read back after mapping, so a delayed/changing snapshot is rejected.
            if bridge.snapshot() != (raw, caret):
                return None
            return keys
    except (OSError, ValueError, TimeoutError, UnicodeError):
        return None
