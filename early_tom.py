"""Research-only marshalled native TOM access; no remote pointer dereferencing.

AccessibleObjectFromWindow obtains a local COM proxy. This is not a bounded
transport: never use it in the production low-level hook without a timing design.
"""
import ctypes
from ctypes import wintypes as w
import uuid


class Guid(ctypes.Structure):
    _fields_ = [('raw', ctypes.c_ubyte * 16)]


class NativeUndoGroup:
    def __init__(self, hwnd):
        self.ole = ctypes.WinDLL('ole32', use_last_error=True)
        self.ole.CoInitializeEx.argtypes, self.ole.CoInitializeEx.restype = [ctypes.c_void_p, w.DWORD], ctypes.c_long
        self.initialized = self.ole.CoInitializeEx(None, 0) >= 0
        if not self.initialized:
            raise OSError('COM-apartment-unavailable')
        self.pointer = ctypes.c_void_p()
        api = ctypes.WinDLL('oleacc', use_last_error=True)
        api.AccessibleObjectFromWindow.argtypes = [w.HWND, w.DWORD, ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)]
        api.AccessibleObjectFromWindow.restype = ctypes.c_long
        iid = Guid((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('8cc497c0-a1df-11ce-8098-00aa0047be5d').bytes_le))
        hr = api.AccessibleObjectFromWindow(hwnd, 0xFFFFFFF0, ctypes.byref(iid), ctypes.byref(self.pointer))
        if hr < 0 or not self.pointer.value:
            self.close()
            raise OSError('native-TOM-unavailable')

    def call(self, slot):
        table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(table[slot])
        result = method(self.pointer)
        if result < 0:
            raise OSError('native-TOM-call')

    def begin(self):
        self.call(20)  # ITextDocument::BeginEditCollection, documented COM vtable.

    def end(self):
        self.call(21)  # ITextDocument::EndEditCollection.

    def close(self):
        if self.pointer.value:
            table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(w.ULONG, ctypes.c_void_p)(table[2])(self.pointer)
            self.pointer = ctypes.c_void_p()
        if self.initialized:
            self.ole.CoUninitialize()
            self.initialized = False
