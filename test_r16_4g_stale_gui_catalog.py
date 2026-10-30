"""Saving a stale package checkbox must not poison the next TypeBridge launch.

All package roots and proposed settings are synthetic. No real HKL/hook/UI.
"""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import shutil

from language_pack_loader import available_language_catalog
from test_r16_language_packs import write_pack
from typebridge_app import Settings, TypeBridgeGui


class StaleGuiCatalogTests(TestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.root = Path(self.temp).resolve() / 'language_packs'
        self.root.mkdir()
        write_pack(self.root, 'ar', word='مرحبا\n')
        self.catalog = available_language_catalog(self.root)
        self.gui = SimpleNamespace(
            current_settings=Mock(return_value=replace(
                Settings(), active_languages=('en', 'ar'))),
            language_catalog=self.catalog, hook=None, run_state='stopped',
            search_settings=Settings(), root=object())

    def _save(self):
        with (patch('typebridge_app.LANGUAGE_PACK_DIR', self.root),
              patch('typebridge_app.save_settings') as save_settings,
              patch('typebridge_app.write_registration') as registry,
              patch('typebridge_app.read_registration') as read_reg,
              patch('typebridge_app.check_active_layouts_loaded') as hkl,
              patch('typebridge_app.messagebox.showerror') as error):
            result = TypeBridgeGui.save(self.gui)
        return result, save_settings, registry, read_reg, hkl, error

    def test_deleted_ar_checkbox_rejected_before_any_write_or_hkl(self):
        shutil.rmtree(self.root / 'ar')  # Only a temporary test package.
        result, save, registration, read_reg, hkl, error = self._save()
        self.assertIsNone(result)
        save.assert_not_called()
        registration.assert_not_called()
        read_reg.assert_not_called()
        hkl.assert_not_called()
        error.assert_called_once()
        self.assertIn('Selected language package', str(error.call_args))

    def test_corrupted_selected_ar_is_rejected_before_any_write(self):
        (self.root / 'ar' / 'words.txt').write_text('tampered\n', encoding='utf-8')
        result, save, registration, read_reg, hkl, error = self._save()
        self.assertIsNone(result)
        save.assert_not_called()
        registration.assert_not_called()
        read_reg.assert_not_called()
        hkl.assert_not_called()
        error.assert_called_once()

    def test_removed_inactive_ar_does_not_block_saving_other_languages(self):
        shutil.rmtree(self.root / 'ar')
        self.gui.current_settings.return_value = replace(
            Settings(), active_languages=('en',))
        self.gui.search_settings = replace(Settings(), active_languages=('en',))
        self.gui.lexicon = object()
        self.gui._runtime_config_lock = None
        self.gui.clipboard_tools = Mock()
        self.gui.quick_search = Mock()
        self.gui._configure_journal = Mock()
        self.gui._apply_caret_indicator = Mock()
        self.gui.tray = None
        self.gui.launch_languages = ('en',)
        self.gui.emit = Mock()
        with (patch('typebridge_app.LANGUAGE_PACK_DIR', self.root),
              patch('typebridge_app.save_settings') as save,
              patch('typebridge_app.write_registration'),
              patch('typebridge_app.read_registration', return_value=None),
              patch('typebridge_app.messagebox.showerror') as error):
            result = TypeBridgeGui.save(self.gui)
        self.assertEqual(result.active_languages, ('en',))
        save.assert_called_once()
        error.assert_not_called()

