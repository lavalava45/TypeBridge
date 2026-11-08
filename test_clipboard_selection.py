"""Clipboard transaction and preview tests; no real clipboard, input or browser."""
from dataclasses import replace
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from clipboard_selection import copy_transaction, SearchPreview, SelectionUnavailable, NativeCopy
from quick_search import QuickSearch, read_selection
from typebridge_app import Settings


class TransactionTests(unittest.TestCase):
    def test_copy_partial_send_cleans_only_accepted_keydowns(self):
        # Real input structure, completely mocked SendInput; no clipboard or
        # actual OS keyboard is touched. Prefixes 0..3 exercise every case.
        for accepted, expected in (
                (0, []), (1, [0x11]), (2, [0x43, 0x11]), (3, [0x11])):
            with self.subTest(accepted=accepted):
                native = NativeCopy.__new__(NativeCopy)
                native._checkpoint = Mock()  # Synthetic verified-backup boundary.
                native.target = SimpleNamespace(pid=1, top=10, focus=20, host_pid=1)
                api = unittest.mock.Mock()
                calls = []
                def send(count, events, size):
                    calls.append(tuple((events[i].u.ki.wVk, events[i].u.ki.dwFlags)
                                       for i in range(count)))
                    return accepted if len(calls) == 1 else count
                api.SendInput.side_effect = send
                with (patch('clipboard_selection.ensure_copy_target_safe'),
                      patch('ctypes.WinDLL', return_value=api)):
                    with self.assertRaises(SelectionUnavailable):
                        native.copy(still_valid=lambda: True, deadline=__import__('time').monotonic() + 1)
                self.assertEqual(len(calls), 1 + int(bool(expected)))
                if expected:
                    self.assertEqual([vk for vk, flags in calls[-1]], expected)
                    self.assertTrue(all(flags & 2 for vk, flags in calls[-1]))
                self.assertEqual(calls[0], ((0x11, 0), (0x43, 0),
                                            (0x43, 2), (0x11, 2)))

    def test_ole_bookkeeping_does_not_block_materialized_image_formats(self):
        from clipboard_selection import BitmapBackup
        native = NativeCopy.__new__(NativeCopy)
        native.hwnd = 123
        native.cb = Mock()
        native.cb.EnumClipboardFormats.side_effect = [49161, 2, 49522, 49171, 8, 17, 0]
        native.cb.GetClipboardFormatName.side_effect = lambda fmt: {49161: 'DataObject', 49522: 'PNG', 49171: 'Ole Private Data'}[fmt]
        native.cb.GetClipboardSequenceNumber.return_value = 22
        native.global_bytes = Mock(return_value=b'fixture image bytes')
        native.bitmap_backup = Mock(return_value=BitmapBackup(1234, 16))
        rows, sequence = native.snapshot()
        self.assertEqual([fmt for fmt, _ in rows], [2, 49522, 8, 17])
        self.assertEqual(sequence, 22)
        self.assertEqual([call.args[0] for call in native.global_bytes.call_args_list], [49522, 8, 17])
        native.cb.EmptyClipboard.assert_not_called()
        native.cb.CloseClipboard.assert_called_once()

    def test_real_embedded_object_or_metadata_only_still_aborts_before_copy(self):
        for name in ('Embedded Object', 'DataObject', 'FileGroupDescriptorW'):
            native = NativeCopy.__new__(NativeCopy)
            native.hwnd = 123
            native.cb = Mock()
            native.cb.EnumClipboardFormats.side_effect = [49161, 0]
            native.cb.GetClipboardFormatName.return_value = name
            with self.assertRaises(SelectionUnavailable):
                native.snapshot()
            native.cb.EmptyClipboard.assert_not_called()
            native.cb.CloseClipboard.assert_called_once()

    def test_bitmap_backup_clones_native_handle_without_touching_clipboard(self):
        import ctypes
        from ctypes import wintypes as w
        from clipboard_selection import BitmapBackup
        api = ctypes.WinDLL('user32', use_last_error=True)
        gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        gdi.CreateBitmap.argtypes, gdi.CreateBitmap.restype = [ctypes.c_int, ctypes.c_int, w.UINT, w.UINT, ctypes.c_void_p], w.HANDLE
        gdi.DeleteObject.argtypes = [w.HANDLE]
        original = gdi.CreateBitmap(2, 2, 1, 32, None)
        self.assertTrue(original)
        native = NativeCopy.__new__(NativeCopy)
        native.bitmaps = []
        fake_api = SimpleNamespace(GetClipboardData=Mock(return_value=original), CopyImage=api.CopyImage)
        try:
            with patch('ctypes.WinDLL', side_effect=lambda name, **kw: fake_api if name == 'user32' else gdi):
                saved = native.bitmap_backup(2, 1024)
            self.assertIsInstance(saved, BitmapBackup)
            self.assertTrue(saved.handle)
            self.assertNotEqual(saved.handle, original)
            self.assertEqual(saved.size, 16)
        finally:
            for bitmap in native.bitmaps:
                if bitmap.handle: gdi.DeleteObject(bitmap.handle)
            gdi.DeleteObject(original)

    def fixture(self):
        n = Mock()
        n.keys_released.return_value = True
        n.snapshot.return_value = ([(13, 'old'), (49152, b'<b>old</b>')], 10)
        state = {'seq': 10, 'time': 0.0}
        n.sequence.side_effect = lambda: state['seq']
        n.copy.side_effect = lambda **kwargs: state.update(seq=11)
        n.owned_by.return_value = True
        n.text.return_value = 'שלום да'
        n.restore.return_value = True
        valid = Mock(return_value=True)
        target = object()
        def sleep(dt): state['time'] += dt
        return n, state, valid, target, lambda: state['time'], sleep

    def test_restore_rich_formats_and_require_preview(self):
        n, state, valid, target, clock, sleep = self.fixture()
        result = copy_transaction(n, target, valid, clock=clock, sleep=sleep)
        self.assertIsInstance(result, SearchPreview)
        self.assertEqual(result.text, 'שלום да')
        n.copy.assert_called_once()
        n.restore.assert_called_once_with(n.snapshot.return_value[0], 11, target)

    def test_no_selection_does_not_search_old_clipboard_or_repeat_copy(self):
        n, state, valid, target, clock, sleep = self.fixture()
        n.copy.side_effect = None  # Application did not update clipboard.
        with self.assertRaises(SelectionUnavailable):
            copy_transaction(n, target, valid, clock=clock, sleep=sleep)
        n.copy.assert_called_once()
        n.text.assert_not_called()
        n.restore.assert_not_called()

    def test_other_owner_new_copy_is_never_restored_over(self):
        n, state, valid, target, clock, sleep = self.fixture()
        n.owned_by.return_value = False
        with self.assertRaises(SelectionUnavailable):
            copy_transaction(n, target, valid, clock=clock, sleep=sleep)
        n.restore.assert_not_called()

    def test_changed_focus_oversized_copy_and_failed_restore_abort(self):
        for mode in ('focus', 'large', 'restore', 'partial'):
            n, state, valid, target, clock, sleep = self.fixture()
            if mode == 'focus': valid.side_effect = [True, True, True, False]
            elif mode == 'large': n.text.return_value = 'x' * 2001
            elif mode == 'restore': n.restore.return_value = False
            elif mode == 'partial':
                def fail(**kwargs):
                    state['seq'] = 11
                    raise SelectionUnavailable('partial')
                n.copy.side_effect = fail
            with self.subTest(mode=mode), self.assertRaises(SelectionUnavailable):
                copy_transaction(n, target, valid, clock=clock, sleep=sleep)
            n.restore.assert_called_once()

    def test_unsupported_snapshot_aborts_before_copy(self):
        n, state, valid, target, clock, sleep = self.fixture()
        n.snapshot.side_effect = SelectionUnavailable('unsupported')
        with self.assertRaises(SelectionUnavailable):
            copy_transaction(n, target, valid, clock=clock, sleep=sleep)
        n.copy.assert_not_called()

    def test_cancelled_configuration_does_not_start_copy(self):
        service = QuickSearch(Mock())
        generation = service.generation
        service.configure()
        with self.assertRaises(SelectionUnavailable):
            with service.guarded_copy(generation):
                self.fail('Cancelled request entered Copy')

    def test_only_explicit_unsupported_uia_pattern_uses_copy(self):
        target = SimpleNamespace(pid=123, top=456, focus=789)
        samples = ({'state': 'ok', 'text': 'selected'},
                   {'state': 'refused'}, {'state': 'unsupported'}, None)
        for sample in samples:
            with (self.subTest(sample=sample),
                  patch('uia_native_worker.get_uia_service') as service,
                  patch('quick_search.copy_selection', return_value=SearchPreview('selected')) as fallback):
                service.return_value.request.return_value = sample
                if sample is not None and sample['state'] in ('ok', 'unsupported'):
                    read_selection(target)
                else:
                    with self.assertRaises(ValueError):
                        read_selection(target)
                service.return_value.request.assert_called_once()
                self.assertEqual(fallback.call_count, int(sample is not None and sample['state'] == 'unsupported'))

    def test_service_never_opens_browser_for_preview(self):
        service = QuickSearch(Mock(), opener=Mock())
        layouts = Mock()
        target = object()
        layouts.capture.return_value = target
        settings = replace(Settings(), quick_search=True)
        preview = SearchPreview('selected', 'https://example.org/?q=selected')
        # Simulate an outstanding request; unsolicited/stale results are ignored.
        service.request_deadline = __import__('time').monotonic() + 8
        with patch('app_exclusions.target_allowed', return_value=True):
            result = service.receive((0, target, settings, preview, None), settings, layouts)
        self.assertEqual(result, preview)
        service.opener.assert_not_called()

    def test_history_ignores_temporary_copy_and_resumes_after_error(self):
        from clipboard_tools import ClipboardTools
        tools = ClipboardTools(Mock(), Mock(), native=Mock())
        tools.configure(replace(Settings(), clipboard_history=True))
        previous = tools.generation
        try:
            with tools.selection_copy():
                self.assertTrue(tools.history_paused)
                tools.receive(('captured', previous, 12, 'private temporary text'))
                self.assertEqual(tools.history.items, [])
                raise ValueError('test')
        except ValueError:
            pass
        self.assertFalse(tools.history_paused)
        self.assertEqual(tools.generation, previous + 2)
        tools.close()


class HiddenPreviewTests(unittest.TestCase):
    def test_only_confirmation_opens_browser_and_settings_change_cancels(self):
        from quick_search_gui import show_search_preview
        from tkinter import ttk
        from ui_i18n import set_language, tr
        root = tk.Tk()
        root.withdraw()
        try:
            app = SimpleNamespace(clipboard_tools=SimpleNamespace(settings=replace(Settings(), quick_search=True)),
                                  quick_search=SimpleNamespace(generation=1, opener=Mock(return_value=True)), closing=False)
            for cancel in (True, False):
                w = show_search_preview(root, SearchPreview('שלום private', 'https://example.org'), app)
                w.withdraw()
                for language in ('he', 'en', 'ru'):
                    set_language(language)
                    root.update_idletasks()
                    self.assertFalse(w.winfo_ismapped())
                app.quick_search.opener.assert_not_called()
                def children(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from children(child)
                button = next(child for child in children(w) if isinstance(child, ttk.Button) and child.cget('text') == tr('Искать в браузере'))
                if cancel: app.quick_search.generation += 1
                button.invoke()
            app.quick_search.opener.assert_called_once_with('https://example.org')
        finally:
            set_language('ru')
            root.destroy()


if __name__ == '__main__':
    unittest.main()
