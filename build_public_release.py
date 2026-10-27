"""Build an isolated portable package from committed source and fresh open data."""
import argparse
import hashlib
from importlib import metadata
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

from build_profiles import build_both, verify_frozen_profile


def run(*args, cwd=None, env=None):
    subprocess.run([str(a) for a in args],cwd=cwd,env=env,check=True)


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def require_clean_source_snapshot(root):
    """Never label git archive HEAD as a snapshot of uncommitted fixes."""
    dirty = subprocess.check_output(
        ['git', 'status', '--porcelain', '--untracked-files=normal'],
        cwd=root, text=True)
    if dirty.strip():
        raise RuntimeError(
            'Public release refused: the working tree contains uncommitted or '
            'untracked files. git archive HEAD would silently omit current '
            'TypeBridge fixes. Commit and review the complete source first.')


def run_isolated_tests(source, env):
    """Run each unittest module in its own process to bound dictionary RAM use."""
    modules = sorted(path.stem for path in source.glob('test_*.py'))
    if not modules:
        raise RuntimeError('Public release refused: no unittest modules in source snapshot')
    for number, module in enumerate(modules, 1):
        print(f'RELEASE_TEST [{number}/{len(modules)}] {module}', flush=True)
        run(sys.executable, '-m', 'unittest', module, '-q', cwd=source, env=env)
    return modules


def check_private_archive_entries(archive, *, level=0):
    """Fail closed on private artifacts, including the embedded source ZIP."""
    if level > 1:
        raise ValueError('Unexpected nested release archive')
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if item.is_dir():
                continue
            parts = Path(item.filename.replace('\\', '/')).parts
            lowered = [p.casefold() for p in parts]
            if (any(p in ('typebridge-data', 'journal', '__pycache__', '.git')
                    for p in lowered[:-1])
                    or any(p in ('settings.json', 'snippets.json', 'user_lexicon.json',
                                 '.env') for p in lowered)
                    or lowered[-1].endswith(('.flag', '.log', '.jsonl', '.sqlite3',
                                             '.sqlite3-wal', '.sqlite3-shm', '.sqlite3-journal'))):
                raise ValueError('Private data in release archive: ' + item.filename)
            if Path(item.filename).name.casefold() == 'typebridge-source.zip':
                if level:
                    raise ValueError('Recursive source archive forbidden')
                if item.file_size > 100_000_000:
                    raise ValueError('Unexpectedly large source archive')
                check_private_archive_entries(io.BytesIO(z.read(item)), level=1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True,help='New directory outside the repository')
    parser.add_argument('--version',default='0.1.0-alpha.1')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    out=args.output_dir.resolve()
    if out.exists() or out==root or root in out.parents:
        parser.error('Output must be a new directory outside the repository')
    if any(c not in '0123456789abcdefghijklmnopqrstuvwxyz.-' for c in args.version):
        parser.error('Invalid version')
    require_clean_source_snapshot(root)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    archive=subprocess.check_output(['git','archive','--format=zip',commit],cwd=root)
    check_private_archive_entries(io.BytesIO(archive), level=1)
    out.mkdir(parents=True)
    source=out/'source'; source.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        for name in z.namelist():
            if not (source/ name).resolve().is_relative_to(source.resolve()):
                raise ValueError('Unsafe source archive path')
        z.extractall(source)
    if any((source/'data').glob('*.txt')):
        raise ValueError('Source snapshot already contains dictionary text; refusing release build')
    run(sys.executable,source/'prepare_open_data.py','--output-dir',source/'data',cwd=source)
    # Release gate: all background regressions on public data, with a fresh
    # process per module so large dictionary/early-index fixtures cannot
    # accumulate across the entire test suite on the Windows CI runner.
    tested_modules = run_isolated_tests(
        source, {**os.environ, 'TYPEBRIDGE_DICTIONARY_TEST_DIR':str(source/'data')})
    # Fail visibly before freezing if the runner lacks the required layouts/data.
    run(sys.executable,source/'typebridge_app.py','--self-test',cwd=source)
    package=out/'portable'/'TypeBridge'; package.mkdir(parents=True)
    licenses=package/'licenses'; licenses.mkdir()
    for filename in ('LICENSE','README.md','README-RU.md','README-HE.md','THIRD-PARTY.md','typebridge-settings.json'):
        shutil.copy2(source/filename,package/filename)
    shutil.copytree(source/'data',package/'data')
    shutil.copytree(source/'dictionary_packs',package/'dictionary_packs')
    (package/'TypeBridge-source.zip').write_bytes(archive)
    for name in ('Pillow','psutil','pywin32','pyinstaller'):
        dist=metadata.distribution(name)
        found=0
        for relative in dist.files or []:
            if not any(k in relative.name.lower() for k in ('license','copying','notice','copyright')):
                continue
            relative_path=Path(str(relative))
            if relative_path.is_absolute() or '..' in relative_path.parts: continue
            path=Path(dist.locate_file(relative))
            if not path.is_file(): continue
            target=licenses/name/relative_path
            target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target); found+=1
        if not found: raise ValueError('Missing license notices for '+name)
    shutil.copy2(Path(sys.base_prefix)/'LICENSE.txt',licenses/'Python-LICENSE.txt')
    shutil.copy2(Path(sys.base_prefix)/'tcl'/'tk8.6'/'license.terms',licenses/'Tk-license.terms')
    for url,name in (
        ('https://raw.githubusercontent.com/tcltk/tcl/core-8-6-branch/license.terms','Tcl-license.terms'),
        ('https://creativecommons.org/licenses/by-sa/4.0/legalcode.txt','CC-BY-SA-4.0.txt')):
        request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 TypeBridge release builder'})
        with urllib.request.urlopen(request,timeout=30) as stream: raw=stream.read()
        if len(raw)<1000: raise ValueError('Unexpectedly short license: '+name)
        (licenses/name).write_bytes(raw)
    # Build BOTH profiles, but only copy the audited Public EXE into the ZIP.
    # Diagnostics stays under the private build output and is never uploaded
    # by release.yml. Neither profile reuses the other's Python analysis.
    results = build_both(source, out/'profiles', commit=commit, version=args.version)
    public_exe, public_manifest = results['public']
    shutil.copy2(public_exe, package/'TypeBridge.exe')
    shutil.copy2(public_exe.parent/'BUILD-PROFILE.json', package/'BUILD-PROFILE.json')
    verify_frozen_profile(package/'TypeBridge.exe', 'public')
    # A hidden self-test only; no GUI event loop, migration or keyboard hooks.
    report_path = out/'self-test.json'
    result=subprocess.run([str(package/'TypeBridge.exe'),'--self-test',
                           '--self-test-report',str(report_path)],cwd=package,timeout=60,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if (result.returncode or not report.get('ok')
            or report.get('build_profile') != 'public'
            or report.get('word_trace_enabled') is not False):
        raise ValueError('Frozen self-test failed: '+json.dumps(report,ensure_ascii=False))
    manifest={'version':args.version,'source_commit':commit,'python':sys.version.split()[0],
              'packages':{n:metadata.version(n) for n in ('Pillow','psutil','pywin32','pyinstaller')},
              'dictionary_sources':json.loads((package/'data'/'SOURCES.json').read_text(encoding='utf-8')),
              'russian_sources':json.loads((package/'data'/'RU-SOURCES.json').read_text(encoding='utf-8')),
              'frequency_sources':json.loads((package/'data'/'FREQUENCY-SOURCES.json').read_text(encoding='utf-8')),
              'self_test_exit':result.returncode,
              'self_test_report':report,
              'unittest_suite':'passed',
              'unittest_module_count':len(tested_modules),
              'files':{p.relative_to(package).as_posix():sha(p) for p in sorted(package.rglob('*')) if p.is_file()}}
    (package/'BUILD-MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    destination=out/f'TypeBridge-{args.version}-windows-x64.zip'
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(package.rglob('*')):
            if path.is_file(): z.write(path,path.relative_to(package.parent))
    with zipfile.ZipFile(destination) as z:
        if z.testzip() is not None: raise ValueError('Corrupt release archive')
    check_private_archive_entries(destination)
    (out/'SHA256SUMS.txt').write_text(f'{sha(destination)}  {destination.name}\n',encoding='ascii')
    shutil.copy2(package/'BUILD-MANIFEST.json',out/'BUILD-MANIFEST.json')
    print('RELEASE_READY',destination,flush=True)


if __name__=='__main__': main()
