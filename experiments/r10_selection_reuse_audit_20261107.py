"""Call existing selection paths with mocked OS APIs; never touches real input.

Diagnostic counterexamples, not tests requiring the old limitations to persist.
"""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path[:0]=[str(ROOT),str(HERE)]
from layout_quality_20261106 import FrozenMapper, KEYMAP, hashes
from clipboard_selection import copy_transaction
from early_native_edit import NativeEditBridge
from early_scintilla import ScintillaEarlyBridge
from quick_search import read_selection
from test_clipboard_selection import TransactionTests
from test_app_controls import make_hook, key
from word_cycle import remember_word
from typebridge_app import VK_BACK, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE
from caret_word_recovery import parse_caret_word


def current_word_packet_with_selection(mapper):
    hook=make_hook()
    hook.engine.mapper=mapper
    hook.engine.active_languages=('ru','en','he')
    hook.keys=[]
    hook.token_blocked=False
    hook.last_word=remember_word(mapper.keys_for('ghbdtn','en'),'ghbdtn','en','',
                                 hook._word_scope(hook.context()),whole=True)
    hook.user32.GetClassNameW.side_effect=lambda hwnd,buf,n:setattr(
        buf,'value','Chrome_RenderWidgetHostHWND')
    hook.user32.GetKeyboardLayout.return_value=mapper.layouts['en']
    hook._record_replay_layout=Mock(return_value=mapper.layouts['he'])
    state={'text':'PREFIX ghbdtn SUFFIX','anchor':7,'caret':13}
    def send(count,events,_size):
        for event in events:
            value=event.u.ki
            if value.dwFlags & KEYEVENTF_KEYUP:continue
            first,last=sorted((state['anchor'],state['caret']))
            if value.wVk==VK_BACK:
                if first==last:first=max(0,first-1)
                state['text']=state['text'][:first]+state['text'][last:]
                state['anchor']=state['caret']=first
            elif value.dwFlags & KEYEVENTF_UNICODE:
                state['text']=state['text'][:first]+chr(value.wScan)+state['text'][last:]
                state['anchor']=state['caret']=first+1
        return count
    hook.user32.SendInput.side_effect=send
    # Read the synthetic selection, never the user's desktop. This reader
    # refuses precisely when the in-memory document has a nonempty range;
    # it does not force a constant rejection regardless of state.
    def model_caret_reader(context, current_mapper, element_id, valid,
                           *, active_languages=None, allow_following_word=False):
        if not valid() or state['anchor'] != state['caret']:
            return None
        identity='uia:diagnostic'
        if element_id and element_id != identity:
            return None
        caret=state['caret']
        sample=dict(before=state['text'][:caret],after=state['text'][caret:],
                    at_start=True,at_end=True,element_id=identity)
        return parse_caret_word(sample,current_mapper,context.language,identity,
                                active_languages=active_languages,
                                allow_following_word=allow_following_word)
    with patch('typebridge_app.read_caret_word', side_effect=model_caret_reader):
        key(hook,0x13)
    return dict(before='PREFIX ghbdtn SUFFIX',after=state['text'],
                prefix_preserved=state['text'].startswith('PREFIX '),
                suffix_preserved=state['text'].endswith(' SUFFIX'))


def run():
    mapper=FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
    mapper.layouts={code:int(hkl,16) for code,hkl in mapper.layouts.items()}
    native,state,valid,target,clock,sleep=TransactionTests().fixture()
    selected=' \tghbdtn\r\n '
    native.text.return_value=selected
    preview=copy_transaction(native,target,valid,clock=clock,sleep=sleep)
    service=Mock()
    service.request.return_value={'state':'ok','text':selected}
    with patch('uia_native_worker.get_uia_service',return_value=service):
        uia_result=read_selection(SimpleNamespace(pid=1,top=10,focus=11))
    admission={}
    for label,text in [('small','hello'),('multiline','a\nb'),('tab','a\tb'),
                       ('emoji','🙂'),('long33','a'*33)]:
        bridge=NativeEditBridge.__new__(NativeEditBridge)
        bridge.off_hook=True
        bridge.pending_char=''
        bridge.last_snapshot=(b'abc',3)
        bridge.failure=''
        try:
            bridge.prepare(0,3,text.encode('utf-8'))
            admission[label]='accepted'
        except ValueError as error:
            admission[label]=str(error)  # Fixed native error code, static dummy input.
    scintilla=ScintillaEarlyBridge.__new__(ScintillaEarlyBridge)
    values={2137:65001,2140:0,2570:1,2187:0,2581:0,2583:0,2381:1,
            2008:6,2009:1,2006:10}
    scintilla.msg=Mock(side_effect=lambda command,*args:values[command])
    try:
        scintilla.snapshot()
        scintilla_admission='accepted'
    except ValueError as error:
        scintilla_admission=str(error)
    return dict(scope='Mocked existing paths only; no live fields, clipboard or input',
        copy_preview=dict(input=selected,output=preview.text,
            exact_whitespace=preview.text==selected,
            restore_called=native.restore.call_count==1),
        uia_search_reader=dict(exact_whitespace=uia_result==selected,
                              has_range_coordinates=not isinstance(uia_result,str)),
        native_word_bridge=admission,scintilla_word_bridge=scintilla_admission,
        stale_word_memory_with_selection=current_word_packet_with_selection(mapper),
        sha256=hashes([Path(__file__),KEYMAP,*[ROOT/name for name in
            ('clipboard_selection.py','quick_search.py','early_native_edit.py',
             'early_scintilla.py','typebridge_app.py','selection_cycle.py',
             'test_app_controls.py','test_clipboard_selection.py')]]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='sha256'},ensure_ascii=True))


if __name__=='__main__':main()
