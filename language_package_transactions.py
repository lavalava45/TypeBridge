"""Durable, serialized filesystem transactions for offline language packages.

Only package directories are affected. Recovery is conservative: ambiguous or
tampered journals fail closed rather than deleting user data. The SHA-256 journal
checksum detects accidental damage; it is not an authenticity signature or
defense against an attacker able to rewrite both the journal and checksum.

R16/4e journal v1 lacked root binding and checksum. Its path-bearing contents
cannot be authenticated, so automatic replay refuses it without deleting any
files. Close TypeBridge, preserve the entire transaction and history folders,
and manually inspect the journal, archive, and installed pack before restoring
anything. An interrupted R16/4e update can leave the previous pack in history.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid


def _foreign_typebridge_running():
    """A different process might hold a hook with a different launch set."""
    import psutil
    own_pid = os.getpid()
    return any(proc.info['pid'] != own_pid and
               (proc.info.get('name') or '').lower() == 'typebridge.exe'
               for proc in psutil.process_iter(['pid', 'name']))


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def package_lock(root):
    """Interprocess lock spanning inspection, renames, and journal replay."""
    root = Path(root).resolve(strict=True)
    storage = root.parent / 'language_pack_transactions'
    storage.mkdir(exist_ok=True)
    if storage.is_symlink() or not storage.is_dir():
        raise ValueError('Unsafe transaction directory')
    lock_file = storage / '.lock'
    with lock_file.open('a+b') as stream:
        stream.seek(0)
        if stream.read(1) == b'':
            stream.seek(0)
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            import time
            deadline = time.monotonic() + 10
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Language package transaction lock busy')
                    time.sleep(.05)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield storage
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def fingerprint(folder):
    """Hash the package contents and names, rejecting nonregular entries."""
    from language_pack_loader import _regular_path
    folder = Path(folder)
    if not _regular_path(folder) or not folder.is_dir():
        raise ValueError('Unsafe transaction package directory')
    result = {}
    for path in folder.iterdir():
        if not _regular_path(path) or not path.is_file():
            raise ValueError('Unsafe transaction package member')
        digest = hashlib.sha256()
        with path.open('rb') as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        result[path.name] = digest.hexdigest()
    return result


def _write(record_path, data):
    data['checksum'] = hashlib.sha256(json.dumps(
        {key: value for key, value in data.items() if key != 'checksum'},
        sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    tmp = record_path.with_suffix('.tmp')
    with tmp.open('x', encoding='utf-8') as output:
        json.dump(data, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(tmp, record_path)
    _sync_directory(record_path.parent)


def start(storage, root, code, operation, *, original=None, replacement=None,
          backup=None):
    location = storage / uuid.uuid4().hex
    location.mkdir()
    data = dict(version=1, root=str(Path(root).resolve(strict=True)),
                code=code, operation=operation,
                original=original, replacement=replacement,
                backup=str(backup) if backup else None, stage='prepared')
    _write(location / 'journal.json', data)
    return location, data


def mark(location, data, stage):
    data['stage'] = stage
    _write(location / 'journal.json', data)


def finish(location, data):
    mark(location, data, 'complete')
    shutil.rmtree(location)
    _sync_directory(location.parent)


def _verify(path, expected):
    if fingerprint(path) != expected:
        raise ValueError(f'Package transaction integrity failure: {path}')


def replay_locked(root, storage, *, running_languages=None, check_foreign=True):
    """Complete published operations or roll back interrupted update swaps.

    Called with package_lock already acquired, before catalog consumption.
    """
    from language_pack_loader import _CODE, _regular_path
    root = Path(root).resolve(strict=True)
    history = (root.parent / 'language_pack_history').resolve()
    for location in sorted(storage.iterdir()):
        if location.name == '.lock':
            continue
        if location.is_symlink() or not location.is_dir():
            raise ValueError('Unsafe transaction entry')
        journal = location / 'journal.json'
        if not any(location.iterdir()):
            # Crash after mkdir and before the first journal write.
            location.rmdir()
            continue
        if (not journal.exists() and
                {entry.name for entry in location.iterdir()} == {'journal.tmp'} and
                (location / 'journal.tmp').is_file() and
                not (location / 'journal.tmp').is_symlink()):
            # The first journal write did not finish. No filesystem rename
            # can precede start() returning, so this is an unused workspace.
            shutil.rmtree(location)
            continue
        if journal.is_symlink() or not journal.is_file():
            raise ValueError('Incomplete package transaction journal')
        data = json.loads(journal.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Invalid package transaction journal')
        if (data.get('version') == 1 and 'checksum' not in data
                and 'root' not in data):
            raise RuntimeError(
                'Legacy R16/4e package journal requires manual recovery: '
                'close TypeBridge, back up language_pack_transactions and '
                'language_pack_history, inspect the journal and package hashes; '
                'automatic replay will not modify or delete these files')
        checksum = hashlib.sha256(json.dumps(
            {key: value for key, value in data.items() if key != 'checksum'},
            sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        if data.get('checksum') != checksum:
            raise ValueError('Package transaction journal checksum mismatch')
        code = data.get('code')
        operation = data.get('operation')
        if (data.get('version') != 1 or not isinstance(code, str)
                or not _CODE.fullmatch(code)
                or operation not in ('install', 'update', 'remove', 'restore')):
            raise ValueError('Invalid package transaction journal')
        if data.get('root') != str(root):
            raise ValueError('Package journal belongs to another installation root')
        if check_foreign and _foreign_typebridge_running():
            raise RuntimeError('Recovery blocked: another TypeBridge instance may hold a running hook')
        if running_languages is not None and code in running_languages:
            raise RuntimeError('Recovery blocked: package is used by running hook')
        destination = root / code
        if destination.is_symlink() or getattr(destination, 'is_junction', lambda: False)():
            raise ValueError('Unsafe transaction destination')
        backup_value = data.get('backup')
        backup_source = Path(backup_value) if isinstance(backup_value, str) else None
        if backup_source is not None and (
                not backup_source.is_absolute() or not _regular_path(backup_source)
                or not _regular_path(backup_source.parent)):
            raise ValueError('Unsafe package transaction history path')
        backup = backup_source.resolve() if backup_source is not None else None
        if operation != 'install' and (
                backup is None or backup.parent.parent != history or backup.name != code
                or not backup.parent.name.startswith(f'{code}-')):
            raise ValueError('Unsafe package transaction history path')
        old = data.get('original')
        new = data.get('replacement')
        allowed_children = {'journal.json', 'journal.tmp'}
        if operation == 'update':
            allowed_children.add('typebridge-update-new')
        if {part.name for part in location.iterdir()} - allowed_children:
            raise ValueError('Unknown content in transaction directory')
        if any(part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
               for part in location.iterdir()):
            raise ValueError('Unsafe transaction directory entry')
        staged_parent = location / 'typebridge-update-new'
        if staged_parent.exists():
            if (not _regular_path(staged_parent) or not staged_parent.is_dir()
                    or any(item.name != code or not _regular_path(item)
                           or not item.is_dir() for item in staged_parent.iterdir())):
                raise ValueError('Unsafe update staging directory')
        if operation == 'install':
            if destination.exists():
                _verify(destination, new)
            # Absent destination means commit rename never occurred.
        elif operation == 'update':
            staged = location / 'typebridge-update-new' / code
            if backup.exists():
                _verify(backup, old)
                if destination.exists():
                    _verify(destination, new)
                else:
                    # Crash after archiving old bytes but before publishing new.
                    os.rename(backup, destination)
                    _sync_directory(root)
            elif destination.exists():
                _verify(destination, old)
            else:
                raise ValueError('Update lost both original and archived package')
            if staged.exists() and backup.exists():
                _verify(staged, new)
        elif operation == 'remove':
            if backup.exists() and not destination.exists():
                _verify(backup, old)
            elif destination.exists() and not backup.exists():
                _verify(destination, old)
            else:
                raise ValueError('Ambiguous remove transaction state')
        else:
            if destination.exists() and not backup.exists():
                _verify(destination, old)
            elif backup.exists() and not destination.exists():
                _verify(backup, old)
            else:
                raise ValueError('Ambiguous restore transaction state')
        shutil.rmtree(location)
        _sync_directory(storage)


def recover_package_transactions(root, settings_path=None, *, running_languages=None):
    """Replay interrupted package operations before reading installed catalog.

    An update rollback may move an old package back. Recovery refuses to
    mutate a package used by this hook or when a different TypeBridge process
    might have a hook. settings_path is accepted for startup API compatibility;
    existing transaction fingerprints, not settings, determine safe replay.
    Directory fsync is available on POSIX. Windows guarantees here concern
    process crashes; sudden power loss additionally depends on filesystem.
    """
    root = Path(root)
    storage = root.parent / 'language_pack_transactions'
    if storage.is_symlink():
        raise ValueError('Unsafe transaction directory')
    if not storage.exists():
        # A clean first startup has nothing to replay. In particular, it must
        # not create a lock file in the user's otherwise untouched profile.
        return
    with package_lock(root) as storage:
        replay_locked(root, storage, running_languages=running_languages)
