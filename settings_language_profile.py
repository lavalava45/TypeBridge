"""R16: reversible language-selection sidecar, independent of Windows APIs.

Old TypeBridge understands only the built-in codes in settings.json. Store the
full selected set and optional package shortcuts separately; do not put any
external language into legacy settings.json or touch installed language packs.
"""

import json
import os
import base64
import hashlib
from pathlib import Path
import stat
import tempfile
import threading

from language_registry import DEFAULT_ACTIVE_LANGUAGES, LANGUAGE_BY_CODE, validate_active_languages


SIDECAR_NAME = 'settings_languages.json'
SIDECAR_LIMIT = 64 * 1024
TRANSACTION_NAME = 'settings_pair_transaction.json'
LEGACY_LIMIT = 512 * 1024
TRANSACTION_LIMIT = 2 * (LEGACY_LIMIT + SIDECAR_LIMIT) + 8192
_PAIR_LOCK = threading.RLock()


def _strict_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('Duplicate language-profile property')
        obj[key] = value
    return obj


def _safe_target(path):
    if (path.is_symlink() or getattr(path, 'is_junction', lambda: False)()
            or (path.exists() and not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode))):
        raise ValueError('Invalid language-profile sidecar path')
    for parent in path.parents:
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            raise ValueError('Language-profile sidecar under redirected directory')


def sidecar_path(settings_path):
    return Path(settings_path).parent / SIDECAR_NAME


def _bounded_bytes(path, limit):
    _safe_target(path)
    if not path.exists():
        return None
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('Settings file exceeds the safety limit')
    return value


def _atomic_write(path, data):
    _safe_target(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                         prefix='.settings-pair-', suffix='.tmp',
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _safe_target(path)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sha(value):
    return hashlib.sha256(value).hexdigest() if value is not None else None


def _encoded(value):
    return base64.b64encode(value).decode('ascii') if value is not None else None


def _decoded(value, limit):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2 * limit:
        raise ValueError('Invalid settings transaction backup')
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError('Invalid settings transaction backup') from error
    if len(result) > limit:
        raise ValueError('Oversized settings transaction backup')
    return result


def recover_pending_pair(settings_path):
    """Recover a pre-commit pair after interrupted two-file replacement.

    Never overwrite a file whose content is neither the old nor the intended
    new snapshot (e.g. it was edited externally after the crash). A complete
    committed pair is preserved. A half-commit reverts to BOTH old files.
    """
    with _PAIR_LOCK:
        path = Path(settings_path)
        marker = path.parent / TRANSACTION_NAME
        raw = _bounded_bytes(marker, TRANSACTION_LIMIT)
        if raw is None:
            return False
        try:
            document = json.loads(raw.decode('utf-8'), object_pairs_hook=_strict_object)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError('Invalid pending settings transaction') from error
        if (not isinstance(document, dict) or set(document) != {'version', 'files'}
                or type(document['version']) is not int or document['version'] != 1
                or not isinstance(document['files'], list)
                or len(document['files']) != 2):
            raise ValueError('Invalid pending settings transaction')
        entries = []
        for item, filename, limit in zip(document['files'],
                                         (path, sidecar_path(path)),
                                         (LEGACY_LIMIT, SIDECAR_LIMIT)):
            if not isinstance(item, dict) or set(item) != {'old', 'old_sha', 'new_sha'}:
                raise ValueError('Invalid pending settings transaction')
            old = _decoded(item['old'], limit)
            if item['old_sha'] != _sha(old) or not isinstance(item['new_sha'], str):
                raise ValueError('Settings transaction integrity error')
            current = _bounded_bytes(filename, limit)
            if _sha(current) not in (item['old_sha'], item['new_sha']):
                raise ValueError('Settings were edited after interrupted transaction')
            entries.append((filename, old, current, item['new_sha']))
        if not all(_sha(current) == new_hash for _, _, current, new_hash in entries):
            for filename, old, current, _ in entries:
                if current == old:
                    continue
                if old is None:
                    filename.unlink()
                else:
                    _atomic_write(filename, old)
        marker.unlink()
        return True


def save_settings_pair(settings_path, legacy_bytes, selected, extra, catalog, *, shortcuts=None):
    """Journalled commit of compatible legacy settings plus language sidecar."""
    with _PAIR_LOCK:
        path = Path(settings_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        recover_pending_pair(path)
        next_sidecar = language_selection_bytes(selected, extra, catalog,
                                                shortcuts=shortcuts)
        if len(legacy_bytes) > LEGACY_LIMIT:
            raise ValueError('Settings file exceeds the safety limit')
        filenames = (path, sidecar_path(path))
        old = (_bounded_bytes(path, LEGACY_LIMIT),
               _bounded_bytes(filenames[1], SIDECAR_LIMIT))
        content = (legacy_bytes, next_sidecar)
        journal = {'version': 1, 'files': [
            {'old': _encoded(previous), 'old_sha': _sha(previous),
             'new_sha': _sha(updated)}
            for previous, updated in zip(old, content)]}
        marker = path.parent / TRANSACTION_NAME
        if marker.exists() or marker.is_symlink():
            raise ValueError('Unresolved settings transaction')
        raw = (json.dumps(journal, ensure_ascii=True, separators=(',', ':')) + '\n').encode('utf-8')
        if len(raw) > TRANSACTION_LIMIT:
            raise ValueError('Settings transaction exceeds the safety limit')
        _atomic_write(marker, raw)
        try:
            _atomic_write(path, legacy_bytes)
            _atomic_write(filenames[1], next_sidecar)
        except OSError:
            # If rollback itself fails, keep the durable journal so the next
            # catalog-aware load can recover or refuse a mixed profile.
            recover_pending_pair(path)
            raise
        marker.unlink()


def read_language_selection(settings_path, catalog):
    """Return (codes, extra hotkeys) or None. Missing packs fail explicitly."""
    profile = read_language_profile(settings_path, catalog)
    return (profile[0], profile[1]) if profile is not None else None


def read_language_profile(settings_path, catalog):
    """Return (active, external legacy bindings, full universal bindings)."""
    path = sidecar_path(settings_path)
    _safe_target(path)
    if not path.exists():
        return None
    if path.stat().st_size > SIDECAR_LIMIT:
        raise ValueError('Language settings sidecar too large')
    with path.open('rb') as handle:
        data = handle.read(SIDECAR_LIMIT + 1)
    try:
        doc = json.loads(data.decode('utf-8'), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid language settings sidecar JSON') from error
    if not isinstance(doc, dict) or type(doc.get('version')) is not int:
        raise ValueError('Unsupported language settings sidecar schema')
    version = doc['version']
    expected = ({'version', 'active_languages', 'direct_extra'} if version == 1
                else {'version', 'active_languages', 'shortcuts'} if version == 2
                else set())
    if set(doc) != expected:
        raise ValueError('Unsupported language settings sidecar schema')
    codes = validate_active_languages(doc['active_languages'], catalog)
    if version == 2:
        shortcuts = doc['shortcuts']
        if (not isinstance(shortcuts, list) or
                any(not isinstance(pair, list) or len(pair) != 2 or
                    not all(isinstance(value, str) for value in pair)
                    for pair in shortcuts)):
            raise ValueError('Invalid universal language shortcuts')
        pairs = tuple((code, chord) for code, chord in shortcuts)
        from language_pack_loader import _CODE
        if (len({code for code, _ in pairs}) != len(pairs)
                or any(not _CODE.fullmatch(code) for code, _ in pairs)):
            raise ValueError('Duplicate or unsafe stored language shortcut')
        # Unavailable *inactive* packages remain dormant in the profile;
        # explicitly selected missing packs fail via validate above.
        extras = tuple((code, chord) for code, chord in pairs
                       if code not in LANGUAGE_BY_CODE
                       and code in catalog
                       and chord.strip().casefold() != 'отключено')
        return codes, extras, pairs
    extra = doc['direct_extra']
    if (not isinstance(extra, list) or
            any(not isinstance(row, list) or len(row) != 2 or
                any(not isinstance(part, str) for part in row) for row in extra)):
        raise ValueError('Invalid language settings hotkeys')
    pairs = tuple((code, chord) for code, chord in extra)
    from language_pack_loader import _CODE
    if (len(set(code for code, _ in pairs)) != len(pairs)
            or any(code in LANGUAGE_BY_CODE or not _CODE.fullmatch(code)
                   for code, _ in pairs)):
        raise ValueError('Language settings contain unknown or duplicate hotkeys')
    if any(code not in catalog for code, _ in pairs):
        # Old v1 profiles sometimes contained shortcuts for temporarily
        # missing, disabled packages. Keep them dormant in the new mapping;
        # never silently delete them, nor allow them to intercept an input.
        usable = tuple((code, chord) for code, chord in pairs if code in catalog)
        return codes, usable, pairs
    return codes, pairs, ()


def legacy_selection(selected):
    """Valid fallback if a previous EXE opens settings.json."""
    builtins = tuple(code for code in selected if code in LANGUAGE_BY_CODE)
    return builtins or DEFAULT_ACTIVE_LANGUAGES


def language_selection_bytes(selected, extra, catalog, *, shortcuts=None):
    """Encode a verified sidecar without changing either settings file."""
    selected = validate_active_languages(selected, catalog)
    extra = tuple(extra)
    # Caller validates hotkey conflicts with the actual Settings object;
    # here we check only the detached payload shape and identity.
    if (any(not isinstance(pair, (tuple, list)) or len(pair) != 2 or
            not all(isinstance(item, str) for item in pair) for pair in extra)
            or len({code for code, _ in extra}) != len(extra)
            or any((code not in catalog and shortcuts is None)
                   or code in LANGUAGE_BY_CODE for code, _ in extra)):
        raise ValueError('Invalid package language shortcuts')
    if shortcuts is not None:
        from language_pack_loader import _CODE
        shortcuts = tuple(shortcuts)
        if (any(not isinstance(pair, (tuple, list)) or len(pair) != 2
                or not all(isinstance(item, str) for item in pair)
                for pair in shortcuts)
                or len({code for code, _ in shortcuts}) != len(shortcuts)
                or any(not _CODE.fullmatch(code) for code, _ in shortcuts)):
            raise ValueError('Invalid universal language shortcut mapping')
        document = {'version': 2, 'active_languages': list(selected),
                    'shortcuts': [list(row) for row in shortcuts]}
    else:
        document = {'version': 1, 'active_languages': list(selected),
                    'direct_extra': [list(row) for row in extra]}
    data = (json.dumps(document,
                       ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    if len(data) > SIDECAR_LIMIT:
        raise ValueError('Language settings sidecar too large')
    return data


def write_language_selection(settings_path, selected, extra, catalog):
    """Atomically publish a fully validated snapshot; never delete old one."""
    data = language_selection_bytes(selected, extra, catalog)
    path = sidecar_path(settings_path)
    _safe_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                         prefix='.settings-language-', suffix='.tmp',
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _safe_target(path)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
