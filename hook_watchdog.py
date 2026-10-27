"""Independent GUI-thread watchdog for the opt-in TypeBridge debug build.

Uses the OS last-input timestamp, which represents mouse OR keyboard activity,
not verified keystrokes. The hook thread merely acknowledges a posted message.
Never records keys, scan codes, text, document titles, or input destinations.
"""

import ctypes
from ctypes import wintypes

from pipeline_probe import emit_pipeline


class LastInputInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]


def windows_last_input_tick():
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetLastInputInfo.argtypes = (ctypes.POINTER(LastInputInfo),)
    api.GetLastInputInfo.restype = wintypes.BOOL
    info = LastInputInfo(cbSize=ctypes.sizeof(LastInputInfo))
    return int(info.dwTime) if api.GetLastInputInfo(ctypes.byref(info)) else None


class HookWatchdog:
    def __init__(self, emit, *, input_tick=windows_last_input_tick,
                 raw_probe=None, control_probe=None):
        self.pipeline_debug = True
        self.emit = emit
        self.input_tick = input_tick
        self.previous_tick = None
        self.raw_probe = raw_probe
        self.control_probe = control_probe
        self._last_raw_events = None
        self._last_keyboard_callbacks = None
        self._last_control_callbacks = None

    def poll(self, hook, worker):
        """Run on Tk's GUI thread, independent of the hook event queue."""
        tick = self.input_tick()
        if tick is None:
            input_changed = 'unknown'
        elif self.previous_tick is None:
            input_changed = 'baseline'
        else:
            input_changed = 'yes' if tick != self.previous_tick else 'no'
        self.previous_tick = tick

        alive = bool(worker is not None and worker.is_alive())
        installed = bool(hook is not None and hook.keyboard_hook)
        thread = bool(hook is not None and hook.thread_id)
        mouse = bool(hook is not None and hook.mouse_hook)
        callbacks = int(getattr(hook, 'debug_keyboard_callbacks', 0))
        ll_delta = (max(0, callbacks - self._last_keyboard_callbacks)
                    if self._last_keyboard_callbacks is not None else 0)
        self._last_keyboard_callbacks = callbacks
        keydowns = int(getattr(hook, 'debug_keydowns', 0))
        mice = int(getattr(hook, 'debug_mouse_callbacks', 0))
        pongs = int(getattr(hook, 'debug_probe_pongs', 0))
        errors = int(getattr(hook, 'debug_callback_errors', 0))
        slow = int(getattr(hook, 'debug_keyboard_slow', 0))
        max_ms = int(getattr(hook, 'debug_keyboard_max_ms', 0))
        ping = 'not-sent'
        if thread and installed:
            try:
                ping = 'posted' if hook.request_probe_ping() else 'post-failed'
            except Exception:
                ping = 'post-error'
        emit_pipeline(self, 'watchdog', alive=alive, thread=thread,
                      keyboard=installed, mouse=mouse, callbacks=callbacks,
                      keydowns=keydowns, mice=mice, pongs=pongs, errors=errors,
                      ping=ping, wininput=input_changed, slow=slow, max_ms=max_ms,
                      ll_delta=ll_delta,
                      **self._raw_fields(), **self._control_fields())

    def _raw_fields(self):
        if self.raw_probe is None:
            return {}
        self.raw_probe.verify_registration()
        info = self.raw_probe.snapshot()
        raw_delta = (max(0, info['events'] - self._last_raw_events)
                     if self._last_raw_events is not None else 0)
        self._last_raw_events = info['events']
        return dict(raw=info['status'], raw_events=info['events'],
                    raw_errors=info['errors'], raw_error=info['error'],
                    raw_alive=info['alive'], raw_delta=raw_delta,
                    raw_wm=info['wm_input'], raw_pump=info['pump'],
                    raw_pump_input=info['pump_input'], raw_reg=info['reg'],
                    raw_reg_error=info['reg_error'], raw_flags=info['reg_flags'])

    def _control_fields(self):
        if self.control_probe is None:
            return {}
        info = self.control_probe.snapshot()
        count = info['callbacks']
        delta = (max(0, count - self._last_control_callbacks)
                 if self._last_control_callbacks is not None else 0)
        self._last_control_callbacks = count
        if info['status'] == 'registered':
            ping = 'posted' if self.control_probe.ping() else 'failed'
        else:
            ping = 'not-ready'
        return dict(control=info['status'], control_error=info['error'],
                    control_alive=info['alive'], control_callbacks=count,
                    control_delta=delta, control_keydowns=info['keydowns'],
                    control_ping=ping, control_pongs=info['pongs'])
