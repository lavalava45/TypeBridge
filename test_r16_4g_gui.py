"""R16/4g GUI/runtime hotkey tests; synthetic engine, no OS hooks or HKLs."""

from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from direct_hotkeys import compile_bindings, parse_chord
from typebridge_app import (Settings, TypeBridgeGui, pending_engine_settings,
                            running_engine_settings)


class R164gGuiTests(TestCase):
    def setUp(self):
        self.catalog = {lang: SimpleNamespace(code=lang, langid=ident)
                        for lang, ident in [('ru', 0x419), ('en', 0x409),
                                            ('he', 0x40d), ('ar', 0x401)]}
        self.previous = replace(Settings(), active_languages=('ru', 'en', 'he'),
                                language_shortcuts=(('ru', 'Ctrl+Shift+F1'),
                                                    ('en', 'Ctrl+Shift+F2'),
                                                    ('he', 'Ctrl+Shift+F3'),
                                                    ('ar', 'Ctrl+Shift+F4')))
        self.desired = replace(self.previous, active_languages=('ru', 'en'),
                               language_shortcuts=(('ru', 'Ctrl+Shift+F3'),
                                                   ('en', 'Ctrl+Shift+F2'),
                                                   ('he', 'Ctrl+Shift+F3'),
                                                   ('ar', 'Ctrl+Shift+F4')))

    def test_disabled_language_shortcut_may_be_reused_after_restart_only(self):
        self.assertEqual(compile_bindings(self.desired, self.catalog)[
            parse_chord('Ctrl+Shift+F3')], 'ru')
        engine = SimpleNamespace(active_languages=('ru', 'en', 'he'),
                                 language_catalog=self.catalog)
        with self.assertRaisesRegex(ValueError, 'Одно сочетание'):
            running_engine_settings(self.desired, engine)
        with self.assertRaisesRegex(ValueError, 'Одно сочетание'):
            pending_engine_settings(self.desired, engine.active_languages,
                                    self.catalog)

    def test_save_rejects_running_conflict_before_mutating_settings(self):
        engine = SimpleNamespace(active_languages=('ru', 'en', 'he'),
                                 language_catalog=self.catalog)
        fake = SimpleNamespace(current_settings=Mock(return_value=self.desired),
                               hook=SimpleNamespace(engine=engine),
                               root=object(), search_settings=self.previous,
                               language_catalog=self.catalog)
        with (patch('typebridge_app.save_settings') as save,
              patch('typebridge_app.write_registration') as registry,
              patch('typebridge_app.messagebox.showerror') as error):
            self.assertIsNone(TypeBridgeGui.save(fake))
        save.assert_not_called()
        registry.assert_not_called()
        error.assert_called_once()

    def test_save_rejects_inflight_mapper_conflict(self):
        fake = SimpleNamespace(current_settings=Mock(return_value=self.desired),
                               hook=None, run_state='starting',
                               launch_languages=('ru', 'en', 'he'),
                               language_catalog=self.catalog, root=object(),
                               search_settings=self.previous)
        with (patch('typebridge_app.save_settings') as save,
              patch('typebridge_app.write_registration') as registry,
              patch('typebridge_app.messagebox.showerror') as error):
            self.assertIsNone(TypeBridgeGui.save(fake))
        save.assert_not_called()
        registry.assert_not_called()
        error.assert_called_once()

    def test_external_only_set_has_no_stale_builtin_binding(self):
        desired = replace(self.previous, active_languages=('ar',),
                          language_shortcuts=(('ar', 'Ctrl+Alt+F6'),
                                              ('he', 'Ctrl+Shift+F3')))
        self.assertEqual(set(compile_bindings(desired, self.catalog).values()),
                         {'ar'})
