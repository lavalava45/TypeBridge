"""Debug-only, metadata-only traces for diagnosing real keyboard delivery.

Never records physical keys, word contents, window titles or document text.
Called on the hook thread: formatting and queueing only, no file writes.
"""

import re
from time import monotonic


EVENTS = frozenset({
    'hook-installed', 'hook-keydown', 'context-rejected', 'context-ready',
    'token-start', 'token-reset', 'token-blocked', 'delimiter',
    'decision-none', 'decision-found', 'transport', 'layout-result',
    'early-found', 'settings', 'input-filtered', 'watchdog', 'callback-error',
})
FIELDS = frozenset({'reason', 'app', 'language', 'target', 'hkl', 'observed',
                    'length', 'separator', 'blocked', 'paused', 'early',
                    'rare', 'manual', 'result', 'method', 'enabled', 'native', 'words',
                    'thread', 'keyboard', 'mouse', 'alive', 'ping', 'pongs',
                    'callbacks', 'keydowns', 'mice', 'errors', 'wininput'})
FIELDS = FIELDS | frozenset({'raw', 'raw_events', 'raw_errors', 'raw_error',
                             'raw_alive', 'slow', 'max_ms', 'raw_delta', 'll_delta'})
FIELDS = FIELDS | frozenset({'mouse_max_us', 'mouse_slow'})
FIELDS = FIELDS | frozenset({
    'raw_wm', 'raw_pump', 'raw_pump_input', 'raw_reg', 'raw_reg_error',
    'raw_flags', 'control', 'control_error', 'control_alive',
    'control_callbacks', 'control_delta', 'control_keydowns',
    'control_ping', 'control_pongs',
})
SAFE_VALUE = re.compile(r'^[a-zA-Z0-9_.+\-]{1,64}$')


def emit_pipeline(hook, event, *, interval=0, **fields):
    """Emit a validated event; repeated context/heartbeat probes are throttled."""
    if not getattr(hook, 'pipeline_debug', False) or event not in EVENTS:
        return
    now = monotonic()
    if interval:
        previous = getattr(hook, '_pipeline_last', None)
        if previous is None:
            previous = {}
            hook._pipeline_last = previous
        # The reason is deliberately part of the key: report distinct failures.
        key = (event, fields.get('reason', ''))
        if now - previous.get(key, -1e30) < interval:
            return
        previous[key] = now
    safe = []
    for name, value in fields.items():
        if name not in FIELDS:
            continue
        if isinstance(value, bool):
            value = 'yes' if value else 'no'
        else:
            value = str(value)
        if not SAFE_VALUE.fullmatch(value):
            value = 'other'
        safe.append(f'{name}={value}')
    hook.emit('probe', 'pipeline:' + event + (':' + ':'.join(safe) if safe else ''))


def decision_failure(engine, keys, current, *, allow_rare=False):
    """Conservative diagnostic classification. Does not influence a decision."""
    if not keys:
        return 'empty-token'
    if current not in engine.words or len(keys) > 40:
        return 'unsupported-context'
    typed = engine.mapper.word(keys, current)
    if len(typed) != len(keys):
        return 'source-mapping-incomplete'
    if engine._excluded(current, typed):
        return 'source-excluded'
    if not allow_rare and engine._known(current, typed):
        return 'already-valid-source-word'
    matches = []
    for lang in engine.words:
        if lang == current:
            continue
        word = engine.mapper.word(keys, lang)
        if len(word) == len(keys) and engine._known(lang, word):
            matches.append((lang, word))
    if not matches:
        return 'no-target-dictionary-match'
    if len(matches) > 1:
        return 'multiple-targets-policy'
    lang, word = matches[0]
    if engine._excluded(lang, word):
        return 'target-excluded'
    if engine.conservative and not allow_rare and len(keys) > 1 and not engine._confident(lang, word):
        return 'rare-word-policy'
    return 'mapping-or-punctuation-policy'
