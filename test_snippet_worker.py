"""Off-hook snippet transport: fake Win32 only, no actual keyboard or windows."""
import threading
import unittest
from unittest.mock import Mock, patch
from dataclasses import replace

from test_app_controls import make_hook, key
from snippets import Snippet, SnippetConfig, SnippetMatcher
from typebridge_app import WM_KEYUP


class SnippetWorkerTests(unittest.TestCase):
    def fixture(self, text='hello'):
        hook = make_hook()
        hook._in_keyboard_callback = True
        hook._browser_field_revision = 0
        hook.thread_id = 123
        hook.user32.PostThreadMessageW.return_value = 1
        hook.user32.GetKeyState.return_value = 0
        hook.native_pending_recovery = None
        hook.mouse_hook = None
        hook.engine.mapper.char = lambda vk, *a: chr(vk).lower()
        hook.snippets = SnippetMatcher(SnippetConfig(True, (Snippet('x', text),)))
        return hook

    def release(self, hook):
        serial = hook.user32.PostThreadMessageW.call_args.args[2]
        hook._release_keyboard_job(serial)
        hook.native_worker.join(2)
        self.assertFalse(hook.native_worker.is_alive())

    @staticmethod
    def packet(events):
        return [(e.u.ki.wVk, e.u.ki.wScan, e.u.ki.dwFlags) for e in events]

    def test_long_packet_is_not_built_or_sent_until_callback_release(self):
        import typebridge_app
        hook = self.fixture('a'*2000)
        caller = threading.get_ident()
        threads = []
        def send(count, *args):
            threads.append(threading.get_ident())
            self.assertTrue(hook._native_is_off_hook())
            return count
        hook.user32.SendInput.side_effect = send
        with patch('typebridge_app.unicode_inputs', wraps=typebridge_app.unicode_inputs) as build:
            self.assertEqual(key(hook, ord('X')), 1)
            build.assert_not_called()
            hook.user32.SendInput.assert_not_called()
            self.release(hook)
        self.assertEqual(hook.user32.SendInput.call_count, 1)
        self.assertEqual(hook.user32.SendInput.call_args.args[0], 4000)
        self.assertNotEqual(threads, [caller])

    def test_trigger_recognition_occurs_once_and_queued_tail_is_replayed_after_template(self):
        hook = self.fixture()
        packets = []
        hook.user32.SendInput.side_effect = lambda n, events, *a: (packets.append(self.packet(events)), n)[1]
        with patch.object(hook.snippets, 'feed', wraps=hook.snippets.feed) as feed:
            self.assertEqual(key(hook, ord('X')), 1)
            key(hook, ord('X'), WM_KEYUP)
            key(hook, ord('C'))
            key(hook, ord('C'), WM_KEYUP)
            self.release(hook)
            feed.assert_called_once_with('x')
        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[1], [(ord('C'), 0, 0), (ord('C'), 0, 2)])
        self.assertFalse(any(vk == ord('X') for p in packets for vk, scan, flags in p))

    def test_zero_submission_replays_only_original_trigger_and_keyup(self):
        hook = self.fixture()
        packets = []
        def send(n, events, *args):
            packets.append(self.packet(events))
            return 0 if len(packets) == 1 else n
        hook.user32.SendInput.side_effect = send
        key(hook, ord('X')); key(hook, ord('X'), WM_KEYUP)
        self.release(hook)
        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[1], [(ord('X'), 0, 0), (ord('X'), 0, 2)])

    def test_partial_submission_never_replays_accepted_prefix_or_original_trigger(self):
        hook = self.fixture()
        packets = []
        def send(n, events, *args):
            packets.append(self.packet(events))
            return 0 if len(packets) == 2 else 1
        hook.user32.SendInput.side_effect = send
        key(hook, ord('X')); key(hook, ord('X'), WM_KEYUP)
        self.release(hook)
        self.assertEqual(len(packets), 3)  # packet, unaccepted suffix once, release only
        self.assertEqual(packets[2], [(0, ord('h'), 6)])
        self.assertFalse(any(vk == ord('X') for p in packets for vk, scan, flags in p))

    def test_exception_after_accepted_packet_does_not_append_original_trigger(self):
        hook = self.fixture()
        def emit(kind, message):
            if kind == 'ok':
                raise RuntimeError('synthetic post-send failure')
        hook.emit.side_effect = emit
        key(hook, ord('X')); key(hook, ord('X'), WM_KEYUP)
        self.release(hook)
        self.assertEqual(hook.user32.SendInput.call_count, 1)
        self.assertTrue(hook.native_edit_disabled)

    def test_new_field_or_dom_revision_cancels_without_editing_the_new_target(self):
        for change in ('focus', 'revision'):
            with self.subTest(change=change):
                hook = self.fixture()
                key(hook, ord('X'))
                if change == 'focus':
                    original = hook.context()
                    hook.context = lambda: replace(original, focus=original.focus+1)
                else:
                    hook._browser_field_revision += 1
                self.release(hook)
                hook.user32.SendInput.assert_not_called()

    def test_replaced_snippet_configuration_forwards_literal_key_in_same_field(self):
        hook = self.fixture()
        key(hook, ord('X')); key(hook, ord('X'), WM_KEYUP)
        hook.snippets = SnippetMatcher(SnippetConfig())
        self.release(hook)
        self.assertEqual(hook.user32.SendInput.call_count, 1)
        self.assertEqual(self.packet(hook.user32.SendInput.call_args.args[1]),
                         [(ord('X'), 0, 0), (ord('X'), 0, 2)])

    def test_scheduler_failure_never_builds_packet_or_waits_inline(self):
        hook = self.fixture()
        hook.user32.PostThreadMessageW.return_value = 0
        with patch('typebridge_app.unicode_inputs') as build:
            self.assertEqual(key(hook, ord('X')), 0)
        build.assert_not_called()
        hook.user32.SendInput.assert_not_called()

    def test_abort_while_preparing_packet_cancels_before_send(self):
        import typebridge_app
        hook = self.fixture()
        original = typebridge_app.unicode_inputs
        def abort(text):
            hook.native_queue.abort('external-injected-input')
            return original(text)
        key(hook, ord('X'))
        with patch('typebridge_app.unicode_inputs', side_effect=abort):
            self.release(hook)
        hook.user32.SendInput.assert_not_called()

    def test_immediate_and_separator_modes_keep_the_correct_deletion_span(self):
        for immediate in (True, False):
            with self.subTest(immediate=immediate):
                hook = self.fixture()
                hook.snippets = SnippetMatcher(SnippetConfig(True, (Snippet('xx', 'hello', immediate=immediate),)))
                self.assertEqual(key(hook, ord('X')), 0)
                key(hook, ord('X'), WM_KEYUP)
                if not immediate:
                    self.assertEqual(key(hook, ord('X')), 0)
                    key(hook, ord('X'), WM_KEYUP)
                    self.assertEqual(key(hook, 32), 1)
                else:
                    self.assertEqual(key(hook, ord('X')), 1)
                hook.user32.SendInput.assert_not_called()
                self.release(hook)
                packet = self.packet(hook.user32.SendInput.call_args.args[1])
                self.assertEqual(sum(vk == 8 and flags == 0 for vk, scan, flags in packet),
                                 1 if immediate else 2)
                text = ''.join(chr(scan) for vk, scan, flags in packet if flags == 4)
                self.assertEqual(text, 'hello' if immediate else 'hello ')

    def test_multiline_and_explicit_enter_keep_existing_packet_semantics(self):
        hook = self.fixture('line1\nline2\tend')
        key(hook, ord('X'))
        self.release(hook)
        packet = self.packet(hook.user32.SendInput.call_args.args[1])
        self.assertFalse(any(vk in (9, 13) for vk, scan, flags in packet))
        text = ''.join(chr(scan) for vk, scan, flags in packet if flags == 4)
        self.assertEqual(text, 'line1\rline2\tend')
        hook = self.fixture()
        hook.snippets = SnippetMatcher(SnippetConfig(True, (Snippet('x', 'hello', immediate=False),)))
        key(hook, ord('X')); key(hook, ord('X'), WM_KEYUP)
        self.assertEqual(key(hook, 13), 1)
        key(hook, 13, WM_KEYUP)
        self.release(hook)
        self.assertEqual(hook.user32.SendInput.call_count, 1)
        packet = self.packet(hook.user32.SendInput.call_args.args[1])
        self.assertEqual([e for e in packet if e[0] == 13], [(13, 0, 0), (13, 0, 2)])

    def test_queued_space_does_not_disable_the_next_immediate_template(self):
        hook = self.fixture()
        self.assertEqual(key(hook, ord('X')), 1)
        key(hook, ord('X'), WM_KEYUP)
        key(hook, 32); key(hook, 32, WM_KEYUP)
        self.release(hook)
        self.assertEqual(key(hook, ord('X')), 1)
        self.release(hook)
        self.assertEqual(hook.user32.SendInput.call_count, 3)


if __name__ == '__main__':
    unittest.main()
