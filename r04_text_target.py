"""Release-oriented, synchronous fail-closed policy for text mutations.

An HTML input in a browser/WebView can switch to a password input without a
native HWND or UIA notification change. No asynchronous metadata snapshot can
atomically bind SendInput to that DOM element. Do not inspect or log field text:
deny *all* browser and unverified custom controls at the context boundary.

Plain typing is handled by Windows, outside this policy. An opt-in UIA guard
may impose *additional* restrictions but cannot relax these defaults.
"""

NATIVE_TEXT_CONTROLS = frozenset({
    'edit', 'richedit20w', 'richedit50w', 'richeditd2dpt',
})

BROWSER_HOSTS = frozenset({
    'chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe',
    'opera.exe', 'vivaldi.exe', 'msedgewebview2.exe',
    'webview2.exe', 'cefclient.exe', 'cefsharp.browsersubprocess.exe',
})

WEBVIEW_WINDOW_PREFIXES = (
    'chrome_', 'chromium', 'cef', 'webview', 'mozilla', 'ieframe',
    'internet explorer_server',
)


def trusted_mutation_target(process: str, host_process: str, control_class: str,
                            top_class: str = '') -> bool:
    """Admit only directly identifiable native text controls.

    Scintilla is allowed only in Notepad++, whose transaction protocol is
    independently verified; custom/Electron/HTML/unknown controls are denied.
    This is intentionally stricter than legacy browser layout correction.
    """
    process = (process or '').casefold()
    host_process = (host_process or '').casefold()
    control_class = (control_class or '').casefold()
    top_class = (top_class or '').casefold()
    if not process or not host_process or process in BROWSER_HOSTS or host_process in BROWSER_HOSTS:
        return False
    # WebViews can be embedded under an otherwise unrelated application EXE.
    # A child Win32 EDIT class alone cannot authorize edits when its host is a
    # renderer window; refuse such frames even when the process name is novel.
    if any(name.startswith(WEBVIEW_WINDOW_PREFIXES)
           for name in (top_class, control_class)):
        return False
    if control_class in NATIVE_TEXT_CONTROLS:
        return True
    return control_class == 'scintilla' and process == host_process == 'notepad++.exe'
