"""Temporary, explicitly opt-in word diagnostics for disposable test documents.

Do not enable in normal use. Record short tokens and caret-adjacent excerpts only;
never persist a whole document or clipboard. Called from the GUI queue drain.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

from diagnostic_identity import computer_id


BROWSER_APPS = frozenset({'chrome.exe', 'msedge.exe', 'firefox.exe',
                          'brave.exe', 'opera.exe', 'vivaldi.exe'})
TRACE_APPS = BROWSER_APPS | frozenset({'notepad.exe', 'notepad++.exe'})

EVENTS = frozenset({'typed-character', 'edit-action', 'browser-readback',
                    'early-decision', 'early-result', 'boundary-decision',
                    'boundary-result', 'post-early-at-boundary',
                    'post-early-deferred', 'manual-cycle-result'})
WORDS = frozenset({'source', 'ru', 'en', 'he', 'replacement', 'observed'})
FIELDS = WORDS | {'event', 'app', 'language', 'target', 'state', 'reason',
                  'transport', 'sent', 'total', 'removed', 'tracked',
                  'delay_ms', 'read_ms', 'phase'}


def safe_word_event(details):
    """JSON-escape and bound content; never include arbitrary extra fields."""
    if not isinstance(details, dict) or details.get('event') not in EVENTS:
        return None
    safe = {'event': details['event']}
    for key in FIELDS - {'event'}:
        val = details.get(key)
        if not isinstance(val, str):
            continue
        maxlen = 60 if key in WORDS else 64
        # Preserve actual caret-span whitespace as JSON escapes. Dropping it
        # could make two document lines look like a duplicated/merged word.
        cleaned = ''.join(c for c in val if (ord(c) >= 32 and ord(c) != 127)
                          or (key == 'observed' and c in '\r\n\t'))
        safe[key] = cleaned[:maxlen]
    return json.dumps(safe, ensure_ascii=False, sort_keys=True)


def record_word_event(folder, details):
    encoded = safe_word_event(details)
    if encoded is None:
        return
    try:
        path = Path(folder) / 'debug-words.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1_048_576:
            path.replace(path.with_suffix('.previous.log'))
        with path.open('a', encoding='utf-8') as stream:
            stream.write(f'{datetime.now(timezone.utc).isoformat()} '
                         f'[pc={computer_id()}] word-trace:{encoded}\n')
    except OSError:
        pass
