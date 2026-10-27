"""Literal expansion checks against in-memory input and withdrawn Tk windows only."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from snippets import Snippet, SnippetConfig, SnippetMatcher
from snippets_gui import SnippetsWindow
import test_word_cycle as fixtures
from test_app_controls import key
from typebridge_app import VK_BACK, VK_SPACE, WM_KEYUP, OWN_INPUT_MARKER


class RulesTests(unittest.TestCase):
    def test_atomic_roundtrip_and_no_accidental_default_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'snippets.json'
            self.assertEqual(SnippetConfig.load(path),SnippetConfig())
            config=SnippetConfig(True,(Snippet('//email','name@domain.com'),Snippet('brb','Сейчас вернусь\nתודה',False,False)))
            config.save(path); self.assertEqual(SnippetConfig.load(path),config)
            self.assertEqual([p.name for p in path.parent.iterdir()],['snippets.json'])

    def test_reject_duplicates_ambiguous_prefixes_and_bad_values(self):
        for config in (
            SnippetConfig(True,(Snippet('x','one'),Snippet('xy','two'))),
            SnippetConfig(True,(Snippet('ab','one',False),Snippet('AB','two'))),
            SnippetConfig(True,(Snippet('ab','one',True,False),Snippet('ab.','two'))),
            SnippetConfig(True,(Snippet('has space','one'),)),
            SnippetConfig(True,(Snippet('x',''),)),
            SnippetConfig('false',()),
            SnippetConfig(True,(Snippet('x','text',sensitive='false'),))):
            with self.assertRaises(ValueError): config.validate()

    def test_instant_boundary_case_and_blocked_midword(self):
        m=SnippetMatcher(SnippetConfig(True,(Snippet('//email','address'),Snippet('brb','Ответ',False,False))))
        results=[m.feed(c)[0] for c in '//email']
        self.assertTrue(all(r is None for r in results[:-1]))
        self.assertEqual((results[-1].delete,results[-1].text),(6,'address'))
        m.feed(' ')
        results=[m.feed(c)[0] for c in 'BRB!']
        self.assertEqual((results[-1].delete,results[-1].separator),(3,'!'))
        m.reset(); self.assertTrue(all(m.feed(c)[0] is None for c in 'https://email '))
        m.reset(); self.assertTrue(all(m.feed(c)[0] is None for c in '//EMAIL '))


class HookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.WordCycleTests.setUpClass()

    def setUp(self):
        self.f=fixtures.WordCycleTests('test_cycle_before_space_and_no_dictionary_match_required')
        self.f.setUp()
        self.hook=self.f.hook
        self.hook.snippets=SnippetMatcher(SnippetConfig(True,(
            Snippet('//email','name@domain.com'),Snippet('brb','Ответ',False,False))))

    def test_example_expands_before_space_without_layout_change_or_recursion(self):
        self.f.type('//email')
        self.assertEqual(self.f.text,'name@domain.com')
        self.assertEqual(self.f.language,'en')
        self.assertIsNone(self.hook.last_word)
        count,events,_=self.hook.user32.SendInput.call_args.args
        self.assertTrue(all(events[i].u.ki.dwExtraInfo==OWN_INPUT_MARKER for i in range(count)))
        self.f.type(' ghbdtn ')
        self.assertEqual(self.f.text,'name@domain.com привет ')

    def test_boundary_replacement_keeps_punctuation_and_case_insensitive(self):
        self.f.type('BRB.')
        self.assertEqual(self.f.text,'Ответ.')

    def test_prefix_miss_is_not_corrected_as_an_unrelated_tail(self):
        self.hook.snippets=SnippetMatcher(SnippetConfig(True,(Snippet('//email','x',True,False),)))
        self.f.type('//emailb ')
        self.assertEqual(self.f.text,'//emailb ')

    def test_pause_disable_focus_click_and_backspace_cancel_partial_trigger(self):
        self.hook.paused=True; self.f.type('//email ')
        self.assertEqual(self.f.text,'//email ')
        self.hook.paused=False; self.f.text=''; self.hook.clear_token()
        self.f.type('//ema'); self.f.focus=101; self.f.type('il')
        self.assertEqual(self.f.text,'//email')
        self.f.type(' '); self.f.type('//ema'); key(self.hook,VK_BACK); key(self.hook,VK_BACK,WM_KEYUP)
        self.f.text=self.f.text[:-1]; self.f.type('ail')
        self.assertNotIn('name@domain.com',self.f.text)
        self.f.type(' '); self.f.type('//ema'); self.hook.handle_mouse(0,0x0201,0); self.f.type('il')
        self.assertNotIn('name@domain.com',self.f.text)
        self.hook.snippets=SnippetMatcher(SnippetConfig(False,(Snippet('//email','x'),)))
        self.f.type(' //email')
        self.assertTrue(self.f.text.endswith('//email'))

    def test_persistent_partial_send_stops_after_one_suffix_attempt(self):
        self.f.type('//emai')
        self.hook.user32.SendInput.side_effect=lambda count,*args: 1
        self.assertEqual(key(self.hook,ord('L')),1)
        self.assertIn(ord('L'),self.hook.suppressed_keyups)
        self.assertTrue(self.hook.token_blocked)
        self.assertEqual(self.hook.user32.SendInput.call_count,2)
        self.assertIsNone(self.hook.last_word)

    def test_changed_focus_before_write_cannot_expand(self):
        self.f.type('//emai')
        original=self.hook.context
        calls=0
        def context():
            nonlocal calls
            calls+=1
            if calls==2: return None
            return original()
        self.hook.context=context
        self.assertEqual(key(self.hook,ord('L')),0)
        self.hook.user32.SendInput.assert_not_called()

    def test_changed_focus_after_snippet_preflight_cannot_delete_new_field(self):
        self.f.type('//emai')
        original = self.hook.context
        calls = 0
        def context():
            nonlocal calls
            calls += 1
            return original() if calls < 3 else None
        self.hook.context = context
        self.assertEqual(key(self.hook, ord('L')), 0)
        self.hook.user32.SendInput.assert_not_called()
        self.assertTrue(self.hook.token_blocked)

    def test_partial_snippet_does_not_replay_accepted_keydown_and_releases_it(self):
        self.f.type('//emai')
        calls = []
        def partial(count, events, size):
            calls.append([(events[i].u.ki.wVk, events[i].u.ki.dwFlags) for i in range(count)])
            return 1 if len(calls) in (1, 3) else 0
        self.hook.user32.SendInput.side_effect = partial
        self.assertEqual(key(self.hook, ord('L')), 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][0], (VK_BACK, 0))
        self.assertEqual(calls[2], [(VK_BACK, 2)])
        self.assertTrue(self.hook.token_blocked)
        self.assertIn(ord('L'), self.hook.suppressed_keyups)

    def test_multiline_template_uses_no_synthetic_enter_or_tab_commands(self):
        self.hook.snippets=SnippetMatcher(SnippetConfig(True,(Snippet('//m','Первая\nשנייה\ttext'),)))
        self.f.type('//m')
        self.assertEqual(self.f.text,'Первая\rשנייה\ttext')
        _,events,_=self.hook.user32.SendInput.call_args.args
        self.assertFalse(any(e.u.ki.wVk in (9,13) for e in events))


class GuiTests(unittest.TestCase):
    def test_crud_and_apply_multiline_rule_in_separate_window(self):
        root=tk.Tk(); root.withdraw()
        original_top=tk.Toplevel
        def hidden_top(parent):
            top=original_top(parent); top.withdraw(); return top
        try:
            with patch('snippets_gui.tk.Toplevel',side_effect=hidden_top), patch('snippets_gui.messagebox.showerror') as error:
                apply=Mock(); editor=SnippetsWindow(root,SnippetConfig(),apply)
                editor.enabled.set(True); editor.trigger.set('//email')
                editor.text.insert('1.0','name@domain.com\nСпасибо')
                self.assertTrue(editor.upsert()); self.assertTrue(editor.save())
                expected=SnippetConfig(True,(Snippet('//email','name@domain.com\nСпасибо'),))
                apply.assert_called_once_with(expected)
                editor.table.selection_set('0'); editor.select()
                self.assertEqual(editor.trigger.get(),'//email')
                editor.text.delete('1.0','end'); editor.text.insert('1.0','new@example.com')
                self.assertTrue(editor.save())
                self.assertEqual(apply.call_args.args[0].rules[0].text,'new@example.com')
                editor.table.selection_set('0'); editor.select(); editor.delete(); editor.save()
                self.assertEqual(apply.call_args.args[0].rules,())
                error.assert_not_called()
        finally: root.destroy()

if __name__=='__main__': unittest.main()
