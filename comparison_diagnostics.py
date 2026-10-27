"""Metadata-only comparison. Heavy inspection runs off the keyboard hook."""
import ctypes
from ctypes import wintypes
from functools import lru_cache
import json
import os
from pathlib import Path


def capture_context(api, context, target):
    pid, host = wintypes.DWORD(), wintypes.DWORD()
    ft = int(api.GetWindowThreadProcessId(context.focus, ctypes.byref(pid)))
    tt = int(api.GetWindowThreadProcessId(context.top, ctypes.byref(host)))
    return dict(pid=int(pid.value), host_pid=int(host.value), top=int(context.top), focus=int(context.focus),
                focus_thread=ft, top_thread=tt, layout_thread=int(context.thread_id),
                source=context.language, target=target,
                focus_hkl=hex(int(api.GetKeyboardLayout(ft) or 0) & 0xFFFFFFFF),
                top_hkl=hex(int(api.GetKeyboardLayout(tt) or 0) & 0xFFFFFFFF),
                style=hex(int(api.GetWindowLongPtrW(context.focus, -16)) & 0xFFFFFFFF))


def version(path):
    import win32api
    try:
        v = win32api.GetFileVersionInfo(str(path), '\\')
        a, b = v['FileVersionMS'], v['FileVersionLS']
        return '.'.join(str(n) for n in (a >> 16, a & 65535, b >> 16, b & 65535))
    except Exception as error:
        return type(error).__name__


def integrity(pid):
    import win32api
    import win32security
    process = token = None
    try:
        process = win32api.OpenProcess(0x1000, False, pid)
        token = win32security.OpenProcessToken(process, 8)
        sid, _ = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
        return dict(integrity_rid=sid.GetSubAuthority(sid.GetSubAuthorityCount()-1),
                    elevated=bool(win32security.GetTokenInformation(token, win32security.TokenElevation)))
    except Exception as error:
        return dict(error=type(error).__name__)
    finally:
        if token is not None: token.Close()
        if process is not None: process.Close()


@lru_cache(maxsize=32)
def process_metadata(pid, created):
    import psutil
    import win32api
    import win32process
    p = psutil.Process(pid)
    if p.create_time() != created:
        return dict(pid=pid, error='process-identity-changed')
    path = p.exe()
    result = dict(pid=pid, image=path, version=version(path), rights=integrity(pid), rich_edit=[])
    handle = None
    try:
        handle = win32api.OpenProcess(0x410, False, pid)
        for module in win32process.EnumProcessModules(handle):
            name = win32process.GetModuleFileNameEx(handle, module)
            if Path(name).name.casefold() in ('msftedit.dll', 'riched20.dll'):
                result['rich_edit'].append(dict(image=name, version=version(name)))
    except Exception as error:
        result['module_error'] = type(error).__name__
    finally:
        if handle is not None: handle.Close()
    return result


def context_report(context):
    import psutil
    report = dict(context, typebridge_rights=integrity(os.getpid()))
    for key, pid in [('field_process', context['pid']), ('host_process', context['host_pid'])]:
        try:
            report[key] = process_metadata(pid, psutil.Process(pid).create_time())
        except Exception as error:
            report[key] = dict(pid=pid, error=type(error).__name__)
    return 'comparison-context:' + json.dumps(report, ensure_ascii=False, separators=(',', ':'))
