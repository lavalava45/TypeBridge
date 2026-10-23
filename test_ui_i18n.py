"""Localization checks in withdrawn windows; no hooks, typing or native dialogs."""
from dataclasses import replace
from pathlib import Path
import re
from string import Formatter
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from ui_i18n import LANGUAGES, ChoiceVar, LocalizedStringVar, get_language, set_language, tr, messagebox
from ui_translations import CATALOG
from typebridge_app import Settings, TypeBridgeGui, load_settings, save_settings
from user_lexicon import UserLexicon
from clipboard_tools import TextHistory


class CatalogTests(unittest.TestCase):
    def tearDown(self):
        set_language('ru')

    def test_complete_language_entries_and_format_fields(self):
        for source, values in CATALOG.items():
            fields = {field for _, field, _, _ in Formatter().parse(source) if field is not None}
            self.assertEqual(set(values), {'en', 'he'})
            for language, translation in values.items():
                self.assertTrue(translation)
                translated_fields = {field for _, field, _, _ in Formatter().parse(translation) if field is not None}
                self.assertEqual(fields, translated_fields, (source, language))

    def test_tray_and_dynamic_messages_follow_language(self):
        from tray_win32 import menu_items, MENU_OPEN, MENU_ENABLE
        for language, settings_label in (('en', 'Open settings'), ('he', 'פתח הגדרות'), ('ru', 'Открыть настройки')):
            set_language(language)
            menu = {row[0]: row for row in menu_items('running')}
            self.assertEqual(menu[MENU_OPEN][1], settings_label)
            self.assertTrue(menu[MENU_ENABLE][3])
        set_language('en')
        self.assertEqual(tr('История включена · фрагментов: 12'), 'History enabled · items: 12')
        self.assertEqual(tr('Дневник включён: снимок сохранён 12:34:56'),
                         'Journal enabled: snapshot saved at 12:34:56')
        self.assertEqual(tr('До пробела: EN → HE · 4 символов'), 'Before space: EN → HE · 4 characters')
        self.assertEqual(tr('Ошибка запуска: Нужная раскладка не загружена'),
                         'Startup error: The required layout is not loaded')

    def test_messagebox_title_and_body_translate_without_opening_dialog(self):
        set_language('he')
        with patch('ui_i18n._messagebox.showerror') as show:
            messagebox.showerror('Настройки', 'Некорректный JSON')
        show.assert_called_once_with('הגדרות', 'JSON אינו תקין')

    def test_language_settings_roundtrip_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            path.write_text('{}')
            self.assertEqual(load_settings(path).ui_language, 'ru')
            for language in LANGUAGES:
                expected = replace(Settings(), ui_language=language)
                save_settings(expected, path)
                self.assertEqual(load_settings(path), expected)
            for value in ('null', '[]', '"fr"', '3'):
                path.write_text('{"ui_language":' + value + '}')
                with self.assertRaises(ValueError):
                    load_settings(path)


class HiddenWindowTests(unittest.TestCase):
    def setUp(self):
        set_language('ru')
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        set_language('ru')
        self.root.destroy()

    def test_status_and_choice_vars_keep_original_data_across_languages(self):
        status = LocalizedStringVar(master=self.root, value='Остановлен')
        shortcut = ChoiceVar(master=self.root, value='Отключено')
        auto = ChoiceVar(master=self.root, value='auto')
        for language in LANGUAGES:
            set_language(language)
            self.assertEqual(shortcut.get(), 'Отключено')
            self.assertEqual(auto.get(), 'auto')
            self.assertEqual(status.source, 'Остановлен')
            self.assertEqual(status.get(), tr('Остановлен'))
        set_language('en')
        tk.StringVar.set(shortcut, 'Disabled')  # What the readonly dropdown displays.
        self.assertEqual(shortcut.get(), 'Отключено')

    def test_all_windows_switch_in_place_without_changing_words_templates_or_enabling_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            lexicon = UserLexicon()
            lexicon.add('words', 'ru', 'Слово', sensitive=True)
            clipboard = Mock()
            clipboard.settings = Settings()
            clipboard.history = TextHistory()
            clipboard.history.add('Слово שלום Settings')
            with (patch('typebridge_app.USER_DIR', folder),
                  patch('typebridge_app.load_settings', return_value=Settings()),
                  patch('typebridge_app.load_user_lexicon', return_value=lexicon),
                  patch('clipboard_tools.ClipboardTools', return_value=clipboard),
                  patch('typebridge_app.save_settings') as save):
                app = TypeBridgeGui(self.root)
                app.open_snippets(); app.snippets_editor.window.withdraw()
                app.open_exclusions(); app.exclusions_window.window.withdraw()
                app.open_dictionary_import(); app.dictionary_import_window.window.withdraw()
                app.open_clipboard_history(); app.clipboard_window.window.withdraw()
                app.open_journal(); app.journal_window.window.withdraw()
                snippet = app.snippets_editor
                draft = 'Несохранённый draft שלום //email'
                snippet.text.insert('1.0', draft)
                snippet.trigger.set('//draft')
                windows = [app.snippets_editor.window, app.exclusions_window.window,
                           app.dictionary_import_window.window, app.clipboard_window.window, app.journal_window.window]
                for language in ('en', 'he', 'ru'):
                    app.ui_language.set(LANGUAGES[language])
                    app.change_ui_language()
                    self.root.update_idletasks()
                    self.assertEqual(save.call_args.args[0].ui_language, language)
                    self.assertEqual(app.current_settings().ui_language, language)
                    self.assertEqual(app.save_button.cget('text'), tr('Сохранить настройки'))
                    self.assertEqual(snippet.text.get('1.0', 'end-1c'), draft)
                    self.assertEqual(snippet.trigger.get(), '//draft')
                    self.assertEqual(app.clipboard_window.preview_text.get('1.0', 'end-1c'), 'Слово שלום Settings')
                    self.assertEqual(app.word_list.item(app.word_list.get_children()[0], 'values')[2], 'Слово')
                    self.assertEqual(app.direct_keys['ru'].get(), 'Ctrl+Shift+F1')
                    self.assertEqual(app.dictionary_import_window.language.get(), 'auto')
                    self.assertFalse(app.corrections_enabled.get())
                    self.assertFalse(app.journal_service.enabled)
                    self.assertIsNone(app.journal_service.worker)
                    self.assertFalse(self.root.winfo_ismapped())
                    self.assertTrue(all(not window.winfo_ismapped() for window in windows))
                    if language != 'ru':
                        def check_labels(widget):
                            source = getattr(widget, '_i18n_text', '')
                            if isinstance(source, str) and re.search('[А-Яа-яЁё]', source):
                                self.assertNotEqual(tr(source), source, source)
                            for child in widget.winfo_children():
                                check_labels(child)
                        check_labels(self.root)
                    if language == 'he':
                        self.assertEqual(app.corrections_check.cget('style'), 'I18nRTL.TCheckbutton')
                    else:
                        self.assertEqual(str(app.corrections_check.cget('style')), '')
                self.assertFalse((folder / 'journal').exists())
                clipboard.native.write_plain.assert_not_called()

    def test_language_save_failure_keeps_previous_locale(self):
        app = TypeBridgeGui.__new__(TypeBridgeGui)
        app.root = self.root
        app.ui_language = tk.StringVar(master=self.root, value='English')
        with (patch('typebridge_app.load_settings', return_value=Settings()),
              patch('typebridge_app.save_settings', side_effect=OSError('disk full')),
              patch('typebridge_app.messagebox.showerror') as error):
            app.change_ui_language()
        self.assertEqual(get_language(), 'ru')
        self.assertEqual(app.ui_language.get(), 'Русский')
        error.assert_called_once()


if __name__ == '__main__':
    unittest.main()
