"""One live keyboard hook process at a time, enforced by a Windows mutex."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes


# Keep the legacy mutex stable: old and renamed applications must not hook together.
MUTEX_NAME = "Local\\KeyboardLayoutPrototype-LiveHook-v1"
ERROR_ALREADY_EXISTS = 183


@contextmanager
def exclusive_live_hook():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, True, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    existed = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    if existed:
        kernel.CloseHandle(handle)
        raise RuntimeError("Another TypeBridge or compatible legacy instance is already running")
    try:
        yield
    finally:
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)
