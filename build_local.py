"""Build local Public and Diagnostics profiles from the same working tree.

The main EXE is dist/TypeBridge.exe. The existing Diagnostics profile is
updated in dist/profiles/Diagnostics. Never replace external user data.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from build_profiles import (PUBLIC_EXCLUDED_MODULES, frozen_smoke_test,
                            profile_directory,
                            stage_sources, verify_frozen_profile)
from engine import DICTIONARY_FILES, require_dictionary_files


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_profile(root, destination, profile):
    """Build and audit an isolated candidate, never the installed EXE."""
    target = destination / 'TypeBridge.exe'
    destination.mkdir(parents=True, exist_ok=True)
    stage = root / 'build' / f'local-{profile}-source'
    work = root / 'build' / f'local-{profile}-work'
    spec = root / 'build' / f'local-{profile}-spec'
    log = root / 'build' / f'pyinstaller-local-{profile}.log'
    stage.parent.mkdir(parents=True, exist_ok=True)
    if stage.exists():
        shutil.rmtree(stage)
    stage_sources(root, stage, profile)
    work.mkdir(parents=True, exist_ok=True)
    spec.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onefile', '--windowed', '--name', 'TypeBridge',
               '--distpath', str(destination), '--workpath', str(work),
               '--specpath', str(spec), '--add-data',
               str(stage / 'dictionary_packs') + ';dictionary_packs']
    if profile == 'public':
        for module in PUBLIC_EXCLUDED_MODULES:
            command.extend(('--exclude-module', module))
    command.append('typebridge_app.py')
    print(f'Building {profile} into {target}', flush=True)
    with log.open('w', encoding='utf-8') as stream:
        subprocess.run(command, cwd=stage, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)
    if not target.is_file():
        raise RuntimeError(f'PyInstaller did not produce {target}')
    inventory = verify_frozen_profile(target, profile)
    report = frozen_smoke_test(target, profile)
    return inventory, report


def assert_typebridge_stopped():
    """The canonical EXE must only be changed after an explicit tray exit."""
    import psutil
    for process in psutil.process_iter(['name']):
        if (process.info.get('name') or '').casefold() == 'typebridge.exe':
            raise RuntimeError('TypeBridge is still running; exit through the tray before building')


def publish_verified(candidates, destinations, backup_dir):
    """Publish only *already verified* EXEs and manifests; roll back on error.

    All backups are private temporary files inside the build directory and
    are removed after a successful/rolled-back build. No alternate user EXE.
    """
    assert_typebridge_stopped()
    changes = []
    package_changes = []
    for profile, destination in destinations.items():
        candidate, manifest = candidates[profile]
        package_src = candidate / 'bundled_language_packs'
        if package_src.is_dir():
            from language_pack_v2 import load_v2_pack
            for code in ('ru', 'en', 'he'):
                load_v2_pack(package_src / code)
            package_dst = destination / 'bundled_language_packs'
            if package_dst.exists() and (not package_dst.is_dir()
                                         or package_dst.is_symlink()):
                raise ValueError('Unsafe installed bundled package directory')
            package_changes.append((package_src, package_dst,
                                    backup_dir / profile / 'retired-packages'))
        outputs = [('TypeBridge.exe', candidate / 'TypeBridge.exe')]
        for filename in (('BUILD-PROFILE.json', 'BUILD-INFO.json')
                         if profile == 'public' else ('BUILD-PROFILE.json',)):
            payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            item = candidate / filename
            item.write_bytes(payload)
            outputs.append((filename, item))
        for name, src in outputs:
            dst = destination / name
            if not src.is_file():
                raise RuntimeError(f'Verified build artifact missing: {src}')
            prior = backup_dir / profile / name
            prior.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_file():
                shutil.copy2(dst, prior)
                if sha256(dst) != sha256(prior):
                    raise RuntimeError(f'Backup checksum mismatch: {dst}')
            elif dst.exists():
                raise RuntimeError(f'Unsafe output path: {dst}')
            changes.append((src, dst, prior if prior.is_file() else None))
    package_states = []
    try:
        assert_typebridge_stopped()
        for src, dst, prior in changes:
            os.replace(src, dst)
        for src, dst, retired in package_changes:
            state = {'destination': dst, 'retired': retired,
                     'old_moved': False, 'new_published': False}
            package_states.append(state)
            if dst.is_dir():
                retired.parent.mkdir(parents=True, exist_ok=True)
                os.replace(dst, retired)
                state['old_moved'] = True
            os.replace(src, dst)
            state['new_published'] = True
    except Exception:
        failures = []
        for state in reversed(package_states):
            dst, retired = state['destination'], state['retired']
            try:
                if state['new_published'] and dst.is_dir():
                    shutil.rmtree(dst)
                if state['old_moved']:
                    os.replace(retired, dst)
            except OSError as error:
                failures.append(f'{dst}: {error}')
        for _, dst, prior in reversed(changes):
            try:
                if prior is None:
                    dst.unlink(missing_ok=True)
                else:
                    shutil.copy2(prior, dst)
                    if sha256(dst) != sha256(prior):
                        raise RuntimeError('Restored hash mismatch')
            except OSError as error:
                failures.append(f'{dst}: {error}')
        if failures:
            raise RuntimeError('Build publication rollback failed: ' + '; '.join(failures))
        raise


def main():
    root = Path(__file__).resolve().parent
    assert_typebridge_stopped()
    dist = root / 'dist'
    obsolete_public = dist / 'profiles' / 'Public' / 'TypeBridge.exe'
    if obsolete_public.is_file():
        print('WARNING: obsolete profiles/Public/TypeBridge.exe is NOT updated; '
              'the only Public build target is dist/TypeBridge.exe.', flush=True)
    destinations = {profile: profile_directory(dist, profile)
                    for profile in ('public', 'diagnostics')}
    # Both locations already exist on developer machines. Never create a
    # second personal distribution or overwrite the profile's external data.
    dictionary_hashes = {}
    settings_hashes = {}
    for profile, destination in destinations.items():
        if not destination.is_dir() or not (destination / 'data').is_dir():
            raise RuntimeError(f'Existing {profile} data directory required: {destination}')
        require_dictionary_files(destination / 'data')
        dictionary_hashes[profile] = {
            name: sha256(destination / 'data' / name) for name in DICTIONARY_FILES}
        settings = destination / 'typebridge-settings.json'
        settings_hashes[profile] = sha256(settings) if settings.is_file() else None
    source_files = sorted(root.glob('*.py'))
    source_hashes = {item.name: sha256(item) for item in source_files}
    bundled_root = root / 'bundled_language_packs'
    bundle_hashes = {}
    if bundled_root.is_dir():
        from language_pack_v2 import load_v2_pack
        for code in ('ru', 'en', 'he'):
            load_v2_pack(bundled_root / code)
        bundle_hashes = {p.relative_to(bundled_root).as_posix(): sha256(p)
                         for p in bundled_root.rglob('*') if p.is_file()}
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root,
                                     text=True).strip()
    dirty = bool(subprocess.check_output(
        ['git', 'status', '--porcelain', '--untracked-files=normal'],
        cwd=root, text=True).strip())

    (root / 'build').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='verified-typebridge-', dir=root / 'build') as tmp:
        staging = Path(tmp)
        candidates = {}
        for profile, destination in destinations.items():
            candidate = staging / profile
            # Frozen self-test reads dictionaries next to the EXE. Supply a
            # private, verified read-only-in-practice snapshot in the staging
            # directory, not the user's profile or settings files.
            candidate_data = candidate / 'data'
            candidate_data.mkdir(parents=True)
            for filename, expected in dictionary_hashes[profile].items():
                copied = candidate_data / filename
                shutil.copy2(destination / 'data' / filename, copied)
                if sha256(copied) != expected:
                    raise RuntimeError(f'{profile}: staged dictionary mismatch: {filename}')
            if bundle_hashes:
                shutil.copytree(bundled_root, candidate / 'bundled_language_packs')
                if any(sha256(candidate / 'bundled_language_packs' / name) != digest
                       for name, digest in bundle_hashes.items()):
                    raise RuntimeError(f'{profile}: staged bundled packages mismatch')
            inventory, report = build_profile(root, candidate, profile)
            if any(sha256(root / name) != value for name, value in source_hashes.items()):
                raise RuntimeError('Source changed during build; cannot certify source identity')
            if any(sha256(bundled_root / name) != digest
                   for name, digest in bundle_hashes.items()):
                raise RuntimeError('Bundled language package changed during build')
            for checked_profile, checked_destination in destinations.items():
                if any(sha256(checked_destination / 'data' / name) != value
                       for name, value in dictionary_hashes[checked_profile].items()):
                    raise RuntimeError(f'{checked_profile}: dictionary data changed during build')
                settings = checked_destination / 'typebridge-settings.json'
                if (sha256(settings) if settings.is_file() else None) != settings_hashes[checked_profile]:
                    raise RuntimeError(f'{checked_profile}: existing settings file changed during build')
            manifest = {
                'profile': profile, 'source_commit': commit, 'worktree_dirty': dirty,
                'built_utc': datetime.now(timezone.utc).isoformat(),
                'exe_sha256': sha256(candidate / 'TypeBridge.exe'),
                'source_sha256': source_hashes,
                'dictionary_sha256': dictionary_hashes[profile],
                'bundled_languages_sha256': bundle_hashes,
                'profile_settings_sha256': settings_hashes[profile],
                'frozen_inventory': inventory, 'self_test': report,
                'note': 'Local working-source build; not physical input acceptance',
            }
            candidates[profile] = (candidate, manifest)
        # Neither user EXE is touched until *both* profiles have passed all
        # verification steps. Publication itself has temporary rollback copies.
        publish_verified(candidates, destinations, staging / 'rollback')
        for profile, destination in destinations.items():
            print(f'{profile.upper()}_READY {destination / "TypeBridge.exe"} '
                  f'sha256={candidates[profile][1]["exe_sha256"]}', flush=True)
    print('Both profiles passed frozen self-test 6/6; Public text-tracing excluded', flush=True)


if __name__ == '__main__':
    main()
