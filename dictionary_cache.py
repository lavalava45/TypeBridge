"""Optional source-bound JSON cache. Never deserializes executable objects."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import zlib

MAGIC = b'TYPEBRIDGE-WORDS-1'
MAX_COMPRESSED = 64 * 1024 * 1024
MAX_DECODED = 128 * 1024 * 1024
LANGS = ('ru', 'en', 'he')


def policy_digest(functions, extra):
    """Stable code identity, independent of marshal's string reference flags."""
    def describe(value):
        if isinstance(value, types.CodeType):
            return [value.co_code.hex(), value.co_names, value.co_varnames,
                    [describe(item) for item in value.co_consts]]
        if isinstance(value, (tuple, frozenset)):
            items = [describe(item) for item in value]
            return sorted(items, key=repr) if isinstance(value, frozenset) else items
        if isinstance(value, bytes):
            return ['bytes', value.hex()]
        return value
    body = [[describe(function.__code__) for function in functions], extra]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def fingerprint(directory, filenames, policy):
    digest = hashlib.sha256(policy.encode('utf-8'))
    for name in filenames:
        digest.update(name.encode('ascii') + b'\0')
        with (directory / name).open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b'\0')
    return digest.hexdigest().encode('ascii')


def _valid(snapshot, languages=LANGS):
    if not isinstance(snapshot, dict) or set(snapshot) != {'words', 'confident', 'exclusions'}:
        return False
    for field in ('words', 'confident'):
        if not isinstance(snapshot[field], dict) or set(snapshot[field]) != set(languages):
            return False
    arrays = [snapshot[field][lang] for field in ('words', 'confident') for lang in languages]
    arrays.append(snapshot['exclusions'])
    for words in arrays:
        if not isinstance(words, list):
            return False
        previous = ''
        for word in words:
            if not isinstance(word, str) or not word or '\0' in word or word <= previous:
                return False
            previous = word
    return all(snapshot['confident'][lang] for lang in languages)


def load_or_build(directory, filenames, policy, builder, cache_dir, progress=None):
    directory, cache_dir = Path(directory), Path(cache_dir)
    # Missing or unreadable source files are fatal, even if an old cache exists.
    key = fingerprint(directory, filenames, policy)
    path = cache_dir / 'words-v1.cache'
    try:
        if cache_dir.is_symlink() or path.is_symlink() or any(p.is_symlink() for p in cache_dir.parents):
            raise ValueError('cache-path')
        with path.open('rb') as stream:
            raw = stream.read(MAX_COMPRESSED + 1)
        if len(raw) > MAX_COMPRESSED:
            raise ValueError('cache-size')
        magic, stored_key, checksum, payload = raw.split(b'\n', 3)
        if magic != MAGIC or stored_key != key or hashlib.sha256(payload).hexdigest().encode() != checksum:
            raise ValueError('cache-signature')
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as packed:
            decoded = packed.read(MAX_DECODED + 1)
        if len(decoded) > MAX_DECODED:
            raise ValueError('cache-size')
        snapshot = json.loads(decoded)
        if not _valid(snapshot):
            raise ValueError('cache-format')
        if progress:
            progress('hit')
        return snapshot, 'hit'
    except (OSError, ValueError, TypeError, EOFError, RecursionError, zlib.error):
        pass
    if progress:
        progress('build')
    data = builder()
    snapshot = {field: {lang: sorted(data[field][lang]) for lang in LANGS}
                for field in ('words', 'confident')}
    snapshot['exclusions'] = sorted(data['exclusions'])
    # Detect a source edit during preparation. Never publish a mixed snapshot.
    if fingerprint(directory, filenames, policy) != key:
        raise ValueError('Словари изменились при загрузке; повтори запуск')
    temporary = None
    try:
        if cache_dir.is_symlink() or path.is_symlink() or any(p.is_symlink() for p in cache_dir.parents):
            raise ValueError('cache-path')
        cache_dir.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > MAX_DECODED:
            return snapshot, 'uncached'
        payload = gzip.compress(encoded, compresslevel=1, mtime=0)
        with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(MAGIC + b'\n' + key + b'\n' + hashlib.sha256(payload).hexdigest().encode() + b'\n' + payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return snapshot, 'built'
    except (OSError, ValueError):
        return snapshot, 'uncached'  # Read-only portable folders still work.
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Optional cache cleanup must not stop dictionary loading.


# The original words-v1.cache and load_or_build() are intentionally unchanged.
# R16/3b needs a separate namespace: files can come from different verified
# package directories and the selected language keys vary by user profile.
V2_MAGIC = b'TYPEBRIDGE-WORDS-2'


def fingerprint_sources(sources, policy):
    """Hash logical identities AND source bytes, not timestamps or ZIP metadata."""
    digest = hashlib.sha256(policy.encode('utf-8'))
    for name, path in sources:
        if (not isinstance(name, str) or not name.isascii() or not name
                or '\0' in name or not isinstance(path, Path)):
            raise ValueError('Invalid language cache source descriptor')
        digest.update(name.encode('ascii') + b'\0')
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b'\0')
    return digest.hexdigest().encode('ascii')


def load_or_build_sources(sources, policy, builder, cache_dir, languages, progress=None):
    """Source-verified v2 cache for selected-language snapshots.

    This API is opt-in. It never opens, rewrites or migrates words-v1.cache.
    Its only side effect is a dedicated cache file inside cache_dir.
    """
    from language_registry import validate_active_languages

    languages = validate_active_languages(languages, languages)
    sources = tuple((name, Path(path)) for name, path in sources)
    if len({name for name, _ in sources}) != len(sources) or not sources:
        raise ValueError('Duplicate or missing language cache source')
    cache_dir = Path(cache_dir)
    key = fingerprint_sources(sources, policy)
    identity = hashlib.sha256('\0'.join(languages).encode('utf-8')).hexdigest()[:20]
    path = cache_dir / f'words-v2-{identity}.cache'
    try:
        if cache_dir.is_symlink() or path.is_symlink() or any(p.is_symlink() for p in cache_dir.parents):
            raise ValueError('cache-path')
        with path.open('rb') as stream:
            raw = stream.read(MAX_COMPRESSED + 1)
        if len(raw) > MAX_COMPRESSED:
            raise ValueError('cache-size')
        magic, stored_key, checksum, payload = raw.split(b'\n', 3)
        if (magic != V2_MAGIC or stored_key != key
                or hashlib.sha256(payload).hexdigest().encode() != checksum):
            raise ValueError('cache-signature')
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as packed:
            decoded = packed.read(MAX_DECODED + 1)
        if len(decoded) > MAX_DECODED:
            raise ValueError('cache-size')
        snapshot = json.loads(decoded)
        if not _valid(snapshot, languages):
            raise ValueError('cache-format')
        if progress:
            progress('hit')
        return snapshot, 'hit'
    except (OSError, ValueError, TypeError, EOFError, RecursionError, zlib.error):
        pass
    if progress:
        progress('build')
    data = builder()
    snapshot = {field: {lang: sorted(data[field][lang]) for lang in languages}
                for field in ('words', 'confident')}
    snapshot['exclusions'] = sorted(data['exclusions'])
    if not _valid(snapshot, languages):
        raise ValueError('Invalid prepared language lexicon')
    if fingerprint_sources(sources, policy) != key:
        raise ValueError('Language package sources changed during lexicon preparation')
    temporary = None
    try:
        if cache_dir.is_symlink() or path.is_symlink() or any(p.is_symlink() for p in cache_dir.parents):
            raise ValueError('cache-path')
        cache_dir.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > MAX_DECODED:
            return snapshot, 'uncached'
        payload = gzip.compress(encoded, compresslevel=1, mtime=0)
        with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(V2_MAGIC + b'\n' + key + b'\n'
                         + hashlib.sha256(payload).hexdigest().encode() + b'\n' + payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return snapshot, 'built'
    except (OSError, ValueError):
        return snapshot, 'uncached'
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
