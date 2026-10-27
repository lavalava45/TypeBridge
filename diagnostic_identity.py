"""Stable pseudonymous machine label; never expose the underlying identifier."""
from functools import lru_cache
import hashlib
import socket
import winreg


@lru_cache(maxsize=1)
def computer_id():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, _ = winreg.QueryValueEx(key, 'MachineGuid')
        if not isinstance(value, str) or not value.strip():
            raise ValueError('missing machine identity')
        identity = 'guid:' + value.strip().casefold()
    except (OSError, ValueError):
        identity = 'hostname:' + socket.gethostname().casefold()
    digest = hashlib.sha256(('TypeBridge/computer-id/v1\0' + identity).encode('utf-8')).hexdigest()
    return 'TB-' + digest[:12].upper()
