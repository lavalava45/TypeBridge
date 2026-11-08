"""Scintilla range protocol with fake native transport; no live windows."""
import ctypes
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from experiments.layout_quality_20261106 import FrozenMapper, KEYMAP
from selection_cycle import SelectionCycle, replace_selection
from scintilla_selection import (ScintillaRangeTransport, ScintillaSelectionAdapter,
                                 TextRange, MAX_BYTES)


class ModelTransport:
    def __init__(self):
        self.data='🙂 PREFIX ghbdtn\r\n SUFFIX'.encode('utf-8')
        self.anchor=len('🙂 PREFIX '.encode('utf-8'))
        self.caret=self.anchor+len('ghbdtn\r\n'.encode('utf-8'))
        self.doc=123
        self.calls=[]
        self.reads=[]
        self.writes=0
        self.target_addr=999
        self.readonly=0
        self.selection_mode=0

    def msg(self, command, wp=0, lp=0):
        self.calls.append(command)
        value={2137:65001,2140:self.readonly,2570:1,2423:self.selection_mode,
               2372:0,2581:0,2583:0,2019:1,2357:self.doc,
               2009:self.anchor,2008:self.caret,2006:len(self.data)}
        if command in value:return value[command]
        if command==2190:self.first=wp
        elif command==2192:self.last=wp
        elif command in (2728,2730):pass
        elif command==2191:return self.first
        elif command==2193:return self.last
        elif command==2194:
            self.writes+=1
            assert wp==len(self.payload) and lp==self.target_addr
            self.data=self.data[:self.first]+self.payload+self.data[self.last:]
            return wp
        elif command==2160:self.anchor,self.caret=wp,lp
        else:raise AssertionError(f'Unexpected native message {command}')
        return 0

    def read_range(self, first, last):
        self.reads.append((first,last))
        return self.data[first:last]

    def prepare_text(self, text):
        self.payload=text.encode('utf-8')


class ScintillaSelectionTests(unittest.TestCase):
    def fixture(self):
        transport=ModelTransport()
        adapter=ScintillaSelectionAdapter(transport,('A',),1,lambda:True)
        mapper=FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))
        cycle=SelectionCycle(adapter.snapshot(),'en',mapper,('en','ru','he'))
        return transport,adapter,cycle

    def test_range_write_keeps_whitespace_neighbors_and_selection(self):
        transport,adapter,cycle=self.fixture()
        result=replace_selection(cycle,adapter,lambda:True)
        self.assertEqual(result.state,'verified')
        self.assertEqual(transport.data,'🙂 PREFIX привет\r\n SUFFIX'.encode('utf-8'))
        self.assertEqual(adapter.snapshot().text,'привет\r\n')
        self.assertEqual(transport.writes,1)
        self.assertNotIn(2182,transport.calls)  # No whole document read.
        self.assertNotIn(2161,transport.calls)  # No unbounded selection copy.
        self.assertNotIn(2078,transport.calls)  # REPLACETARGET owns its UndoGroup.
        self.assertNotIn(2079,transport.calls)

    def test_document_swap_at_same_window_refuses_without_editing(self):
        transport,adapter,cycle=self.fixture()
        transport.doc=124
        self.assertEqual(replace_selection(cycle,adapter,lambda:True).state,'refused')
        self.assertEqual(transport.writes,0)

    def test_readonly_and_rectangular_selection_are_not_mutated(self):
        for attribute in ('readonly','selection_mode'):
            transport,adapter,cycle=self.fixture()
            setattr(transport,attribute,1)
            self.assertEqual(replace_selection(cycle,adapter,lambda:True).state,'refused')
            self.assertEqual(transport.writes,0)

    def test_selection_change_during_prepare_refuses_without_editing(self):
        transport,adapter,cycle=self.fixture()
        prepare=transport.prepare_text
        def changed(text):
            prepare(text)
            transport.caret=transport.anchor
        transport.prepare_text=changed
        self.assertEqual(replace_selection(cycle,adapter,lambda:True).state,'refused')
        self.assertEqual(transport.writes,0)

    def test_lost_reply_after_native_write_does_not_repeat_or_open_undo_group(self):
        transport,adapter,cycle=self.fixture()
        msg=transport.msg
        def lost(command,wp=0,lp=0):
            answer=msg(command,wp,lp)
            if command==2194:raise TimeoutError('injected-after-write')
            return answer
        transport.msg=lost
        self.assertEqual(replace_selection(cycle,adapter,lambda:True).state,'uncertain')
        self.assertEqual(transport.writes,1)
        self.assertTrue(cycle.closed)
        self.assertNotIn(2078,transport.calls)

    def test_remote_range_request_has_fixed_bounds_when_selection_grows(self):
        # Exercise the real ABI serialization/read_range against a LOCAL fake
        # kernel buffer; no remote process or actual selection is involved.
        bridge=ScintillaRangeTransport.__new__(ScintillaRangeTransport)
        bridge.timeout=False
        storage=ctypes.create_string_buffer(ctypes.sizeof(TextRange)+2*(MAX_BYTES+1))
        bridge.remote=ctypes.addressof(storage)
        bridge.handle=1
        def write(_handle,address,data,size,count):
            ctypes.memmove(address,data,size);count._obj.value=size;return True
        def read(_handle,address,data,size,count):
            ctypes.memmove(data,address,size);count._obj.value=size;return True
        bridge.k=SimpleNamespace(WriteProcessMemory=write,ReadProcessMemory=read)
        calls=[]
        def message(command,wp,lp):
            # Independent receiver layout from Sci_TextRangeFull, rather than
            # reusing the sender class (which concealed the legacy ABI bug).
            self.assertEqual(ctypes.sizeof(TextRange),24)
            first,last,address=struct.unpack('<qqQ',ctypes.string_at(lp,24))
            calls.append((command,first,last))
            self.assertEqual(address,bridge.remote+24)
            payload=b'abcdef'[first:last]+b'\0'
            ctypes.memmove(address,payload,len(payload))
            return last-first
        bridge.msg=message
        self.assertEqual(bridge.read_range(1,4),b'bcd')
        self.assertEqual(calls,[(2039,1,4)])
        with self.assertRaises(ValueError):bridge.read_range(0,MAX_BYTES+1)
        self.assertEqual(len(calls),1)

    def test_unsupported_full_range_never_falls_back_to_legacy_pointer_message(self):
        bridge=ScintillaRangeTransport.__new__(ScintillaRangeTransport)
        bridge.timeout=False
        bridge.allocate=Mock(return_value=1024)
        bridge.remote=1000
        bridge.write_memory=Mock()
        bridge.msg=Mock(return_value=0)  # Old DLL does not implement FULL.
        with self.assertRaisesRegex(ValueError,'incomplete-range'):
            bridge.read_range(1,4)
        bridge.msg.assert_called_once_with(2039,0,1000)

    def test_old_header_reconstructs_the_crash_address_without_native_calls(self):
        # Exact layout mismatch observed in the user's dump: cpMin becomes
        # last<<32, cpMax becomes our output pointer, lpstrText becomes NULL.
        old=struct.pack('<iiQ',0,16,0x3290010)+bytes(8)
        first,last,pointer=struct.unpack('<qqQ',old)
        self.assertEqual(first,0x1000000000)
        self.assertEqual(pointer,0)
        self.assertEqual((last-first)&0xFFFFFFFFFFFFFFFF,0xFFFFFFF003290010)

    def test_timed_out_transport_never_reuses_pending_remote_buffers(self):
        bridge=ScintillaRangeTransport.__new__(ScintillaRangeTransport)
        bridge.timeout=True
        bridge.check_identity=Mock()
        bridge.k=Mock()
        for operation in (lambda:bridge.allocate(),
                          lambda:bridge.write_memory(123,b'text'),
                          lambda:bridge.msg(2194,4,123)):
            with self.assertRaisesRegex(TimeoutError,'faulted'):operation()
        bridge.check_identity.assert_not_called()
        bridge.k.WriteProcessMemory.assert_not_called()
        bridge.k.VirtualAllocEx.assert_not_called()

    def test_closed_transport_does_not_access_a_freed_remote_buffer(self):
        bridge=ScintillaRangeTransport.__new__(ScintillaRangeTransport)
        bridge.closed=True
        bridge.timeout=False
        bridge.k=Mock()
        with self.assertRaisesRegex(ValueError,'closed'):bridge.read_range(0,3)
        with self.assertRaisesRegex(ValueError,'closed'):bridge.msg(2194,3,123)
        bridge.k.WriteProcessMemory.assert_not_called()


if __name__=='__main__':unittest.main()
