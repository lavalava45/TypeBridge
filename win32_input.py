"""Shared Win64 SendInput ABI. Importing this module has no runtime side effects."""
import ctypes
from ctypes import wintypes

OWN_INPUT_MARKER = 0x4C53574954434831


class KeyInput(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t))


class InputUnion(ctypes.Union):
    # Win64 INPUT is 40 bytes because MOUSEINPUT makes the union 32 bytes.
    _fields_ = (("ki", KeyInput), ("reserved", ctypes.c_ubyte * 32))


class Input(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("u", InputUnion))


