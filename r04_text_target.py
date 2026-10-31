"""Select field metadata, never deny an application by its name.

Native editors expose ES_PASSWORD synchronously. Shared browser/WebView and
custom controls require focused-element UIA metadata, not an editor whitelist.
"""

NATIVE_TEXT_CONTROLS = frozenset({'edit', 'richedit20w', 'richedit50w', 'richeditd2dpt'})
BROWSER_HOSTS = frozenset({
    'chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe', 'opera.exe',
    'vivaldi.exe', 'msedgewebview2.exe', 'webview2.exe', 'cefclient.exe',
    'cefsharp.browsersubprocess.exe',
})
WEBVIEW_WINDOW_PREFIXES = (
    'chrome_', 'chromium', 'cef', 'webview', 'mozilla', 'ieframe',
    'internet explorer_server',
)


def native_field_metadata(process, host_process, control_class, top_class=''):
    """Can Win32 identify this native editor without DOM metadata?"""
    process, host_process = (process or '').casefold(), (host_process or '').casefold()
    control_class, top_class = (control_class or '').casefold(), (top_class or '').casefold()
    if (process in BROWSER_HOSTS or host_process in BROWSER_HOSTS
            or any(name.startswith(WEBVIEW_WINDOW_PREFIXES)
                   for name in (control_class, top_class))):
        return False  # Require UIA element identity, not a browser-wide refusal.
    return (control_class in NATIVE_TEXT_CONTROLS or
            control_class == 'scintilla' and process == host_process == 'notepad++.exe')
