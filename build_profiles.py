"""Create isolated Public and Diagnostics Windows EXEs from the same source.

Public EXE must contain no user-text tracing modules, regardless of `.flag`
files alongside it. Diagnostics is local-only; never include it in releases.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PUBLIC_EXCLUDED_MODULES = (
    'word_trace', 'browser_readback', 'comparison_diagnostics',
    'hook_watchdog', 'raw_input_probe', 'keyboard_control_probe',
)
FORBIDDEN_TEXT_MODULES = ('word_trace', 'browser_readback')
PROFILES = ('public', 'diagnostics')
RELEASE_DATA_NAMES = (
    'ru.txt', 'en.txt', 'he.txt', 'confident_ru.txt', 'confident_en.txt',
    'confident_he.txt', 'frequency_ru.tsv', 'frequency_en.tsv',
    'frequency_he.tsv', 'FREQUENCY-SOURCES.json', 'FREQUENCY-DATA-LICENSE.txt',
    'RU-SOURCES.json', 'RU-DATA-LICENSE.txt', 'SOURCES.json',
    'DATA-LICENSE.txt', 'HEBREW-SOURCE.md',
)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen_module_names(exe):
    """Inspect actual PyInstaller PYZ, not an unverified import graph."""
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    archive = CArchiveReader(str(exe))
    names = set(archive.toc)
    pyzs = [name for name in names if name.endswith('.pyz')]
    if len(pyzs) != 1:
        raise ValueError('Expected exactly one embedded PyInstaller PYZ')
    payload = archive.extract(pyzs[0])
    with tempfile.TemporaryDirectory(prefix='typebridge-exe-audit-') as folder:
        payload_path = Path(folder) / 'runtime.pyz'
        payload_path.write_bytes(payload)
        names.update(ZlibArchiveReader(str(payload_path)).toc)
    return names


def verify_frozen_profile(exe, profile):
    if profile not in PROFILES:
        raise ValueError('Unknown build profile')
    names = frozen_module_names(exe)
    if 'build_variant' not in names:
        raise ValueError('Missing frozen build profile marker')
    present = sorted(name for name in PUBLIC_EXCLUDED_MODULES if name in names)
    if profile == 'public' and present:
        raise ValueError('Unsafe public EXE contains diagnostic modules: ' + ', '.join(present))
    if profile == 'diagnostics' and any(name not in names for name in FORBIDDEN_TEXT_MODULES):
        raise ValueError('Diagnostics EXE missing required test tracing modules')
    return {'profile_marker_present': True, 'diagnostic_modules': present,
            'module_count': len(names)}


def stage_sources(source, target, profile):
    if profile not in PROFILES:
        raise ValueError('Unknown profile')
    target.mkdir(parents=True, exist_ok=False)
    for path in source.glob('*.py'):
        if path.name == 'build_variant.py':
            continue
        shutil.copy2(path, target / path.name)
    shutil.copytree(source / 'dictionary_packs', target / 'dictionary_packs')
    (target / 'build_variant.py').write_text(
        '# Embedded by the TypeBridge profile builder. Do not modify at runtime.\n'
        f'PROFILE = {profile!r}\n', encoding='utf-8')


def install_public_data(source, folder):
    destination = folder / 'data'
    destination.mkdir(parents=True, exist_ok=True)
    for name in RELEASE_DATA_NAMES:
        original = source / 'data' / name
        if original.is_file():
            shutil.copy2(original, destination / name)
    for required in ('ru.txt', 'en.txt', 'he.txt', 'confident_ru.txt',
                     'confident_en.txt', 'confident_he.txt'):
        if not (destination / required).is_file():
            raise ValueError('Missing required dictionary: ' + required)
    # The engine requires these two files even when no personal overlays are
    # configured. NEVER copy the developer's ignored local he_custom/exclude:
    # those can contain private words. Match prepare_open_data.py defaults.
    defaults = {'he_custom.txt': 'מחשב\n',
                'exclude.txt': '# Optional local exclusions, one per line.\n'}
    for name, content in defaults.items():
        target = destination / name
        if not target.exists():
            target.write_text(content, encoding='utf-8')
    default_settings = source / 'typebridge-settings.json'
    if not (folder / default_settings.name).exists():
        shutil.copy2(default_settings, folder / default_settings.name)


def build_one(source, output_root, profile, *, commit, version):
    """Build one profile; preserve the other profile and any user data."""
    if profile not in PROFILES:
        raise ValueError('Unknown profile')
    output_root = Path(output_root).resolve()
    source = Path(source).resolve()
    folder = output_root / ('Public' if profile == 'public' else 'Diagnostics')
    folder.mkdir(parents=True, exist_ok=True)
    stage_root = output_root / '.staging'
    stage_root.mkdir(parents=True, exist_ok=True)
    stage = stage_root / profile
    if stage.exists():
        shutil.rmtree(stage)
    stage_sources(source, stage, profile)
    work = output_root / '.pyinstaller-work' / profile
    spec = output_root / '.pyinstaller-spec' / profile
    work.mkdir(parents=True, exist_ok=True)
    spec.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onefile', '--windowed', '--name', 'TypeBridge',
               '--distpath', str(folder), '--workpath', str(work),
               '--specpath', str(spec), '--add-data',
               str(stage / 'dictionary_packs') + ';dictionary_packs']
    if profile == 'public':
        for module in PUBLIC_EXCLUDED_MODULES:
            command.extend(('--exclude-module', module))
    command.append('typebridge_app.py')
    log = output_root / f'pyinstaller-{profile}.log'
    with log.open('w', encoding='utf-8') as stream:
        subprocess.run(command, cwd=stage, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)
    exe = folder / 'TypeBridge.exe'
    inventory = verify_frozen_profile(exe, profile)
    install_public_data(source, folder)
    report = frozen_smoke_test(exe, profile)
    manifest = {
        'profile': profile, 'version': version, 'source_commit': commit,
        'exe_sha256': sha256(exe), 'trace_text_capable': profile == 'diagnostics',
        'frozen_inventory': inventory,
        'self_test': report,
        'dictionary_sha256': {path.name: sha256(path)
                              for path in sorted((folder / 'data').iterdir())
                              if path.is_file()},
    }
    (folder / 'BUILD-PROFILE.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return exe, manifest


def frozen_smoke_test(exe, profile):
    """Even with BOTH flags present, Public cannot enable content tracing."""
    flags = (exe.parent / 'TypeBridge-debug.flag',
             exe.parent / 'TypeBridge-debug-words.flag')
    created = []
    with tempfile.TemporaryDirectory(prefix='typebridge-profile-test-') as work:
        try:
            for flag in flags:
                if not flag.exists():
                    flag.touch()
                    created.append(flag)
            destination = Path(work) / 'report.json'
            result = subprocess.run(
                [str(exe), '--self-test', '--self-test-report', str(destination)],
                cwd=exe.parent, timeout=120,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if not destination.is_file():
                raise ValueError(f'{profile}: frozen self-test report missing')
            report = json.loads(destination.read_text(encoding='utf-8'))
            if (result.returncode or not report.get('ok')
                    or report.get('build_profile') != profile
                    or report.get('word_trace_enabled') is not (profile == 'diagnostics')):
                raise ValueError(f'{profile}: frozen self-test/profile mismatch')
            return report
        finally:
            for flag in created:
                flag.unlink(missing_ok=True)


def build_both(source, output_root, *, commit, version):
    result = {}
    for profile in PROFILES:
        print(f'BUILD_PROFILE {profile}', flush=True)
        result[profile] = build_one(source, output_root, profile,
                                    commit=commit, version=version)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--source-dir', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--version', default='0.0.0-local')
    args = parser.parse_args()
    source = args.source_dir.resolve()
    output = args.output_dir.resolve()
    if source == output or output in source.parents:
        parser.error('Output must not replace source directory')
    git_root = Path(__file__).resolve().parent
    dirty = subprocess.check_output(['git', 'status', '--porcelain',
                                     '--untracked-files=normal'], cwd=git_root,
                                    text=True).strip()
    if dirty:
        parser.error('Commit all source changes before building both versioned profiles')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                     cwd=git_root, text=True).strip()
    result = build_both(source, output, commit=commit, version=args.version)
    for name, (path, manifest) in result.items():
        print(f'{name.upper()}_READY {path} sha256={manifest["exe_sha256"]}', flush=True)


if __name__ == '__main__':
    main()
