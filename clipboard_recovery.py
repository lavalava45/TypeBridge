"""Bounded DPAPI clipboard checkpoints. No clipboard access on import.

Only ciphertext reaches disk. Checkpoints are verified before the caller may
replace the clipboard, and retained after partial restore or process failure.
"""
import base64
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import uuid

MAX_RAW = 8_000_000
MAX_FILE = 12_000_000
MAGIC = b'TypeBridgeClipboard1\0'


def recovery_folder():
    from app_paths import USER_DIR
    return USER_DIR / 'clipboard-recovery'


def protect(raw):
    import win32crypt
    return win32crypt.CryptProtectData(raw, 'TypeBridge clipboard recovery', None, None, None, 1)


def unprotect(raw):
    import win32crypt
    return win32crypt.CryptUnprotectData(raw, None, None, None, 1)[1]


class Bitmap(ctypes.Structure):
    _fields_ = [('type', w.LONG), ('width', w.LONG), ('height', w.LONG),
                ('stride', w.LONG), ('planes', w.WORD), ('bpp', w.WORD),
                ('bits', ctypes.c_void_p)]


def bitmap_api():
    gdi = ctypes.WinDLL('gdi32', use_last_error=True)
    gdi.GetObjectW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi.GetBitmapBits.argtypes = [w.HANDLE, w.LONG, ctypes.c_void_p]
    gdi.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, w.UINT, w.UINT, ctypes.c_void_p]
    gdi.CreateBitmap.restype = w.HANDLE
    gdi.DeleteObject.argtypes = [w.HANDLE]
    return gdi


def pack_bitmap(handle):
    gdi, info = bitmap_api(), Bitmap()
    if not gdi.GetObjectW(handle, ctypes.sizeof(info), ctypes.byref(info)):
        raise OSError('clipboard-bitmap-metadata')
    size = info.stride * info.height * info.planes
    # Indexed/device-specific palettes cannot be reconstructed from pixels alone.
    if (info.width <= 0 or info.height <= 0 or info.planes != 1
            or info.bpp not in (24, 32) or not 0 < size <= MAX_RAW
            or info.stride != ((info.width * info.bpp + 15) // 16) * 2):
        raise ValueError('clipboard-bitmap-not-portable')
    data = ctypes.create_string_buffer(size)
    if gdi.GetBitmapBits(handle, size, data) != size:
        raise OSError('clipboard-bitmap-read')
    return {'width': info.width, 'height': info.height, 'bpp': info.bpp,
            'stride': info.stride}, data.raw


def unpack_bitmap(meta, raw):
    width, height, bpp, stride = (meta[k] for k in ('width', 'height', 'bpp', 'stride'))
    if (any(type(x) is not int for x in (width, height, bpp, stride))
            or not 0 < width <= MAX_RAW or not 0 < height <= MAX_RAW
            or bpp not in (24, 32) or stride != ((width * bpp + 15) // 16) * 2
            or len(raw) != stride * height or not 0 < len(raw) <= MAX_RAW):
        raise ValueError('clipboard-bitmap-invalid')
    gdi = bitmap_api()
    data = ctypes.create_string_buffer(raw)
    handle = gdi.CreateBitmap(width, height, 1, bpp, data)
    if not handle:
        raise OSError('clipboard-bitmap-create')
    try:
        observed_meta, observed = pack_bitmap(handle)
        if observed_meta != meta or observed != raw:
            raise OSError('clipboard-bitmap-readback')
        return handle
    except Exception:
        gdi.DeleteObject(handle)
        raise


def serialize(snapshot, format_name):
    from clipboard_selection import BitmapBackup
    rows, total = [], 0
    for fmt, value in snapshot:
        meta, raw = pack_bitmap(value.handle) if isinstance(value, BitmapBackup) else (None, value)
        if not isinstance(raw, bytes):
            raise ValueError('clipboard-backup-requires-bytes')
        total += len(raw)
        if total > MAX_RAW or len(rows) >= 64:
            raise ValueError('clipboard-backup-limit')
        rows.append({'format': fmt, 'name': format_name(fmt) if fmt >= 0xC000 else None,
                     'bitmap': meta, 'data': base64.b64encode(raw).decode('ascii')})
    payload = json.dumps({'version': 1, 'rows': rows}, ensure_ascii=True).encode('ascii')
    decode(payload)  # Validate every format before any clipboard mutation.
    return payload


def decode(payload):
    if len(payload) > MAX_FILE:
        raise ValueError('clipboard-backup-limit')
    document = json.loads(payload)
    if document.get('version') != 1 or not isinstance(document.get('rows'), list):
        raise ValueError('clipboard-backup-version')
    rows, total, seen = [], 0, set()
    for row in document['rows']:
        fmt, name, bitmap = row['format'], row['name'], row['bitmap']
        if (type(fmt) is not int or not 1 <= fmt <= 0xFFFF or fmt in seen
                or len(rows) >= 64 or (fmt >= 0xC000 and
                    (not isinstance(name, str) or not 1 <= len(name) <= 255 or '\0' in name))
                or (fmt < 0xC000 and name is not None)
                or (bitmap is not None and (fmt not in (2, 0x82) or not isinstance(bitmap, dict)))
                or (bitmap is None and (fmt in (2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E)
                                       or 0x200 <= fmt <= 0x2FF))):
            raise ValueError('clipboard-backup-format')
        raw = base64.b64decode(row['data'], validate=True)
        total += len(raw)
        if total > MAX_RAW:
            raise ValueError('clipboard-backup-limit')
        rows.append((fmt, name, bitmap, raw))
        seen.add(fmt)
    return rows


def regular_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
           for p in (path, *path.parents)):
        raise ValueError('clipboard-backup-link')
    return path


class Checkpoint:
    def __init__(self, path, ciphertext, payload):
        self.path, self.ciphertext, self.payload = path, ciphertext, payload

    @classmethod
    def create(cls, snapshot, format_name, *, folder=None):
        payload = serialize(snapshot, format_name)
        ciphertext = MAGIC + protect(payload)
        if len(ciphertext) > MAX_FILE:
            raise ValueError('clipboard-backup-limit')
        folder = regular_path(folder or recovery_folder())
        folder.mkdir(parents=True, exist_ok=True)
        existing = list(folder.glob('*.tbclip'))
        if (len(existing) >= 32
                or sum(p.stat().st_size for p in existing) + len(ciphertext) > 64_000_000):
            raise OSError('clipboard-recovery-folder-full')
        path = folder / (uuid.uuid4().hex + '.tbclip')
        created = False
        try:
            with path.open('xb') as stream:
                created = True
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            loaded = cls.load(path)
            if loaded.payload != payload or loaded.ciphertext != ciphertext:
                raise OSError('clipboard-backup-verification')
            return loaded
        except Exception:
            # No clipboard mutation has been authorized yet. Remove only the
            # incomplete file created by this attempt, not old recovery data.
            if created:
                try:
                    path.unlink()
                except OSError:
                    pass
            raise

    @classmethod
    def load(cls, path):
        path = regular_path(path)
        with path.open('rb') as stream:
            ciphertext = stream.read(MAX_FILE + 1)
        if len(ciphertext) > MAX_FILE or not ciphertext.startswith(MAGIC):
            raise ValueError('clipboard-backup-invalid')
        payload = unprotect(ciphertext[len(MAGIC):])
        decode(payload)
        return cls(path, ciphertext, payload)

    def matches(self, snapshot, format_name):
        # Registered IDs can change after reboot; names are the stable identity.
        def canonical(payload):
            return [(name if name is not None else fmt, meta, raw)
                    for fmt, name, meta, raw in decode(payload)]
        return canonical(serialize(snapshot, format_name)) == canonical(self.payload)

    def materialize(self, native):
        from clipboard_selection import BitmapBackup
        rows, seen = [], set()
        for fmt, name, meta, raw in decode(self.payload):
            if name is not None:
                fmt = native.cb.RegisterClipboardFormat(name)
            if not fmt or fmt in seen:
                raise ValueError('clipboard-backup-format-collision')
            seen.add(fmt)
            if meta is not None:
                raw = BitmapBackup(unpack_bitmap(meta, raw), len(raw))
                native.bitmaps.append(raw)
            rows.append((fmt, raw))
        return rows

    def discard(self):
        # Delete only our verified checkpoint, never a subsequently replaced file.
        path = regular_path(self.path)
        if path.exists():
            with path.open('rb') as stream:
                if stream.read(MAX_FILE + 1) != self.ciphertext:
                    raise OSError('clipboard-backup-file-changed')
            path.unlink()


def restore_file(path, *, still_valid=lambda: True):
    """Explicit user action only. Never invoked at startup or on a timer."""
    from clipboard_selection import NativeCopy
    checkpoint = Checkpoint.load(path)
    native = NativeCopy(None, verify_copy_target=False)
    try:
        native._checkpoint = checkpoint
        # This is an older unresolved operation, even if this attempt refuses
        # before writing. close() must never discard its recovery file.
        native._mutation_possible = True
        snapshot = checkpoint.materialize(native)
        native.cb.OpenClipboard(native.hwnd)
        try:
            sequence, owner = native.sequence(), native.cb.GetClipboardOwner()
        finally:
            native.cb.CloseClipboard()
        # Reuse the verified original; even a full recovery folder must not
        # prevent a recovery attempt. It remains until ALL formats succeed.
        if not native.restore(snapshot, sequence, None, owner_hwnd=owner,
                              explicit_recovery=True, still_valid=still_valid):
            raise OSError('clipboard-changed-during-recovery')
        checkpoint.discard()
    finally:
        native.close()
