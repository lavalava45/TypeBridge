"""R16/4c: safe offline install and activation of data-only v1/v2 languages.

No imports or execution from a package. No downloads, input hooks, HKL
installation or running-engine mutation. An installed/removed language is
only made active after a normal full TypeBridge restart.
"""

from dataclasses import replace
import hashlib
import json
import ntpath
import os
from pathlib import Path
import shutil
import stat
from tempfile import TemporaryDirectory, mkdtemp
import zipfile

from language_package_transactions import (package_lock, replay_locked,
    fingerprint, start, mark, finish, _sync_directory)

from language_pack_loader import (_CODE, _regular_path,
                                  available_language_catalog,
                                  load_installed_language_pack)
from language_registry import LANGUAGE_BY_CODE, validate_active_languages


def _real_directory(path):
    path = Path(path)
    if any(not _regular_path(part) for part in (path, *path.parents)):
        raise ValueError('Package directory cannot use symlinks or junctions')
    path = path.resolve(strict=True)
    if (not path.is_dir() or not _regular_path(path)
            or any(not _regular_path(parent) for parent in path.parents)):
        raise ValueError('Package destination must be a real directory')
    return path


def _ensure_install_root(destination_root):
    """Create the optional external-pack directory on first installation."""
    root = Path(destination_root)
    if not root.exists() and not root.is_symlink() and not getattr(root, 'is_junction', lambda: False)():
        parent = _real_directory(root.parent)
        (parent / root.name).mkdir(exist_ok=True)
    return _real_directory(root)


def _copy_regular_file(source, target):
    if (not _regular_path(source) or not source.is_file()
            or not stat.S_ISREG(source.stat(follow_symlinks=False).st_mode)):
        raise ValueError('Unsafe package payload during installation')
    with source.open('rb') as reader, target.open('xb') as writer:
        while part := reader.read(1024 * 1024):
            writer.write(part)
        writer.flush()
        os.fsync(writer.fileno())


def _publish(staged, destination_root, code, storage):
    destination = destination_root / code
    if destination.exists() or destination.is_symlink() or getattr(
            destination, 'is_junction', lambda: False)():
        raise FileExistsError(f'Already installed; no implicit overwrite: {code}')
    # The rename is the commit point. Replay accepts a published package even
    # if a subsequent journal update fails or the process crashes.
    if storage is None:
        os.rename(staged, destination)
        return destination
    location, transaction = start(storage, destination_root, code, 'install',
                                  replacement=fingerprint(staged))
    mark(location, transaction, 'ready')
    try:
        os.rename(staged, destination)
        _sync_directory(destination_root)
        mark(location, transaction, 'published')
        finish(location, transaction)
    except Exception:
        replay_locked(destination_root, storage, check_foreign=False)
        if destination.is_dir() and fingerprint(destination) == transaction['replacement']:
            return destination
        raise
    return destination


def _install_directory(source, destination_root, storage):
    """Install one verified directory, no replacement of existing packages."""
    source = _real_directory(source)
    root = _real_directory(destination_root)
    if source.parent == root or root in source.parents or source in root.parents:
        raise ValueError('Source and destination directories must be separate')
    verified = load_installed_language_pack(source)
    code = verified.description.code
    if code in LANGUAGE_BY_CODE:
        raise ValueError('Cannot override a bundled language compatibility package')
    if (root / code).exists():
        raise FileExistsError(f'Package already installed: {code}')
    # Validate LANGID/code collisions before publication. Broken unrelated
    # packages are isolated rather than blocking a new safe installation.
    catalog = available_language_catalog(root, tolerant=True)
    if code in catalog or any(item.langid == verified.description.langid
                              for item in catalog.values()):
        raise ValueError('Installed language code/LANGID collision')
    digests = {}
    for file in source.iterdir():
        digest = hashlib.sha256()
        with file.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digests[file.name] = digest.hexdigest()
    with TemporaryDirectory(prefix='.typebridge-install-', dir=root.parent) as folder:
        staged = Path(folder) / code
        staged.mkdir()
        for path in source.iterdir():
            if not path.is_file() or not _regular_path(path):
                raise ValueError('Package source changed after validation')
            _copy_regular_file(path, staged / path.name)
        again = load_installed_language_pack(staged)
        copied_digests = {}
        for file in staged.iterdir():
            digest = hashlib.sha256()
            with file.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            copied_digests[file.name] = digest.hexdigest()
        if again.description != verified.description or copied_digests != digests:
            raise ValueError('Copied language package differs from source')
        return _publish(staged, root, code, storage)


def _install_zip(archive, destination_root, storage=None):
    """ZIP with exactly `code/manifest.json` and declared flat .txt/.md files.

    Extraction is bounded and strictly confined to a temporary sibling of
    the installation root. The existing package directory is never replaced.
    """
    root = _real_directory(destination_root)
    archive = Path(archive)
    if not _regular_path(archive) or not archive.is_file():
        raise ValueError('ZIP must be a regular file')
    with zipfile.ZipFile(archive, 'r') as zipped:
        members = zipped.infolist()
        if not 2 <= len(members) <= 26:
            raise ValueError('Invalid number of ZIP entries')
        filenames = [member.filename for member in members]
        if len(filenames) != len(set(filenames)):
            raise ValueError('Duplicate ZIP entries')
        codes = {name.split('/')[0] for name in filenames}
        if len(codes) != 1:
            raise ValueError('ZIP must contain exactly one language')
        (code,) = codes
        if not _CODE.fullmatch(code) or code in LANGUAGE_BY_CODE:
            raise ValueError('Unsafe language package code')
        if (root / code).exists():
            raise FileExistsError(f'Package already installed: {code}')
        declared = set()
        reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                    *(f'LPT{i}' for i in range(1, 10))}
        for member in members:
            name = member.filename
            if (member.is_dir() or name.count('/') != 1 or not name.startswith(code + '/')
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Unsafe ZIP member path or type')
            basename = name.split('/')[1]
            # Validate raw ZIP tokens before Path joins: a colon can mean a
            # Windows drive-relative path or an alternate data stream.
            if (':' in name or ntpath.isabs(name) or ntpath.splitdrive(name)[0]
                    or any(token.rstrip(' .') != token for token in (code, basename))
                    or basename.split('.')[0].upper() in reserved):
                raise ValueError('Unsafe Windows ZIP filename')
            if (basename != 'manifest.json' and
                    (not basename or not basename.endswith(('.txt', '.md')))):
                raise ValueError('ZIP contains unsupported payload')
            if basename.startswith('.') or '\\' in basename or basename in ('', '.', '..'):
                raise ValueError('ZIP contains unsafe filename')
            if member.file_size < 1 or member.file_size > 64 * 1024 * 1024:
                raise ValueError('ZIP payload size out of bounds')
            declared.add(basename)
        if 'manifest.json' not in declared or sum(m.file_size for m in members) > 192 * 1024 * 1024:
            raise ValueError('ZIP missing manifest or too large')
        with TemporaryDirectory(prefix='.typebridge-install-', dir=root.parent) as folder:
            staged = Path(folder) / code
            staged.mkdir()
            for member in members:
                target = staged / member.filename.split('/')[1]
                if target.resolve(strict=False).parent != staged.resolve(strict=True):
                    raise ValueError('ZIP extraction escapes staging directory')
                length = 0
                with zipped.open(member) as reader, target.open('xb') as writer:
                    while part := reader.read(1024 * 1024):
                        length += len(part)
                        if length > member.file_size:
                            raise ValueError('ZIP payload exceeds declared size')
                        writer.write(part)
                    writer.flush()
                    os.fsync(writer.fileno())
                if length != member.file_size:
                    raise ValueError('Truncated ZIP payload')
            loaded = load_installed_language_pack(staged)
            catalog = available_language_catalog(root, tolerant=True)
            if loaded.description.langid in (item.langid for item in catalog.values()):
                raise ValueError('Installed language LANGID collision')
            return _publish(staged, root, code, storage)


def install_directory(source, destination_root):
    """Serialize publication with other language package operations."""
    destination_root = _ensure_install_root(destination_root)
    with package_lock(destination_root) as storage:
        replay_locked(destination_root, storage)
        return _install_directory(source, destination_root, storage)


def install_zip(archive, destination_root):
    """Serialize publication with other language package operations."""
    destination_root = _ensure_install_root(destination_root)
    with package_lock(destination_root) as storage:
        replay_locked(destination_root, storage)
        return _install_zip(archive, destination_root, storage)


def save_active_selection(settings_path, pack_root, selected, *, check_hkl=True):
    """Write settings via the existing recoverable two-file transaction.

    Does not touch the running hook: the new language set takes effect only
    after the user exits via tray and launches TypeBridge again.
    """
    from typebridge_app import load_settings, save_settings
    from engine import check_active_layouts_loaded

    catalog = available_language_catalog(pack_root, tolerant=True)
    desired = validate_active_languages(selected, catalog)
    if check_hkl:
        check_active_layouts_loaded(desired, catalog)
    settings = load_settings(settings_path, catalog=catalog)
    save_settings(replace(settings, active_languages=desired), settings_path,
                  catalog=catalog)
    return desired


def _running_instance_count():
    import psutil
    return sum(1 for p in psutil.process_iter(['name'])
               if (p.info.get('name') or '').lower() == 'typebridge.exe')


def _guard_replacement(root, code, settings_path, running_languages=None):
    """Refuse any mutation of a selected or currently used package.

    The standalone API refuses all mutations while TypeBridge is running;
    the in-process GUI may supply its immutable launch set for inactive
    packages, but cannot override a persisted active selection.
    """
    if code in LANGUAGE_BY_CODE or not _CODE.fullmatch(code):
        raise ValueError('Cannot replace a bundled or unsafe language')
    from typebridge_app import load_settings
    active_catalog = available_language_catalog(root, tolerant=True)
    # An invalid selected package must not disappear from the settings
    # merely because catalog inventory quarantined it.
    current = load_settings(settings_path, catalog=active_catalog)
    if code in current.active_languages:
        raise ValueError('Active installed language must be disabled and saved first')
    if running_languages is None:
        if _running_instance_count():
            raise RuntimeError('Close TypeBridge through the tray before changing a package')
    else:
        if code in running_languages:
            raise ValueError('Language is active in the running hook; full restart required')
        # A GUI instance only knows ITS launch-language set. With multiple
        # TypeBridge processes another hook may currently be using this pack.
        # Refuse a hot change when we cannot prove it is inactive everywhere.
        if _running_instance_count() > 1:
            raise RuntimeError('Several TypeBridge instances are running; close them through the tray before modifying language packages')


def _verify_replacement_personal_words(root, code, replacement, settings_path):
    """Fail before publish if new package policy would quarantine personal words.

    An update cannot silently deactivate user-maintained words. The existing
    external personal lexicon remains untouched; a previously published
    incompatible pack is separately handled by UserLexicon's lossless
    quarantine at the next startup. This preflight only reads a local profile.
    """
    from user_lexicon import UserLexicon
    catalog = available_language_catalog(root, tolerant=True)
    catalog[code] = replacement.description
    profile = Path(settings_path).parent
    personal = UserLexicon.load(profile, catalog=catalog)
    if (code in personal.unavailable['words'] or
            code in personal.unavailable['exclusions']):
        raise ValueError(
            f'Cannot update {code.upper()}: existing personal words or '
            'exclusions are incompatible with the replacement language '
            'policy. They were not changed; keep the old package, or '
            'explicitly edit and back up the personal dictionary first.')


def _archive_old(root, code, *, operation):
    """Move old bytes to durable sibling history, not to temp trash."""
    old = root / code
    if (not _regular_path(old) or not old.is_dir()
            or any(not _regular_path(item) for item in old.iterdir())):
        raise ValueError('Unsafe installed package cannot be replaced')
    history = root.parent / 'language_pack_history'
    history.mkdir(exist_ok=True)
    history = _real_directory(history)
    record = Path(mkdtemp(prefix=f'{code}-{operation}-', dir=history))
    backup = record / code
    try:
        os.rename(old, backup)
        return record, backup
    except Exception:
        record.rmdir()
        raise


def update_zip(archive, root, settings_path, *, running_languages=None):
    """Prevalidate ZIP separately, then replace disabled package with rollback.

    Previously installed directory remains in persistent history on success.
    A failed publication restores its previous bytes and leaves no partial
    installed directory. Never changes any personal lexicon or settings file.
    """
    root = _real_directory(root)
    archive = Path(archive)
    with zipfile.ZipFile(archive) as zipped:
        top = {entry.filename.split('/')[0] for entry in zipped.infolist()}
    if len(top) != 1:
        raise ValueError('Update must contain exactly one language code')
    (code,) = top
    with package_lock(root) as storage:
        replay_locked(root, storage, running_languages=running_languages)
        _guard_replacement(root, code, settings_path, running_languages)
        old = root / code
        if not old.is_dir():
            raise ValueError('Cannot update a language that is not installed')
        original = fingerprint(old)
        history = root.parent / 'language_pack_history'
        history.mkdir(exist_ok=True)
        history = _real_directory(history)
        record = Path(mkdtemp(prefix=f'{code}-update-', dir=history))
        backup = record / code
        location, transaction = start(storage, root, code, 'update',
                                      original=original, backup=backup)
        try:
            stage_root = location / 'typebridge-update-new'
            stage_root.mkdir()
            # This is an unpublished private staging directory already covered
            # by the outer root lock and update journal. No nested lock/journal.
            new = _install_zip(archive, stage_root)
            if new.name != code:
                raise ValueError('Updated package code differs from existing package')
            replacement = load_installed_language_pack(new)
            for other, details in available_language_catalog(root, tolerant=True).items():
                if other != code and details.langid == replacement.description.langid:
                    raise ValueError('Updated package collides with installed LANGID')
            _verify_replacement_personal_words(root, code, replacement, settings_path)
            transaction['replacement'] = fingerprint(new)
            mark(location, transaction, 'ready')
            _guard_replacement(root, code, settings_path, running_languages)
            _verify_replacement_personal_words(root, code, replacement, settings_path)
            if fingerprint(old) != original:
                raise ValueError('Installed package modified during update')
            os.rename(old, backup)
            _sync_directory(root)
            _sync_directory(record)
            mark(location, transaction, 'archived')
            os.rename(new, old)
            _sync_directory(root)
            mark(location, transaction, 'published')
            finish(location, transaction)
            return old, backup
        except Exception:
            # A normal exception can be recovered now; a process crash leaves
            # the fsynced journal for replay on the next startup.
            replay_locked(root, storage, running_languages=running_languages,
                          check_foreign=False)
            if (old.is_dir() and backup.is_dir()
                    and fingerprint(old) == transaction.get('replacement')
                    and fingerprint(backup) == original):
                return old, backup
            if record.exists() and not any(record.iterdir()):
                record.rmdir()
            raise


def remove_package(root, code, settings_path, *, running_languages=None):
    """Reversible removal: directory moved to history, personal data stays."""
    root = _real_directory(root)
    with package_lock(root) as storage:
        replay_locked(root, storage, running_languages=running_languages)
        _guard_replacement(root, code, settings_path, running_languages)
        old = root / code
        if not old.is_dir():
            raise FileNotFoundError(code)
        original = fingerprint(old)
        _guard_replacement(root, code, settings_path, running_languages)
        history = root.parent / 'language_pack_history'
        history.mkdir(exist_ok=True)
        history = _real_directory(history)
        record = Path(mkdtemp(prefix=f'{code}-remove-', dir=history))
        backup = record / code
        location, transaction = start(storage, root, code, 'remove',
                                      original=original, backup=backup)
        try:
            if fingerprint(old) != original:
                raise ValueError('Installed package modified during removal')
            os.rename(old, backup)
            _sync_directory(root)
            _sync_directory(record)
            mark(location, transaction, 'archived')
            finish(location, transaction)
            return backup
        except Exception:
            replay_locked(root, storage, running_languages=running_languages,
                          check_foreign=False)
            if record.exists() and not any(record.iterdir()):
                record.rmdir()
            raise


def restore_package(root, backup, settings_path, *, running_languages=None):
    """Restore a previously archived verified data package without overwrite."""
    root = _real_directory(root)
    backup = _real_directory(backup)
    history = _real_directory(root.parent / 'language_pack_history')
    if history not in backup.parents or backup.name in LANGUAGE_BY_CODE:
        raise ValueError('Restore source must be within package history')
    code = backup.name
    with package_lock(root) as storage:
        replay_locked(root, storage, running_languages=running_languages)
        _guard_replacement(root, code, settings_path, running_languages)
        if (root / code).exists():
            raise FileExistsError('Disable/remove current package before restoring')
        verified = load_installed_language_pack(backup)
        for other, details in available_language_catalog(root, tolerant=True).items():
            if other != code and details.langid == verified.description.langid:
                raise ValueError('Restored package collides with installed LANGID')
        original = fingerprint(backup)
        location, transaction = start(storage, root, code, 'restore',
                                      original=original, backup=backup)
        try:
            os.rename(backup, root / code)
            _sync_directory(root)
            _sync_directory(backup.parent)
            mark(location, transaction, 'restored')
            finish(location, transaction)
            return root / code
        except Exception:
            replay_locked(root, storage, running_languages=running_languages,
                          check_foreign=False)
            raise
