"""No real hooks, focus changes, clipboard access or browser launches."""
import ctypes
from dataclasses import replace
from types import SimpleNamespace
import json
from pathlib import Path
import queue
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from quick_search import DEFAULT_ENGINES, QuickSearch, read_selection, search_url, validate_engines
from typebridge_app import Settings, TypeBridgeGui, load_settings, save_settings
from direct_hotkeys import compile_bindings
from tray_layouts import LayoutTarget


class SearchTests(unittest.TestCase):
    def test_receive_rechecks_deadline_after_window_validation(self):
        target = LayoutTarget(10, 11, 12, 13)
        settings = replace(Settings(), quick_search=True)
        for delay in (1.0, 30.0):
            with self.subTest(delay=delay):
                now = [0.0]
                layouts = Mock()
                layouts.capture.return_value = target
                def valid(_):
                    now[0] = delay
                    return True
                layouts.valid.side_effect = valid
                opener = Mock(return_value=True)
                service = QuickSearch(Mock(), opener=opener)
                service.busy = True
                service.request_deadline = 8.0
                with patch('quick_search.time.monotonic', side_effect=lambda: now[0]), \
                     patch('app_exclusions.target_allowed', return_value=True):
                    message = service.receive((0, target, settings, 'https://example.org/', None),
                                              settings, layouts)
                self.assertFalse(service.busy)
                self.assertIsNone(service.request_deadline)
                if delay > 8:
                    opener.assert_not_called()
                    self.assertEqual(message, 'Поиск отменён: истёк срок запроса')
                else:
                    opener.assert_called_once_with('https://example.org/')

    def test_worker_start_failure_completes_and_allows_retry(self):
        target = LayoutTarget(10, 11, 12, 13)
        settings = replace(Settings(), quick_search=True)
        layouts = Mock()
        layouts.capture.return_value = target
        layouts.valid.return_value = True
        results = queue.Queue()
        reader, opener = Mock(return_value='test'), Mock(return_value=True)
        service = QuickSearch(results.put, reader, opener)
        with patch('app_exclusions.target_allowed', return_value=True):
            for failure in ('constructor', 'start'):
                with self.subTest(failure=failure):
                    with patch('quick_search.threading.Thread') as thread:
                        if failure == 'constructor':
                            thread.side_effect = RuntimeError('private-provider-detail')
                        else:
                            thread.return_value.start.side_effect = RuntimeError('private-provider-detail')
                        service.request(target, settings, layouts)
                        self.assertTrue(service.busy)
                        # No second request until the GUI consumes the error.
                        service.request(target, settings, layouts)
                        self.assertEqual(thread.call_count, 1)
                    reader.assert_not_called()
                    opener.assert_not_called()
                    result = results.get_nowait()
                    self.assertTrue(results.empty())
                    self.assertEqual(service.receive(result, settings, layouts),
                                     'Не удалось запустить поиск')
                    self.assertFalse(service.busy)
                    self.assertNotIn('private-provider-detail', str(result))
            service.request(target, settings, layouts)
            result = results.get(timeout=2)
            self.assertEqual(service.receive(result, settings, layouts), 'Поиск открыт в браузере')
            self.assertFalse(service.busy)
            reader.assert_called_once_with(target)
            opener.assert_called_once()

    def test_defaults_roundtrip_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            path.write_text('{}')
            self.assertFalse(load_settings(path).quick_search)
            settings = replace(Settings(), quick_search=True, quick_search_key='Ctrl+Alt+2',
                               quick_search_engine='Dictionary', quick_search_engines=(*DEFAULT_ENGINES, ('Dictionary', 'https://example.org/{query}')))
            save_settings(settings, path)
            self.assertEqual(settings, load_settings(path))
            for changes in ({'quick_search': 'yes'}, {'quick_search_engine': []},
                            {'quick_search_key': 'nonsense'}, {'quick_search_engines': []}):
                path.write_text(json.dumps(changes))
                with self.assertRaises(ValueError):
                    load_settings(path)

    def test_encoding_and_rejection(self):
        from urllib.parse import parse_qs, urlsplit
        text = 'да שלום &q=secret # / ?'
        url = search_url(Settings(), text)
        self.assertEqual(parse_qs(urlsplit(url).query), {'q': [text]})
        for text in ('', ' ', '\0', 'x' * 2001, None):
            with self.assertRaises(ValueError):
                search_url(Settings(), text)
        for url in ('file:///x/{query}', 'javascript:{query}', 'https://{query}.example.com/',
                    'https://user:pass@example.org/{query}', 'https://example.org/no-placeholder',
                    'https://example.org/{query}{query}', 'https://example.org/{other}',
                    'https://example.org:bad/{query}', 'https://example.org/\n{query}'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_engines((('Test', url),), 'Test')
        with self.assertRaises(ValueError):
            validate_engines((('Test', 'https://example.org/{query}'), ('test', 'https://example.com/{query}')), 'Test')

    def test_shortcut_conflicts(self):
        for key in ('Ctrl+Shift+F1', 'Ctrl+Alt+Pause', 'F8', 'Ctrl+Alt+F12'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                compile_bindings(replace(Settings(), quick_search=True, quick_search_key=key))
        with self.assertRaises(ValueError):
            compile_bindings(replace(Settings(), quick_search=True, clipboard_history=True,
                                     quick_search_key='Ctrl+Alt+H'))
        compile_bindings(replace(Settings(), quick_search=True))

    def test_native_selection_reader_uses_verified_persistent_uia_service(self):
        target = LayoutTarget(101, 102, 1, 555)
        service = Mock()
        service.request.return_value = {'state': 'ok', 'text': 'שלום'}
        with patch('uia_native_worker.get_uia_service', return_value=service):
            self.assertEqual(read_selection(target), 'שלום')
        command = service.request.call_args.args[0]
        self.assertEqual(command, {'command': 'selection', 'top': 101, 'focus': 102,
                                   'pid': 555})
        self.assertTrue(callable(service.request.call_args.kwargs['valid']))
        for reply in ({'state': 'refused'}, None, {'state': 'ok', 'text': ''},
                      {'state': 'ok', 'text': 'x' * 2001}):
            with self.subTest(reply=repr(reply)[:60]):
                service.request.return_value = reply
                with patch('uia_native_worker.get_uia_service', return_value=service), \
                     self.assertRaises(ValueError):
                    read_selection(target)

    def test_native_selection_validates_process_focus_and_selected_range_without_input(self):
        from uia_selection_native import read_native_selection
        api = SimpleNamespace(UIA_ProcessIdPropertyId=1, UIA_TextPatternId=2,
                              IUIAutomationTextPattern=object,
                              TextPatternRangeEndpoint_Start=0,
                              TextPatternRangeEndpoint_End=1)
        element = Mock()
        element.GetCurrentPropertyValue.return_value = 555
        native_range = Mock()
        native_range.Clone.return_value = native_range
        native_range.CompareEndpoints.side_effect = (
            lambda endpoint, _other, other_endpoint: 1 if endpoint != other_endpoint else 0)
        native_range.GetText.return_value = 'selected'
        ranges = Mock(Length=1)
        ranges.GetElement.return_value = native_range
        pattern = Mock()
        pattern.GetSelection.return_value = ranges
        element.GetCurrentPattern.return_value.QueryInterface.return_value = pattern
        client = Mock()
        client.GetFocusedElement.return_value = element
        with patch('uia_native_worker._safe_target', return_value=True), \
             patch('uia_native_worker._runtime_id', return_value='uia:1,2'):
            result = read_native_selection(client, {'top': 101, 'focus': 102, 'pid': 555}, api)
            self.assertEqual(result, {'state': 'ok', 'text': 'selected'})
            self.assertEqual(native_range.GetText.call_args.args, (2001,))
            element.GetCurrentPropertyValue.return_value = 556
            self.assertEqual(read_native_selection(client, {'top': 101, 'focus': 102, 'pid': 555}, api),
                             {'state': 'refused'})
        element.GetCurrentPattern.return_value = None
        element.GetCurrentPropertyValue.return_value = 555
        with patch('uia_native_worker._safe_target', return_value=True), \
             patch('uia_native_worker._runtime_id', return_value='uia:1,2'):
            self.assertEqual(read_native_selection(client, {'top': 101, 'focus': 102, 'pid': 555}, api),
                             {'state': 'unsupported'})

    def test_service_success_rejection_and_cancellation(self):
        target = LayoutTarget(10, 11, 12, 13)
        settings = replace(Settings(), quick_search=True)
        layouts = Mock()
        layouts.capture.return_value = target
        layouts.valid.return_value = True
        results = queue.Queue()
        reader, opener = Mock(return_value='שלום'), Mock(return_value=True)
        service = QuickSearch(results.put, reader, opener)
        with patch('app_exclusions.target_allowed', return_value=True):
            service.request(target, settings, layouts)
            result = results.get(timeout=2)
            self.assertEqual(service.receive(result, settings, layouts), 'Поиск открыт в браузере')
            self.assertIn('%D7', opener.call_args.args[0])
            for mode in ('focus', 'disabled', 'generation', 'closed'):
                opener.reset_mock()
                service.request(target, settings, layouts)
                result = results.get(timeout=2)
                current = settings
                if mode == 'focus':
                    layouts.capture.return_value = None
                elif mode == 'disabled':
                    current = Settings()
                elif mode == 'generation':
                    service.configure()
                service.receive(result, current, layouts, closing=mode == 'closed')
                opener.assert_not_called()
                layouts.capture.return_value = target
            reader.side_effect = RuntimeError('private text must not leak')
            service.request(target, settings, layouts)
            message = service.receive(results.get(timeout=2), settings, layouts)
            self.assertNotIn('private', message)
            opener.assert_not_called()
        reader.reset_mock()
        with patch('app_exclusions.target_allowed', return_value=False):
            service.request(target, settings, layouts)
        reader.assert_not_called()

    def test_hook_route_with_fake_native_api_only(self):
        from test_app_exclusions import ClipboardRoutingTests
        from clipboard_tools import clipboard_bindings
        fixture = ClipboardRoutingTests()
        router = fixture.fixture()
        router.settings = replace(Settings(), quick_search=True)
        router.context_cache = replace(router.context_cache, settings=router.settings)
        router.bindings = clipboard_bindings(router.settings)
        router.api.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if vk in (0x11, 0x12) else 0
        with patch('clipboard_hotkeys.target_allowed', return_value=True):
            self.assertEqual(fixture.send(router, vk=ord('G')), 1)
            self.assertEqual(fixture.send(router, vk=ord('G')), 1)
            self.assertEqual(fixture.send(router, vk=ord('G'), message=0x101), 1)
        router.notify.assert_called_once_with(('quick-search', router.layouts.capture.return_value))
        router.notify.reset_mock()
        router.context_cache = None  # Background exclusion denies the target.
        with patch('clipboard_hotkeys.target_allowed', return_value=False):
            self.assertEqual(fixture.send(router, vk=ord('G')), 0)
        router.notify.assert_not_called()

    def test_apply_settings_only_after_successful_save(self):
        app = TypeBridgeGui.__new__(TypeBridgeGui)
        app.search_settings = Settings()
        app.quick_search = Mock()
        app.clipboard_tools = Mock()
        app.tray = Mock()
        app.emit = Mock()
        with patch('typebridge_app.load_settings', return_value=Settings()), patch('typebridge_app.save_settings', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                app.apply_search_settings(True, 'Ctrl+Alt+G', 'Google', DEFAULT_ENGINES)
        self.assertFalse(app.search_settings.quick_search)
        app.tray.configure_clipboard.assert_not_called()
        app.quick_search.configure.assert_not_called()
        with patch('typebridge_app.load_settings', side_effect=[Settings(), replace(Settings(), quick_search=True)]), patch('typebridge_app.save_settings'):
            app.apply_search_settings(True, 'Ctrl+Alt+G', 'Google', DEFAULT_ENGINES)
        self.assertTrue(app.search_settings.quick_search)
        app.tray.configure_clipboard.assert_called_once()


class HiddenSearchGuiTests(unittest.TestCase):
    def test_actual_save_handler_roundtrip_in_temporary_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            save_settings(replace(Settings(), only_window_title='preserve'), path)
            app = TypeBridgeGui.__new__(TypeBridgeGui)
            app.quick_search = Mock()
            app.clipboard_tools = Mock()
            app.tray = None
            app.emit = Mock()
            with (patch('typebridge_app.SETTINGS_PATH', path),
                  patch('typebridge_app.save_settings', side_effect=lambda s: save_settings(s, path))):
                app.apply_search_settings(True, 'Ctrl+Alt+G', 'Google', DEFAULT_ENGINES)
                self.assertTrue(load_settings(path).quick_search)
                self.assertTrue(app.clipboard_tools.configure.call_args.args[0].quick_search)
                self.assertEqual(load_settings(path).only_window_title, 'preserve')
                app.apply_search_settings(False, 'Ctrl+Alt+G', 'Google', DEFAULT_ENGINES)
                self.assertFalse(load_settings(path).quick_search)

    def test_provider_editing_and_localization_without_focus_or_search(self):
        from quick_search_gui import QuickSearchWindow
        from ui_i18n import set_language, tr
        root = tk.Tk()
        root.withdraw()
        app = Mock(search_settings=Settings())
        try:
            window = QuickSearchWindow(root, app)
            window.window.withdraw()
            window.name.set('Словарь שלום')
            window.url.set('https://example.org/search?q={query}')
            window.upsert()
            self.assertEqual(len(window.engines), 2)
            window.selected.set('Словарь שלום')
            window.list.selection_set('1')
            window.select()
            window.name.set('New')
            window.upsert(update=True)
            self.assertEqual(window.selected.get(), 'New')
            for lang in ('en', 'he', 'ru'):
                set_language(lang)
                root.update_idletasks()
                self.assertEqual(window.window.title(), tr('TypeBridge — Быстрый поиск'))
                self.assertEqual(window.selected.get(), 'New')
                self.assertEqual(tuple(window.provider.cget('values')), ('Google', 'New'))
                self.assertFalse(window.window.winfo_ismapped())
                self.assertLessEqual(window.window.winfo_reqheight(), 640)
            window.save()
            app.apply_search_settings.assert_called_once()
            self.assertEqual(window.save_status.source, 'Сохранено: поиск выключен')
            window.enabled.set(True)
            self.assertEqual(window.save_status.source, 'Есть несохранённые изменения')
            app.apply_search_settings.side_effect = RuntimeError('secret text')
            window.save()
            self.assertEqual(window.save_status.source, 'Настройки поиска не сохранены')
            app.root.report_callback_exception.assert_called_once()
            window.list.selection_set('1')
            window.remove()
            self.assertEqual(window.selected.get(), 'Google')
        finally:
            set_language('ru')
            root.destroy()


if __name__ == '__main__':
    unittest.main()
