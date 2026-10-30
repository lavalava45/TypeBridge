"""R16 staged active-language policy: deterministic, no physical input/hooks."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from engine import LanguageEngine, LANGS, candidate_token_key
from incremental_recognition import prepare_early, suggest_live_early
from language_registry import DEFAULT_ACTIVE_LANGUAGES, validate_active_languages
from test_incremental_recognition import fixture
from test_word_cycle import WordCycleTests
from word_cycle import ORDER, RecentWord


class ActiveLanguagesTests(TestCase):
    def test_default_order_and_invalid_sets(self):
        self.assertEqual(DEFAULT_ACTIVE_LANGUAGES, ('ru', 'en', 'he'))
        self.assertEqual(ORDER, DEFAULT_ACTIVE_LANGUAGES)
        self.assertEqual(validate_active_languages(('ru', 'en')), ('ru', 'en'))
        self.assertEqual(validate_active_languages(('he',)), ('he',))
        for invalid in ((), ('ru', 'ru'), ('ru', 'ar'), 'ru', None,
                        {'ru', 'en'}, ('ru', 5)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_active_languages(invalid)

    def test_real_engine_defaults_and_filters_complete_words(self):
        from test_r16_registry_integration import MapFixture
        with TemporaryDirectory() as folder:
            data = Path(folder)
            contents = {'ru.txt': 'дом\n', 'en.txt': 'hello\n',
                        'he.txt': 'שלום\n', 'confident_ru.txt': 'дом\n',
                        'confident_en.txt': 'hello\n',
                        'confident_he.txt': 'שלום\n', 'he_custom.txt': '',
                        'exclude.txt': ''}
            for filename, content in contents.items():
                (data / filename).write_text(content, encoding='utf-8')
            forms = {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}
            mapper = MapFixture(forms)
            with patch('engine.LayoutMapper', return_value=mapper):
                original = LanguageEngine(data)
                reduced = LanguageEngine(data, active_languages=('ru', 'en'))
            self.assertEqual(original.active_languages, tuple(LANGS))
            self.assertEqual(reduced.active_languages, ('ru', 'en'))
            keys = [(65 + i, False) for i in range(5)]
            self.assertEqual(original.decide(keys, 'ru').language, 'en')
            self.assertEqual(reduced.decide(keys, 'ru').language, 'en')
            self.assertIsNone(reduced.decide(keys, 'he'))
            self.assertIsNone(reduced.decide_token(keys, 'he'))
            # Unified runtime does not load disabled languages into memory.
            self.assertNotIn('he', reduced.words)
            self.assertEqual(reduced.decide(keys, 'ru').language, 'en')
            reduced.words['en'].clear()
            self.assertIsNone(reduced.decide(keys, 'ru'))
            self.assertIsNone(reduced.resolve_ambiguity([('he', 'אקךךם')], 'ru'))

    def test_early_gate_and_disabled_prefix_no_longer_competes(self):
        representations = {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}
        engine, keys = fixture(representations, 'ru', 'en', 'hellos',
                               competitors=[('he', 'אקךךםא')])
        engine.conservative = True
        engine._confident = lambda language, word: word in engine.confident[language]
        self.assertIsNone(suggest_live_early(engine, keys, 'ru'))
        engine.active_languages = ('ru', 'en')
        self.assertEqual(suggest_live_early(engine, keys, 'ru').language, 'en')
        self.assertIsNone(suggest_live_early(engine, keys, 'he'))
        engine.active_languages = ('ru', 'he')
        self.assertIsNone(suggest_live_early(engine, keys, 'ru'))
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))

    def test_rare_early_must_not_use_disabled_target(self):
        forms = {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}
        engine, keys = fixture(forms, 'ru', 'en', 'hello')
        engine.conservative = False
        engine._confident = lambda language, word: word in engine.confident[language]
        engine.confident['en'].clear()
        self.assertEqual(suggest_live_early(engine, keys, 'ru').language, 'en')
        engine.active_languages = ('ru', 'he')
        self.assertIsNone(suggest_live_early(engine, keys, 'ru'))

    def test_punctuation_tokenization_ignores_disabled_layout(self):
        from test_r16_registry_integration import MapFixture
        mapper = MapFixture({}, {(0xBC, 'en', False): ',',
                                 (0xBC, 'ru', False): 'б',
                                 (0xBC, 'he', False): ''})
        self.assertTrue(candidate_token_key(mapper, 0xBC, False, 'en', False))
        self.assertFalse(candidate_token_key(
            mapper, 0xBC, False, 'en', False, active_languages=('en', 'he')))
        self.assertTrue(candidate_token_key(
            mapper, 0xBC, False, 'en', False, active_languages=('en', 'ru')))
        self.assertFalse(candidate_token_key(
            mapper, 0xBC, False, 'ru', False, active_languages=('en', 'he')))

    def test_cycle_skips_disabled_languages_without_changing_default(self):
        class Mapper:
            def word(self, keys, language):
                return {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}[language]

        word = RecentWord(tuple((65 + i, False) for i in range(5)), 'hello',
                          'en', '', (1,))
        self.assertEqual(word.next_variant(Mapper()), ('he', 'אקךךם'))
        self.assertEqual(word.next_variant(Mapper(), ('ru', 'en')),
                         ('ru', 'руддщ'))
        with self.assertRaises(ValueError):
            word.next_variant(Mapper(), ('ru', 'he'))
        with self.assertRaises(ValueError):
            word.next_variant(Mapper(), ('en',))

    def test_manual_cycle_hook_model_uses_engine_active_languages(self):
        model = WordCycleTests('test_cycle_before_space_and_no_dictionary_match_required')
        model.setUpClass()
        model.setUp()
        with patch.object(model.engine, 'active_languages', ('ru', 'en'), create=True):
            model.type('xyzzy ')
            initial = model.text
            model.cycle()
            self.assertNotEqual(model.text, initial)
            self.assertEqual(model.language, 'ru')
            model.cycle()
            self.assertEqual(model.text, initial)
            self.assertEqual(model.language, 'en')
            model.language = 'he'
            model.type('xyzzy ')
            unchanged = model.text
            model.hook.user32.SendInput.reset_mock()
            model.cycle()
            self.assertEqual(model.text, unchanged)
            model.hook.user32.SendInput.assert_not_called()

