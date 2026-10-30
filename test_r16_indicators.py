"""Dynamic indicators and safe fallbacks; never open live desktop controls."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from caret_indicator import CaretReader, CaretFlagIndicator
from caret_process import CaretProcess
from language_registry import LANGUAGE_REGISTRY, DEFAULT_ACTIVE_LANGUAGES
from tray_win32 import TrayController, _icon_files, display_icon_code
from typebridge_app import TypeBridgeGui


class DynamicIndicatorTests(unittest.TestCase):
    def test_default_caret_and_tray_use_registry_metadata_without_engine_import(self):
        import ast
        sources = (Path(__file__).with_name('caret_indicator.py'),
                   Path(__file__).with_name('caret_process.py'))
        for path in sources:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            self.assertFalse(any(
                node.module == 'engine' for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)), path.name)
        expected = {item.code: item.langid for item in LANGUAGE_REGISTRY}
        self.assertEqual(CaretProcess().languages, expected)
        self.assertEqual(CaretFlagIndicator().languages, expected)
        tray = TrayController(lambda *_: None)
        self.assertEqual(tray.active_languages, DEFAULT_ACTIVE_LANGUAGES)

    def test_icons_for_ar_es_and_script_neutral_fallback(self):
        with TemporaryDirectory() as folder:
            with patch('app_paths.USER_DIR', Path(folder)):
                paths = _icon_files(('ar','es','pt-br','../escape','ru'))
            self.assertEqual(set(paths), {'ru','en','he','idle','ar','es','pt-br'})
            for key in ('ar','es','pt-br'):
                self.assertTrue(paths[key].is_file())
                from PIL import Image
                with Image.open(paths[key]) as img:
                    self.assertEqual(img.format, 'ICO')
            self.assertFalse((Path(folder) / 'escape.ico').exists())

    def test_inactive_installed_language_falls_back_to_neutral(self):
        codes = ('en', 'ar')
        self.assertEqual(display_icon_code('ar', codes), 'ar')
        self.assertEqual(display_icon_code('es', codes), 'idle')
        self.assertEqual(display_icon_code('unknown', codes), 'idle')
        tray = TrayController.__new__(TrayController)
        tray.closing = False
        tray.hwnd = 0
        tray.active_languages = codes
        tray.set_language('es')
        self.assertEqual(tray.language, 'idle')
        tray.set_language('ar')
        self.assertEqual(tray.language, 'ar')

    def test_caret_helper_accepts_ar_es_language_codes_without_hooks(self):
        mapping = {'en': 0x0409, 'ar': 0x0401, 'es': 0x0C0A}
        process = CaretProcess(languages=mapping, synthetic=True)
        self.assertEqual(process.languages, mapping)
        self.assertFalse(process.process)
        reader = CaretReader.__new__(CaretReader)
        reader.languages = mapping
        indicator = CaretFlagIndicator(languages=mapping)
        self.assertEqual(indicator.languages, mapping)
        self.assertFalse(indicator.enabled.is_set())
        self.assertFalse(indicator.ready.is_set())
        # Constructor must not install a hook or start worker processes.
        self.assertIsNone(process.process)

    def test_external_only_caret_ipc_accepts_ar_without_desktop_input(self):
        process = CaretProcess(languages={'ar':0x0401}, synthetic=True)
        try:
            value = process.snapshot(scope='synthetic-ar')
            self.assertIsNotNone(value, process.last_error)
            self.assertEqual(value.language, 'ar')
        finally:
            process.close()

    def test_taskbar_unknown_layout_uses_existing_idle_icon(self):
        gui = TypeBridgeGui.__new__(TypeBridgeGui)
        gui.root = Mock()
        gui.display_languages = ('en','ar')
        gui._window_icon_language = None
        with patch('tray_win32._icon_files', return_value={
                'idle':Path('idle.ico'),'ar':Path('ar.ico'),'en':Path('en.ico')}) as icons:
            gui._update_taskbar_language_icon('es')
            gui.root.iconbitmap.assert_called_with('idle.ico')
            gui._update_taskbar_language_icon('ar')
            gui.root.iconbitmap.assert_called_with('ar.ico')
            self.assertEqual(gui.root.iconbitmap.call_count, 2)


if __name__ == '__main__':
    unittest.main()
