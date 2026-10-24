"""Portable profile beside the executable and non-destructive AppData migration."""
import os
import sys
from pathlib import Path
import tempfile

LOCAL_DATA = Path(os.environ.get('LOCALAPPDATA') or Path.home())
APP_DIR = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
           else Path(__file__).resolve().parent)
USER_DIR = APP_DIR / 'TypeBridge-data'
PREVIOUS_USER_DIR = LOCAL_DATA / 'TypeBridge'
LEGACY_USER_DIR = LOCAL_DATA / 'LayoutSwitch'
DATA_FILES = ('settings.json', 'user_lexicon.json', 'snippets.json')


def migrate_user_data(destination=USER_DIR, source=None):
    """Copy missing personal files only; never overwrite or remove either copy."""
    if source is None:
        marker = destination / '.migration-complete'
        if marker.exists():
            return []
        # A portable profile brought from another PC must not inherit that PC's data.
        existing = any((destination / name).exists() for name in DATA_FILES)
        progress = destination / '.migration-in-progress'
        identity = os.environ.get('COMPUTERNAME', '') + '\n' + str(PREVIOUS_USER_DIR.resolve())
        resume = progress.exists() and progress.read_text(encoding='utf-8') == identity
        copied = []
        if not existing or resume:
            destination.mkdir(parents=True, exist_ok=True)
            progress.write_text(identity, encoding='utf-8')
            for previous in (PREVIOUS_USER_DIR, LEGACY_USER_DIR):
                copied.extend(_copy_missing(destination, previous))
        destination.mkdir(parents=True, exist_ok=True)
        with marker.open('a', encoding='utf-8'):
            pass
        progress.unlink(missing_ok=True)
        return copied
    return _copy_missing(destination, source)


def _copy_missing(destination, source):
    copied = []
    for name in DATA_FILES:
        old, new = source/name, destination/name
        if not old.is_file() or new.exists():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(old.read_bytes()); stream.flush(); os.fsync(stream.fileno())
            if not new.exists():
                # Windows rename refuses an existing destination, including a racing writer.
                try:
                    temporary.rename(new); copied.append(name)
                except FileExistsError:
                    pass
        finally:
            if temporary and temporary.exists(): temporary.unlink()
    return copied
