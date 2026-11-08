"""R02 caret recovery: API fixtures only, no live UIA, hooks or input."""
from dataclasses import replace
import time
import unittest
from unittest.mock import Mock, patch

from caret_word_recovery import CaretWord, parse_caret_word, read_caret_word
from engine import LanguageEngine, LayoutMapper, source_data_dir
from password_fields import PasswordFieldGuard
from word_cycle import remember_word
from test_app_controls import key
import test_word_cycle as cycle_tests
from typebridge_app import FocusContext, VK_BACK, VK_SPACE, VK_PAUSE, WM_KEYUP


class CaretParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapper = LayoutMapper()

    def sample(self, before='גיאהז ', **updates):
        return dict(before=before, after='', at_start=True, element_id='uia:1,2', **updates)

    def test_exact_hebrew_word_maps_to_russian_and_keeps_spaces(self):
        word = parse_caret_word(self.sample(), self.mapper, 'he', 'uia:1,2')
        self.assertEqual(word.text, 'גיאהז')
        self.assertEqual(word.suffix, ' ')
        self.assertEqual(word.element_id, 'uia:1,2')
        self.assertEqual(self.mapper.word(list(word.keys), 'ru'), 'время')

    def test_previous_word_before_next_word_is_separated_by_spaces(self):
        sample = self.sample(before='גיאהז  ')
        sample['after'] = 'NEXT'
        # Default parser still rejects ambiguous changed-middle inputs.
        self.assertIsNone(parse_caret_word(sample, self.mapper, 'he', 'uia:1,2'))
        word = parse_caret_word(sample, self.mapper, 'he', 'uia:1,2',
                                allow_following_word=True)
        self.assertIsNotNone(word)
        self.assertEqual((word.text, word.suffix, word.caret_offset),
                         ('גיאהז', '  ', None))

    def test_unambiguous_punctuation_preserves_exact_suffix(self):
        for before, suffix in (
                ('PREFIX ghbdtn!  ', '!  '),
                ('PREFIX (ghbdtn)  ', ')  '),
                ('PREFIX ((ghbdtn))!!  ', '))!!  ')):
            with self.subTest(before=before):
                sample = {'before': before, 'after': ' SUFFIX',
                          'at_start': False, 'at_end': False, 'element_id': 'uia:1,2'}
                word = parse_caret_word(sample, self.mapper, 'en', 'uia:1,2',
                                        active_languages=('en', 'ru', 'he'),
                                        allow_following_word=True)
                self.assertIsNotNone(word)
                self.assertEqual((word.text, word.suffix, word.caret_offset),
                                 ('ghbdtn', suffix, None))

    def test_ambiguous_cross_layout_oem_is_not_trimmed_as_literal_punctuation(self):
        sample = {'before': 'PREFIX ghbdtn, ', 'after': ' SUFFIX',
                  'at_start': False, 'at_end': False, 'element_id': 'uia:1,2'}
        word = parse_caret_word(sample, self.mapper, 'en', 'uia:1,2',
                                active_languages=('en', 'ru', 'he'),
                                allow_following_word=True)
        self.assertIsNotNone(word)
        self.assertEqual(word.text, 'ghbdtn,')
        self.assertEqual(word.suffix, ' ')

    def test_incomplete_selected_or_unbounded_samples_are_rejected(self):
        samples = [self.sample(before='x'*82), self.sample(before='גיאהז'+' '*41),
                   self.sample(before='גיאהז\n'), self.sample(before='abc123'),
                   {'before':'גיאהז', 'after':'א', 'at_start':True,'element_id':'uia:1,2'},
                   {'before':'גיאהז','after':'','at_start':False,'element_id':'uia:1,2'},
                   {'before':'גיאהז','after':'','at_start':True,'element_id':'uia:other'}]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertIsNone(parse_caret_word(sample,self.mapper,'he','uia:1,2'))

    def test_reader_does_not_start_without_verified_identity_or_valid_context(self):
        ctx=FocusContext(1,2,3,'he','chrome.exe','fixture')
        with patch('uia_native_worker.get_uia_service') as service:
            self.assertIsNone(read_caret_word(ctx,self.mapper,'invalid:',lambda:True))
            self.assertIsNone(read_caret_word(ctx,self.mapper,'uia:1,2',lambda:False))
            service.assert_not_called()

    def test_changed_context_timeout_and_bad_reply_fail_closed(self):
        ctx=FocusContext(1,2,3,'he','chrome.exe','fixture')
        for outcome in (None, {}, {'before':42}, {'element_id':'uia:other'}):
            with self.subTest(outcome=outcome), patch('uia_native_worker.get_uia_service') as getter:
                getter.return_value.request.return_value = outcome
                self.assertIsNone(read_caret_word(ctx,self.mapper,'uia:1,2',lambda:True))
        with patch('uia_native_worker.get_uia_service') as getter:
            getter.return_value.request.return_value = dict(before='גיאהז ',after='',
                                                             at_start=True,at_end=True,element_id='uia:1,2')
            self.assertIsNone(read_caret_word(ctx,self.mapper,'uia:1,2',Mock(side_effect=[True,False])))

    def test_caret_diagnostic_is_reason_only_and_opt_in(self):
        ctx = FocusContext(1, 2, 3, 'en', 'chrome.exe', 'fixture')
        for result, expected in (
                ({'state': 'unavailable', 'reason': 'noncollapsed-selection'},
                 'noncollapsed-selection'),
                (None, 'service-unavailable-or-context-changed'),
                ({'before': 'tot', 'after': '', 'at_start': True,
                  'at_end': True, 'element_id': 'uia:1,2'}, None),
                ({'before': '123', 'after': '', 'at_start': True,
                  'at_end': True, 'element_id': 'uia:1,2'}, 'word-parser-refused')):
            with self.subTest(expected=expected):
                reasons = []
                with patch('uia_native_worker.get_uia_service') as getter:
                    getter.return_value.request.return_value = result
                    read_caret_word(ctx, self.mapper, '', lambda: True,
                                    simple_mode=True, on_refusal=reasons.append)
                    request = getter.return_value.request.call_args.args[0]
                    self.assertEqual(request['diagnostic'], True)
                    self.assertEqual(request['simple_mode'], True)
                    self.assertNotIn('tot', str(reasons))
                self.assertEqual(reasons, [] if expected is None else [expected])

    def test_helper_parses_without_executing_uia_or_reading_foreground(self):
        from uia_native_worker import read_native_caret
        self.assertNotIn('SetFocus', read_native_caret.__code__.co_names)
        self.assertNotIn('Select', read_native_caret.__code__.co_names)
        self.assertNotIn('SetValue', read_native_caret.__code__.co_names)


class CaretRecoveryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=LanguageEngine(source_data_dir(),conservative=False)

    def setUp(self):
        self.d=cycle_tests.WordCycleTests('test_cycle_before_space_and_no_dictionary_match_required')
        self.d.engine=self.engine
        self.d.setUp()
        self.d.language='he'
        self.h=self.d.hook
        # WordCycleTests intentionally models the no-UIA simple switcher.
        # This suite instead verifies strict password/UIA caret recovery;
        # never silently inherit its simple-mode admission bypass.
        self.h.simple_mode=False
        self.h.settings=replace(self.h.settings,manual_key='Scroll Lock',cycle_key='Pause')
        self.h._try_early=Mock(return_value=False)
        self.h._in_keyboard_callback=True
        self.h.thread_id=123
        self.h.user32.PostThreadMessageW.return_value=1
        self.h.user32.GetClassNameW.side_effect=lambda hwnd,buf,size:setattr(buf,'value','Chrome_RenderWidgetHostHWND')
        self.guard=PasswordFieldGuard(require_element_id=True)
        original=self.h.context
        self.h.password_guard=self.guard
        self.h.protection_scope=(90,100)
        self.h.context=lambda: (replace(original(),process_name='chrome.exe',password_revision=self.guard.snapshot.revision)
                                if self.guard.permit_revision(90,100) is not None else None)
        self.guard.revalidate=Mock(side_effect=lambda top,focus,rev:self.guard.permit_revision(top,focus)==rev)
        self.safe()
        self.h._tracked_password_revision=self.guard.snapshot.revision
        self.h.last_focus=100;self.h.last_language='he'

    def safe(self,state='safe',element='uia:1,2'):
        ticks=621355968000000000+int(time.time()*10000000)
        self.guard.ingest(f'90|100|{state}|{ticks}|checked|{element}')

    def type_hebrew(self,vks=(68,72,84,86,90)):
        for vk in vks:
            before=self.d.language
            if key(self.h,vk)!=1:
                self.d.text+=self.engine.mapper.char(vk,before,False)
            key(self.h,vk,WM_KEYUP)

    def read_fixture(self,context,mapper,element,valid, **_options):
        self.assertTrue(valid())
        # The real helper binds an initially unknown focused element to the
        # runtime ID it reads. Mirror that behavior without live UIA.
        identity = element or 'uia:1,2'
        return parse_caret_word({'before':self.d.text,'after':'','at_start':True,
                                 'element_id':identity},mapper,context.language,identity)

    def release(self):
        self.h._release_keyboard_job(self.h._keyboard_job_release[0])
        self.h.native_worker.join(3)
        self.assertFalse(self.h.native_worker.is_alive())
        self.assertFalse(self.h.native_queue.busy)

    def test_backspace_unknown_then_hebrew_word_is_corrected_on_space(self):
        key(self.h,VK_BACK);key(self.h,VK_BACK,WM_KEYUP)
        self.type_hebrew()
        self.assertEqual(self.h.keys,[])
        self.assertEqual(self.d.text,'גיאהז')
        with patch('typebridge_app.read_caret_word',side_effect=self.read_fixture) as reader:
            self.assertEqual(key(self.h,VK_SPACE),1)
            key(self.h,VK_SPACE,WM_KEYUP)
            reader.assert_not_called()  # No field read inside the hook.
            self.release()
        self.assertEqual(self.d.text,'время ')
        self.assertEqual(self.d.language,'ru')
        self.assertEqual(self.h.last_word.text,'время')

    def test_missing_first_letters_are_recovered_as_whole_word_on_space(self):
        self.safe('unknown')
        self.type_hebrew((68,72))
        self.safe()
        self.type_hebrew((84,86,90))
        with patch('typebridge_app.read_caret_word',side_effect=self.read_fixture):
            self.assertEqual(key(self.h,VK_SPACE),1)
            key(self.h,VK_SPACE,WM_KEYUP)
            self.release()
        self.assertEqual(self.d.text,'время ')

    def test_ordinary_tracked_word_does_not_need_caret_reader(self):
        self.type_hebrew()
        with patch('typebridge_app.read_caret_word') as reader:
            self.assertEqual(key(self.h,VK_SPACE),1)
            key(self.h,VK_SPACE,WM_KEYUP)
            self.release()
            reader.assert_not_called()
        self.assertEqual(self.d.text,'время ')

    def test_refused_auto_recovery_passes_one_space_and_unblocks_next_word(self):
        key(self.h,VK_BACK);key(self.h,VK_BACK,WM_KEYUP)
        self.type_hebrew()
        with patch('typebridge_app.read_caret_word',return_value=None):
            self.assertEqual(key(self.h,VK_SPACE),1)
            key(self.h,VK_SPACE,WM_KEYUP)
            self.release()
        self.assertEqual(self.d.text,'גיאהז ')
        self.assertFalse(self.h.token_blocked)
        self.type_hebrew()
        self.assertEqual(len(self.h.keys),5)
        with patch('typebridge_app.read_caret_word') as reader:
            self.assertEqual(key(self.h,VK_SPACE),1)
            key(self.h,VK_SPACE,WM_KEYUP)
            self.release()
            reader.assert_not_called()
        self.assertEqual(self.d.text,'גיאהז время ')

    def test_repeated_single_command_after_space_preserves_word(self):
        self.d.text='גיאהז '
        keys=tuple((vk,False) for vk in (68,72,84,86,90))
        self.h.last_word=remember_word(keys,'גיאהז','he',' ',
                                      self.h._word_scope(self.h.context()))
        original=self.h.last_word
        self.assertIs(self.h.last_word,original)
        with patch('typebridge_app.read_caret_word',side_effect=self.read_fixture) as reader:
            key(self.h,VK_PAUSE);key(self.h,VK_PAUSE,WM_KEYUP)
            self.release()
            # P0-D requires two fresh reads even when the word was tracked.
            self.assertEqual(reader.call_count, 2)
        self.assertEqual(self.d.text,'время ')

    def test_pause_recovers_untracked_word_after_space(self):
        self.d.text='גיאהז '
        self.h.keys=[];self.h.last_word=None
        with patch('typebridge_app.read_caret_word',side_effect=self.read_fixture) as reader:
            self.assertEqual(key(self.h,VK_PAUSE),1)
            key(self.h,VK_PAUSE,WM_KEYUP)
            self.release()
            # One initial recovery snapshot, one independently checked before
            # SendInput; never start three separate PowerShell processes.
            self.assertEqual(reader.call_count, 2)
        self.assertEqual(self.d.text,'время ')

    def test_empty_token_after_tracking_reset_recovers_on_space(self):
        self.d.text='גיאהז'
        self.h.keys=[];self.h.last_word=None;self.h.token_blocked=False
        with patch('typebridge_app.read_caret_word',side_effect=self.read_fixture):
            self.assertEqual(key(self.h,VK_SPACE),1)
            key(self.h,VK_SPACE,WM_KEYUP)
            self.release()
        self.assertEqual(self.d.text,'время ')

    def test_simple_mode_untracked_space_passes_once_without_uia(self):
        # Simple mode has NO caret recovery: one forwarded physical Space,
        # no injected duplicate, no UIA worker and no document mutation.
        self.h.simple_mode=True
        self.d.text='גיאהז'
        self.h.keys=[];self.h.last_word=None;self.h.token_blocked=False
        with patch('typebridge_app.read_caret_word') as reader:
            self.assertEqual(key(self.h,VK_SPACE),0)
            self.d.text+=' '  # Host receives the one forwarded physical Space.
            key(self.h,VK_SPACE,WM_KEYUP)
            reader.assert_not_called()
        self.assertEqual(self.d.text,'גיאהז ')
        self.h.user32.SendInput.assert_not_called()
        self.assertFalse(getattr(self.h,'native_queue',None) and self.h.native_queue.busy)

    def test_pause_before_space_can_recover_skipped_prefix(self):
        self.d.text='גיאהז';self.h._guard_skipped_token=True
        with patch('typebridge_app.read_caret_word',side_effect=self.read_fixture):
            self.assertEqual(key(self.h,VK_PAUSE),1)
            key(self.h,VK_PAUSE,WM_KEYUP)
            self.release()
        self.assertEqual(self.d.text,'время')

    def test_changed_password_field_after_read_never_receives_replacement(self):
        self.d.text='גיאהז '
        self.h.last_word=None
        def change(*args):
            word=self.read_fixture(*args)
            self.safe('password','uia:9,9')
            return word
        with patch('typebridge_app.read_caret_word',side_effect=change):
            key(self.h,VK_PAUSE);key(self.h,VK_PAUSE,WM_KEYUP)
            self.release()
        self.h.user32.SendInput.assert_not_called()
        self.assertEqual(self.d.text,'גיאהז ')

    def test_unsupported_provider_never_guesses_word(self):
        self.d.text='גיאהז '
        self.h.last_word=None
        with patch('typebridge_app.read_caret_word',return_value=None):
            key(self.h,VK_PAUSE);key(self.h,VK_PAUSE,WM_KEYUP)
            self.release()
        self.h.user32.SendInput.assert_not_called()

    def test_password_never_starts_caret_reader(self):
        self.safe('password')
        with patch('typebridge_app.read_caret_word') as reader:
            key(self.h,VK_PAUSE);key(self.h,VK_SPACE)
            reader.assert_not_called()
        self.h.user32.SendInput.assert_not_called()


if __name__=='__main__':
    unittest.main()
