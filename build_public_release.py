"""Build an isolated portable package from committed source and fresh open data."""
import argparse
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile


def run(*args, cwd=None):
    subprocess.run([str(a) for a in args],cwd=cwd,check=True)


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


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
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    archive=subprocess.check_output(['git','archive','--format=zip',commit],cwd=root)
    out.mkdir(parents=True)
    source=out/'source'; source.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        for name in z.namelist():
            if not (source/ name).resolve().is_relative_to(source.resolve()):
                raise ValueError('Unsafe source archive path')
        z.extractall(source)
    if any((source/'data').glob('*.txt')):
        raise ValueError('Source snapshot already contains dictionary text; refusing release build')
    run(sys.executable,source/'prepare_dictionaries.py',cwd=source)
    package=out/'portable'/'TypeBridge'; package.mkdir(parents=True)
    licenses=package/'licenses'; licenses.mkdir()
    for filename in ('LICENSE','README.md','README-RU.md','README-HE.md','THIRD-PARTY.md','typebridge-settings.json'):
        shutil.copy2(source/filename,package/filename)
    shutil.copytree(source/'data',package/'data')
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
    log=out/'pyinstaller.log'
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--windowed',
             '--name','TypeBridge','--distpath',str(package),'--workpath',str(out/'build'),
             '--add-data','data;data','typebridge_app.py']
    with log.open('w',encoding='utf-8') as stream:
        subprocess.run(command,cwd=source,stdout=stream,stderr=subprocess.STDOUT,check=True)
    # A hidden self-test only; no GUI event loop, migration or keyboard hooks.
    result=subprocess.run([str(package/'TypeBridge.exe'),'--self-test'],cwd=package,timeout=60,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: raise ValueError('Frozen self-test failed')
    manifest={'version':args.version,'source_commit':commit,'python':sys.version.split()[0],
              'packages':{n:metadata.version(n) for n in ('Pillow','psutil','pywin32','pyinstaller')},
              'dictionary_sources':json.loads((package/'data'/'SOURCES.json').read_text(encoding='utf-8')),
              'self_test_exit':result.returncode,
              'files':{p.relative_to(package).as_posix():sha(p) for p in sorted(package.rglob('*')) if p.is_file()}}
    (package/'BUILD-MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    destination=out/f'TypeBridge-{args.version}-windows-x64.zip'
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(package.rglob('*')):
            if path.is_file(): z.write(path,path.relative_to(package.parent))
    with zipfile.ZipFile(destination) as z:
        if z.testzip() is not None: raise ValueError('Corrupt release archive')
        for name in z.namelist():
            if any(part in ('settings.json','snippets.json','user_lexicon.json') for part in Path(name).parts):
                raise ValueError('Private user file in archive')
    (out/'SHA256SUMS.txt').write_text(f'{sha(destination)}  {destination.name}\n',encoding='ascii')
    shutil.copy2(package/'BUILD-MANIFEST.json',out/'BUILD-MANIFEST.json')
    print('RELEASE_READY',destination,flush=True)


if __name__=='__main__': main()
