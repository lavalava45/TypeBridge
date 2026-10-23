"""Current data location and non-destructive migration from the former app name."""
import os
from pathlib import Path
import tempfile

LOCAL_DATA = Path(os.environ.get('LOCALAPPDATA') or Path.home())
USER_DIR = LOCAL_DATA / 'TypeBridge'
LEGACY_USER_DIR = LOCAL_DATA / 'LayoutSwitch'
DATA_FILES = ('settings.json', 'user_lexicon.json', 'snippets.json')


def migrate_user_data(destination=USER_DIR, source=LEGACY_USER_DIR):
    """Copy missing personal files only; never overwrite or remove either copy."""
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
