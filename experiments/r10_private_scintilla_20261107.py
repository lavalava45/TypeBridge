"""Own native Scintilla controls on an INACTIVE Windows desktop.

No user windows, hooks, SendInput, clipboard, SetFocus, SwitchDesktop or HKL.
The DLL is read from the existing Python installation; never redistributed.
The normal Notepad++ identity gate is bypassed ONLY by this test subclass
which verifies the exact disposable child PID launched below.
"""
import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'experiments')]
from layout_quality_20261106 import FrozenMapper, KEYMAP, hashes
from scintilla_selection import ScintillaRangeTransport, ScintillaSelectionAdapter, MAX_BYTES
from selection_cycle import SelectionCycle, replace_selection


def apis():
    u=ctypes.WinDLL('user32',use_last_error=True)
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.GetCurrentThreadId.restype=w.DWORD
    k.LoadLibraryW.argtypes,k.LoadLibraryW.restype=[w.LPCWSTR],w.HMODULE
    u.GetThreadDesktop.argtypes,u.GetThreadDesktop.restype=[w.DWORD],w.HANDLE
    u.CreateDesktopW.argtypes=[w.LPCWSTR,w.LPCWSTR,ctypes.c_void_p,w.DWORD,w.DWORD,ctypes.c_void_p]
    u.CreateDesktopW.restype=w.HANDLE
    u.OpenDesktopW.argtypes,u.OpenDesktopW.restype=[w.LPCWSTR,w.DWORD,w.BOOL,w.DWORD],w.HANDLE
    u.SetThreadDesktop.argtypes,u.SetThreadDesktop.restype=[w.HANDLE],w.BOOL
    u.CloseDesktop.argtypes=[w.HANDLE]
    u.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,
        ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,ctypes.c_void_p]
    u.CreateWindowExW.restype=w.HWND
    u.DestroyWindow.argtypes=[w.HWND]
    u.GetMessageW.argtypes=[ctypes.POINTER(w.MSG),w.HWND,w.UINT,w.UINT]
    u.TranslateMessage.argtypes=[ctypes.POINTER(w.MSG)]
    u.DispatchMessageW.argtypes,u.DispatchMessageW.restype=[ctypes.POINTER(w.MSG)],ctypes.c_ssize_t
    u.PostThreadMessageW.argtypes=[w.DWORD,w.UINT,w.WPARAM,w.LPARAM]
    return u,k


def worker(dll):
    u,k=apis()
    tid=k.GetCurrentThreadId()
    original=u.GetThreadDesktop(tid)
    name='TypeBridge-R10-'+uuid.uuid4().hex
    desktop=u.CreateDesktopW(name,None,None,0,0x1FF,None)
    top=a=b=None
    try:
        if not desktop or not u.SetThreadDesktop(desktop):raise ctypes.WinError(ctypes.get_last_error())
        library=k.LoadLibraryW(str(dll))
        if not library:raise ctypes.WinError(ctypes.get_last_error())
        top=u.CreateWindowExW(0,'STATIC','R10 own inactive fixture',0,0,0,600,400,None,None,None,None)
        a=u.CreateWindowExW(0,'Scintilla','',0x40000000,0,0,500,180,top,None,library,None)
        b=u.CreateWindowExW(0,'Scintilla','',0x40000000,0,190,500,180,top,None,library,None)
        if not top or not a or not b:raise ctypes.WinError(ctypes.get_last_error())
        print(json.dumps(dict(desktop=name,top=int(top),a=int(a),b=int(b),thread=tid)),flush=True)
        def stop():
            sys.stdin.readline()
            u.PostThreadMessageW(tid,0x12,0,0)
        threading.Thread(target=stop,daemon=True).start()
        msg=w.MSG()
        while u.GetMessageW(ctypes.byref(msg),None,0,0)>0:
            u.TranslateMessage(ctypes.byref(msg));u.DispatchMessageW(ctypes.byref(msg))
    except Exception as error:
        print(json.dumps(dict(error=type(error).__name__,code=ctypes.get_last_error())),flush=True)
    finally:
        for hwnd in (b,a,top):
            if hwnd:u.DestroyWindow(hwnd)
        u.SetThreadDesktop(original)
        if desktop:u.CloseDesktop(desktop)


def probe(dll):
    mapper=FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
    u,k=apis()
    original=u.GetThreadDesktop(k.GetCurrentThreadId())
    child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker','--dll',str(dll)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
    desktop=None
    attached=False
    report=dict(desktop='private-inactive',hooks=False,SendInput=False,clipboard=False,
        focus_or_layout_changed=False,dll=str(dll),dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest(),cases=[])
    try:
        ready_queue=queue.Queue()
        threading.Thread(target=lambda:ready_queue.put(child.stdout.readline()),daemon=True).start()
        ready=json.loads(ready_queue.get(timeout=8))
        if 'error' in ready:raise RuntimeError(str(ready))
        desktop=u.OpenDesktopW(ready['desktop'],0,False,0x1FF)
        if not desktop or not u.SetThreadDesktop(desktop):raise ctypes.WinError(ctypes.get_last_error())
        attached=True
        class OwnedTransport(ScintillaRangeTransport):
            def __enter__(self):
                pid=w.DWORD()
                tid=self.u.GetWindowThreadProcessId(self.context.focus,ctypes.byref(pid))
                if pid.value!=child.pid or tid!=ready['thread']:
                    raise ValueError('not-the-owned-fixture')
                self.handle=self.k.OpenProcess(0x1038,False,child.pid)
                if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
                try:
                    self.bind_process(child.pid,tid)
                    self.check_identity()
                    self.ranges=[]
                    return self
                except Exception:
                    self.__exit__(None,None,None)
                    raise
            def read_range(self,first,last):
                self.ranges.append((first,last))
                return super().read_range(first,last)
        def transport(hwnd):
            return OwnedTransport(SimpleNamespace(top=ready['top'],focus=hwnd,process_name='own-fixture'))
        def seed(hwnd,text,anchor=0,caret=0):
            with transport(hwnd) as native:
                payload=text.encode('utf-8')+b'\0'
                address=native.k.VirtualAllocEx(native.handle,None,len(payload),0x3000,4)
                if not address:raise OSError('seed-allocation')
                try:
                    native.write_memory(address,payload)
                    native.msg(2171,0)  # SCI_SETREADONLY(false)
                    native.msg(2037,65001)
                    native.msg(2012,1)
                    native.msg(2181,0,address)
                    native.msg(2160,anchor,caret)
                    native.msg(2175)
                finally:
                    if not native.timeout:native.k.VirtualFreeEx(native.handle,address,0,0x8000)
        def read(native):
            length=native.msg(2006)
            return b''.join(native.read_range(first,min(first+MAX_BYTES,length))
                for first in range(0,length,MAX_BYTES)).decode('utf-8')
        def render(physical,lang):
            return ''.join(ch if ch.isspace() or self_keys is None else mapper.word(self_keys,lang)
                for ch in physical for self_keys in [mapper.keys_for(ch,'en')])
        def apply(cycle=None,source='en',active=('en','ru','he'),prepare_change=False):
            with transport(ready['a']) as native:
                adapter=ScintillaSelectionAdapter(native,('private',ready['a']),1,lambda:True)
                if cycle is None:cycle=SelectionCycle(adapter.snapshot(),source,mapper,active)
                if prepare_change:
                    prepare=adapter.prepare
                    def changed(plan):
                        prepare(plan);native.msg(2160,0,0)
                    adapter.prepare=changed
                result=replace_selection(cycle,adapter,lambda:True)
                snapshot=adapter.snapshot() if result.state=='verified' else None
                return result,cycle,snapshot,list(native.ranges)
        seed(ready['b'],'B_UNCHANGED')
        prefix,suffix='🙂 PREFIX ',' SUFFIX'
        physical='ghbdtn z,kj\r\n\takuo 🙂'
        for source in ('ru','en','he'):
            for target in ('ru','en','he'):
                if target==source:continue
                for backward in (False,True):
                    selected=render(physical,source)
                    first=len(prefix.encode('utf-8'));last=first+len(selected.encode('utf-8'))
                    anchor,caret=(last,first) if backward else (first,last)
                    original_text=prefix+selected+suffix
                    seed(ready['a'],original_text,anchor,caret)
                    order=(source,target,next(c for c in ('ru','en','he') if c not in (source,target)))
                    result,cycle,snapshot,ranges=apply(source=source,active=order)
                    wanted=prefix+render(physical,target)+suffix
                    with transport(ready['a']) as native:
                        assert result.state=='verified',result
                        assert read(native)==wanted
                        assert snapshot.text==render(physical,target)
                        assert (snapshot.anchor>snapshot.caret)==backward
                        native.msg(2176)
                        assert read(native)==original_text and native.msg(2174)==0
                        native.msg(2011)
                        assert read(native)==wanted
                    with transport(ready['b']) as native:assert read(native)=='B_UNCHANGED'
                    assert all(first==start and end-start<=MAX_BYTES for start,end in ranges)
                    report['cases'].append(dict(kind='six-directions',source=source,target=target,
                        backward=backward,selected_range_read=True,undo=True,redo=True,peer_unchanged=True))
        original_text=prefix+'Hello, ghbdtn\r\n\t🙂'+suffix
        first=len(prefix.encode());last=len((original_text[:-len(suffix)]).encode())
        seed(ready['a'],original_text,last,first)
        cycle=None
        history=[original_text]
        for _ in range(3):
            result,cycle,snapshot,_=apply(cycle=cycle)
            assert result.state=='verified',result
            with transport(ready['a']) as native:history.append(read(native))
        assert history[-1]==original_text
        with transport(ready['a']) as native:
            for wanted in reversed(history[:-1]):native.msg(2176);assert read(native)==wanted
            assert native.msg(2174)==0
            for wanted in history[1:]:native.msg(2011);assert read(native)==wanted
        report['cases'].append(dict(kind='three-cycles-three-undo-redo',passed=True))
        large_prefix='L'*100000
        selected='🙂'*1999+'a'
        first=len(large_prefix.encode());last=first+len(selected.encode())
        seed(ready['a'],large_prefix+selected+suffix,first,last)
        result,cycle,snapshot,ranges=apply()
        assert result.state=='verified' and snapshot.text=='🙂'*1999+'ф'
        assert all(start==first and end-start<=MAX_BYTES for start,end in ranges)
        with transport(ready['a']) as native:
            assert read(native)==large_prefix+'🙂'*1999+'ф'+suffix
            native.msg(2176);assert read(native)==large_prefix+selected+suffix
        report['cases'].append(dict(kind='2000-characters-100k-prefix',passed=True,selected_only=True))
        seed(ready['a'],prefix+'ghbdtn'+suffix,len(prefix.encode()),len((prefix+'ghbdtn').encode()))
        result,_,_,_=apply(prepare_change=True)
        assert result.state=='refused'
        with transport(ready['a']) as native:
            assert read(native)==prefix+'ghbdtn'+suffix and native.msg(2174)==0
        report['cases'].append(dict(kind='selection-change-before-commit',passed=True))
        report['passed']=len(report['cases'])
        report['sha256']=hashes([Path(__file__),ROOT/'scintilla_selection.py',ROOT/'selection_cycle.py',KEYMAP])
        return report
    finally:
        if attached:u.SetThreadDesktop(original)
        if desktop:u.CloseDesktop(desktop)
        try:child.communicate(input='exit\n',timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate();child.wait(timeout=3)  # ONLY our disposable child.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--dll',type=Path,default=Path(sys.executable).parent/'Lib/site-packages/pythonwin/scintilla.dll')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.worker:worker(args.dll);return
    if not args.output:parser.error('--output required')
    report=probe(args.dll.resolve())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'],desktop=report['desktop'],cases=report['cases'])))


if __name__=='__main__':main()
