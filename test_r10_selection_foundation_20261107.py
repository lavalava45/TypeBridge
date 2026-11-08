"""Selected-range protocol model; no editor, clipboard, OS input or live HKLs."""
from dataclasses import replace
import json
import unittest

from experiments.layout_quality_20261106 import FrozenMapper, KEYMAP
from selection_cycle import SelectionSnapshot, SelectionCycle, replace_selection, text_units


def index_at(text, position, unit):
    if unit == 'utf16':
        return len(text.encode('utf-16-le')[:position*2].decode('utf-16-le'))
    if unit == 'utf8':
        return len(text.encode('utf-8')[:position].decode('utf-8'))
    return position


class MemoryRangeEditor:
    """An ideal adapter contract, not proof of any real editor's Undo."""
    def __init__(self, text, prefix='🙂 PREFIX ', suffix=' SUFFIX', unit='codepoint', backward=False):
        self.text, self.unit = prefix+text+suffix, unit
        first, last = text_units(prefix,unit), text_units(prefix+text,unit)
        self.anchor, self.caret = (last,first) if backward else (first,last)
        self.scope, self.revision = ('A','field-A'), 1
        self.undo_stack, self.redo_stack = [], []
        self.write_started = False
        self.writes = 0
        self.undo_calls = 0

    def snapshot(self):
        first, last = sorted((self.anchor,self.caret))
        text = self.text[index_at(self.text,first,self.unit):index_at(self.text,last,self.unit)]
        return SelectionSnapshot(self.scope,self.revision,self.anchor,self.caret,text,self.unit)

    def prepare(self, plan):
        self.write_started = False

    def state(self):
        return self.text,self.anchor,self.caret

    def replace(self, plan):
        if self.snapshot() != plan.before:
            raise ValueError('range-changed')
        self.write_started = True
        self.writes += 1
        self.undo_stack.append(self.state())
        self.redo_stack.clear()
        first = index_at(self.text,plan.before.start,self.unit)
        end = index_at(self.text,max(plan.before.anchor,plan.before.caret),self.unit)
        self.text = self.text[:first] + plan.after.text + self.text[end:]
        self.anchor,self.caret = plan.after.anchor,plan.after.caret

    def undo(self):
        self.undo_calls += 1
        self.redo_stack.append(self.state())
        self.text,self.anchor,self.caret = self.undo_stack.pop()

    def redo(self):
        self.undo_stack.append(self.state())
        self.text,self.anchor,self.caret = self.redo_stack.pop()


class SelectionFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapper = FrozenMapper(json.loads(KEYMAP.read_text(encoding='utf-8')))

    def render(self, physical, language):
        return ''.join(char if char.isspace() else self.mapper.word(
            self.mapper.keys_for(char,'en'),language) for char in physical)

    def test_six_directions_keep_neighbors_and_exact_whitespace(self):
        physical = 'ghbdtn z,kj\r\n\takuo  '
        for source in ('ru','en','he'):
            for target in ('ru','en','he'):
                if source == target:
                    continue
                for unit in ('codepoint','utf16','utf8'):
                    with self.subTest(source=source,target=target,unit=unit):
                        original = self.render(physical,source)
                        editor = MemoryRangeEditor(original,unit=unit)
                        order=(source,target,next(c for c in ('ru','en','he') if c not in (source,target)))
                        cycle=SelectionCycle(editor.snapshot(),source,self.mapper,order)
                        outcome=replace_selection(cycle,editor,lambda:True)
                        self.assertEqual(outcome.state,'verified')
                        expected=self.render(physical,target)
                        self.assertEqual(editor.text,'🙂 PREFIX '+expected+' SUFFIX')
                        self.assertEqual(editor.snapshot().text,expected)
                        self.assertEqual(editor.writes,1)

    def test_repeated_cycle_preserves_original_keys_case_emoji_and_literals(self):
        original='Hello, z,kj\r\n\t🙂 é  '
        editor=MemoryRangeEditor(original,unit='utf8',backward=True)
        before=editor.state()
        cycle=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru','he'))
        for _ in range(3):
            self.assertEqual(replace_selection(cycle,editor,lambda:True).state,'verified')
            self.assertGreater(editor.anchor,editor.caret)
        self.assertEqual(editor.state(),before)

    def test_each_cycle_is_one_undo_action_and_redo_in_the_model(self):
        editor=MemoryRangeEditor('ghbdtn')
        before=editor.state()
        cycle=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru','he'))
        replace_selection(cycle,editor,lambda:True)
        after=editor.state()
        self.assertEqual(len(editor.undo_stack),1)
        editor.undo()
        self.assertEqual(editor.state(),before)
        editor.redo()
        self.assertEqual(editor.state(),after)

    def test_changed_selection_between_prepare_and_commit_never_writes(self):
        editor=MemoryRangeEditor('ghbdtn')
        cycle=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru','he'))
        editor.prepare=lambda _plan:setattr(editor,'caret',editor.anchor)
        outcome=replace_selection(cycle,editor,lambda:True)
        self.assertEqual(outcome.state,'refused')
        self.assertEqual(editor.writes,0)
        self.assertEqual(editor.text,'🙂 PREFIX ghbdtn SUFFIX')
        with self.assertRaises(ValueError):cycle.plan_next()

    def test_scope_or_revision_change_cannot_edit_a_new_recipient(self):
        for attribute,value in [('scope',('B','field-B')),('revision',2)]:
            editor=MemoryRangeEditor('ghbdtn')
            cycle=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru','he'))
            setattr(editor,attribute,value)
            self.assertEqual(replace_selection(cycle,editor,lambda:True).state,'refused')
            self.assertEqual(editor.writes,0)

    def test_partial_write_is_uncertain_without_retry_or_blind_undo(self):
        editor=MemoryRangeEditor('ghbdtn')
        cycle=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru','he'))
        def partial(plan):
            editor.write_started=True
            editor.writes+=1
            editor.text='partial model edit'
            raise TimeoutError('dummy provider detail')
        editor.replace=partial
        result=replace_selection(cycle,editor,lambda:True)
        self.assertEqual((result.state,result.reason),('uncertain','TimeoutError'))
        self.assertEqual(editor.writes,1)
        self.assertEqual(editor.undo_calls,0)
        self.assertTrue(cycle.closed)

    def test_missing_readback_does_not_advance_or_retry_cycle(self):
        editor=MemoryRangeEditor('ghbdtn')
        cycle=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru','he'))
        snapshot=editor.snapshot
        def readback():
            if editor.write_started:raise TimeoutError('readback')
            return snapshot()
        editor.snapshot=readback
        self.assertEqual(replace_selection(cycle,editor,lambda:True).state,'uncertain')
        self.assertEqual(cycle.language,'en')
        self.assertEqual(editor.writes,1)
        self.assertTrue(cycle.closed)

    def test_units_are_not_interchangeable_and_empty_is_distinct(self):
        with self.assertRaises(ValueError):
            SelectionSnapshot(('A',),1,0,1,'🙂','utf16')
        with self.assertRaises(ValueError):
            SelectionSnapshot(('A',),1,0,2001,'a'*2001,'codepoint')
        empty=SelectionSnapshot(('A',),1,3,3,'','codepoint')
        with self.assertRaisesRegex(ValueError,'empty-selection'):
            SelectionCycle(empty,'en',self.mapper,('en','ru'))

    def test_stale_plan_from_a_different_operation_cannot_advance_state(self):
        editor=MemoryRangeEditor('ghbdtn')
        first=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru'))
        second=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru'))
        plan=first.plan_next()
        with self.assertRaisesRegex(ValueError,'no-longer-current'):
            second.accept(plan,plan.after)

    def test_old_plan_is_stale_even_after_a_complete_three_layout_cycle(self):
        editor=MemoryRangeEditor('ghbdtn')
        cycle=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru','he'))
        old=cycle.plan_next()
        for _ in range(3):
            self.assertEqual(replace_selection(cycle,editor,lambda:True).state,'verified')
        self.assertEqual(cycle.snapshot,old.before)
        with self.assertRaisesRegex(ValueError,'no-longer-current'):
            cycle.accept(old,old.after)

    def test_failed_preflight_after_previous_success_is_not_a_new_partial_write(self):
        editor=MemoryRangeEditor('ghbdtn')
        cycle=SelectionCycle(editor.snapshot(),'en',self.mapper,('en','ru','he'))
        self.assertEqual(replace_selection(cycle,editor,lambda:True).state,'verified')
        def fail_read():raise TimeoutError('before-next-write')
        editor.snapshot=fail_read
        self.assertEqual(replace_selection(cycle,editor,lambda:True).state,'refused')
        self.assertEqual(editor.writes,1)


if __name__ == '__main__':
    unittest.main()
