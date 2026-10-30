"""R16 characterizations: engine decisions without Windows layouts or hooks."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import (ALTERNATIVE_PRIORITY, DICTIONARY_FILES, LANGS,
                    LanguageEngine, _read_lexical_data, _valid_typed,
                    candidate_token_key, read_words, select_loaded_layouts)
from incremental_recognition import prepare_early, suggest_live_early
from language_registry import LANGUAGE_BY_CODE, LANGUAGE_REGISTRY
from test_incremental_recognition import fixture


class MapFixture:
    def __init__(self, forms, chars=None):
        self.forms = forms
        self.chars = chars or {}

    def word(self, keys, language):
        return self.forms[language][:len(keys)]

    def char(self, vk, language, shift=False):
        return self.chars.get((vk, language, shift), "")


def engine_fixture(forms, known=(), confident=()):
    engine = LanguageEngine.__new__(LanguageEngine)
    engine.mapper = MapFixture(forms)
    engine.words = {lang: set() for lang in forms}
    engine.confident = {lang: set() for lang in forms}
    engine.exclusions = set()
    engine.user_lexicon = None
    engine.conservative = True
    for lang in known:
        engine.words[lang].add(forms[lang].casefold())
    for lang in confident:
        engine.confident[lang].add(forms[lang].casefold())
    keys = [(65 + index, False) for index in range(len(next(iter(forms.values()))))]
    return engine, keys


class R16EngineBehaviorTests(unittest.TestCase):
    def test_complete_words_all_six_directions(self):
        examples = (
            ('ru', 'en', {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}),
            ('he', 'en', {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}),
            ('en', 'ru', {'ru': 'привет', 'en': 'ghbdtn', 'he': 'עינגאמ'}),
            ('he', 'ru', {'ru': 'привет', 'en': 'ghbdtn', 'he': 'עינגאמ'}),
            ('en', 'he', {'ru': 'флгщ', 'en': 'akuo', 'he': 'שלום'}),
            ('ru', 'he', {'ru': 'флгщ', 'en': 'akuo', 'he': 'שלום'}),
        )
        for source, target, forms in examples:
            with self.subTest(source=source, target=target):
                engine, keys = engine_fixture(forms, known=(target,), confident=(target,))
                decision = engine.decide_token(keys, source)
                self.assertIsNotNone(decision)
                self.assertEqual((decision.typed, decision.replacement, decision.language,
                                  decision.reason),
                                 (forms[source], forms[target], target, 'unique dictionary match'))

    def test_existing_priority_and_ambiguity_refusals(self):
        forms = {'en': 'xyz', 'ru': 'абв', 'he': 'אבג'}
        for source, target in (('ru', 'en'), ('en', 'ru'), ('he', 'ru')):
            with self.subTest(source=source):
                engine, keys = engine_fixture(forms, known=('ru', 'en', 'he'),
                                              confident=('ru', 'en', 'he'))
                engine.words[source].clear()
                decision = engine.decide(keys, source)
                self.assertEqual((decision.language, decision.reason),
                                 (target, 'configured language priority'))
                engine.words[source].add(forms[source])
                self.assertIsNone(engine.decide(keys, source))

        engine, keys = engine_fixture(forms, known=('ru', 'he'), confident=())
        self.assertIsNone(engine.decide(keys, 'en'))
        engine.confident['ru'].add('абв')
        self.assertEqual(engine.decide(keys, 'en').language, 'ru')
        engine.exclusions.add('абв')
        self.assertIsNone(engine.decide(keys, 'en'))

    def test_single_letter_unique_and_priority(self):
        forms = {'en': 'z', 'ru': 'я', 'he': 'ז'}
        engine, keys = engine_fixture(forms, known=('ru', 'he'))
        decision = engine.decide(keys, 'en')
        self.assertEqual((decision.language, decision.replacement, decision.reason),
                         ('ru', 'я', 'configured language priority'))
        engine.words['en'].add('z')
        self.assertIsNone(engine.decide(keys, 'en'))
        engine.words['en'].clear()
        engine.words['ru'].clear()
        engine.words['he'].clear()
        self.assertIsNone(engine.decide(keys, 'en'))

    def test_letter_in_other_layout_while_punctuation_in_source(self):
        forms = {'en': ',tksq', 'ru': 'белый', 'he': 'רתךדנ'}
        engine, keys = engine_fixture(forms, known=('ru',), confident=('ru',))
        keys[0] = (0xBC, False)
        engine.mapper.chars[(0xBC, 'en', False)] = ','
        engine.mapper.chars[(0xBC, 'ru', False)] = 'б'
        self.assertTrue(candidate_token_key(engine.mapper, 0xBC, False, 'en', False))
        self.assertTrue(_valid_typed(keys, forms['en'], 'en'))
        result = engine.decide(keys, 'en')
        self.assertEqual((result.replacement, result.language), ('белый', 'ru'))
        engine.words['en'].add(',tksq')
        self.assertIsNone(engine.decide(keys, 'en'))
        # Hebrew Q is punctuation whereas the other two layouts use a letter.
        engine.mapper.chars[(0x51, 'he', False)] = '/'
        engine.mapper.chars[(0x51, 'en', False)] = 'q'
        self.assertTrue(candidate_token_key(engine.mapper, 0x51, False, 'he', False))
        self.assertTrue(_valid_typed([(0x51, False)], '/', 'he'))
        engine.mapper.chars[(0x51, 'en', False)] = ''
        self.assertFalse(candidate_token_key(engine.mapper, 0x51, False, 'he', False))

    def test_ru_yo_and_hebrew_final_forms_and_additions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            values = {'ru.txt': 'ещё\nёлка\n', 'en.txt': 'hello\n',
                      'he.txt': 'שלום\n', 'confident_ru.txt': 'ещё\n',
                      'confident_en.txt': 'hello\n', 'confident_he.txt': 'שלום\n',
                      'he_custom.txt': 'מלך\n', 'exclude.txt': ''}
            for name, value in values.items():
                (root / name).write_text(value, encoding='utf-8')
            data = _read_lexical_data(root)
            self.assertIn('еще', data['words']['ru'])
            self.assertIn('елка', data['words']['ru'])
            self.assertNotIn('ещё', data['words']['ru'])
            self.assertIn('еще', data['confident']['ru'])
            self.assertIn('מלך', data['words']['he'])
            self.assertIn('מלך', data['confident']['he'])
            self.assertNotIn('מלכ', data['words']['he'])
            self.assertEqual(read_words(root / 'he_custom.txt', 'he'), {'מלך'})

    def test_live_early_four_letter_and_competing_prefix(self):
        forms = {'en': 'akuo', 'ru': 'флгщ', 'he': 'שלום'}
        engine, keys = fixture(forms, 'en', 'he', 'שלום')
        engine.conservative = True
        engine._confident = lambda lang, word: word in engine.confident[lang]
        self.assertIsNone(suggest_live_early(engine, keys[:3], 'en'))
        result = suggest_live_early(engine, keys, 'en')
        self.assertEqual((result.replacement, result.language, result.reason),
                         ('שלום', 'he', 'confident early prefix'))
        engine.words['ru'].add('флгщина')
        prepare_early(engine)
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))


class R16RegistryIntegrationTests(unittest.TestCase):
    def test_registry_addition_role_change_invalidates_cached_lexicon(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, cache = root / 'data', root / 'cache'
            data.mkdir()
            contents = {
                'ru.txt': 'дом\n', 'en.txt': 'hello\n', 'he.txt': 'שלום\n',
                'confident_ru.txt': 'дом\n', 'confident_en.txt': 'hello\n',
                'confident_he.txt': 'שלום\n', 'he_custom.txt': 'מלך\n',
                'exclude.txt': '',
            }
            for name, content in contents.items():
                (data / name).write_text(content, encoding='utf-8')
            descriptions = tuple(
                replace(language, additions=tuple(
                    replace(addition, for_confident=False)
                    for addition in language.additions))
                if language.code == 'he' else language
                for language in LANGUAGE_REGISTRY)
            with patch('engine.LayoutMapper', return_value=MapFixture({})):
                original = LanguageEngine(data, cache_dir=cache,
                                          engine_backend='legacy')
                warm = LanguageEngine(data, cache_dir=cache,
                                      engine_backend='legacy')
                with patch('engine.LANGUAGE_REGISTRY', descriptions):
                    changed = LanguageEngine(data, cache_dir=cache,
                                             engine_backend='legacy')
            self.assertEqual((original.cache_status, warm.cache_status,
                              changed.cache_status), ('built', 'hit', 'built'))
            self.assertIn('מלך', warm.confident['he'])
            self.assertNotIn('מלך', changed.confident['he'])

    def test_engine_compatibility_values_are_derived_from_descriptions(self):
        self.assertEqual(tuple(LANGS), ('ru', 'en', 'he'))
        self.assertIs(type(LANGS), dict)
        self.assertIs(type(ALTERNATIVE_PRIORITY), dict)
        self.assertIs(type(DICTIONARY_FILES), tuple)
        self.assertEqual(LANGS, {'ru': 0x0419, 'en': 0x0409, 'he': 0x040D})
        self.assertEqual(ALTERNATIVE_PRIORITY, {
            'ru': ('en', 'he'), 'en': ('ru', 'he'), 'he': ('ru', 'en')})
        self.assertEqual(DICTIONARY_FILES, (
            'ru.txt', 'en.txt', 'he.txt', 'confident_ru.txt',
            'confident_en.txt', 'confident_he.txt', 'he_custom.txt', 'exclude.txt'))
        self.assertEqual(LANGS, {item.code: item.langid for item in LANGUAGE_REGISTRY})
        self.assertEqual(ALTERNATIVE_PRIORITY,
                         {item.code: item.alternatives for item in LANGUAGE_REGISTRY})
        self.assertEqual(LANGUAGE_BY_CODE['ru'].unicode_script, 'CYRILLIC')
        self.assertEqual(LANGUAGE_BY_CODE['en'].unicode_script, 'LATIN')
        self.assertEqual(LANGUAGE_BY_CODE['he'].unicode_script, 'HEBREW')
        self.assertIn('ё', LANGUAGE_BY_CODE['ru'].alphabet)
        self.assertTrue(set('ךםןףץ') <= set(LANGUAGE_BY_CODE['he'].alphabet))
        self.assertEqual(select_loaded_layouts(
            [0x04190419, 0x04090409, 0xF03D040D], {}),
            {'ru': 0x04190419, 'en': 0x04090409, 'he': 0xF03D040D})

    def test_real_engine_loader_and_early_path_on_all_six_directions(self):
        """Exercises LanguageEngine loader, not just the registry in isolation."""
        cases = (
            ('ru', 'en', {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}),
            ('he', 'en', {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}),
            ('en', 'ru', {'ru': 'привет', 'en': 'ghbdtn', 'he': 'עינגאמ'}),
            ('he', 'ru', {'ru': 'привет', 'en': 'ghbdtn', 'he': 'עינגאמ'}),
            ('en', 'he', {'ru': 'флгщ', 'en': 'akuo', 'he': 'שלום'}),
            ('ru', 'he', {'ru': 'флгщ', 'en': 'akuo', 'he': 'שלום'}),
        )
        fallback = {'ru': 'дом', 'en': 'house', 'he': 'בית'}
        for source, target, forms in cases:
            with self.subTest(source=source, target=target):
                with tempfile.TemporaryDirectory() as folder:
                    data = Path(folder)
                    for language in LANGUAGE_REGISTRY:
                        word = forms[target] if language.code == target else fallback[language.code]
                        (data / language.dictionary_file).write_text(word + '\n', encoding='utf-8')
                        (data / language.confident_file).write_text(word + '\n', encoding='utf-8')
                    (data / 'he_custom.txt').write_text('', encoding='utf-8')
                    (data / 'exclude.txt').write_text('', encoding='utf-8')
                    mapper = MapFixture(forms)
                    with patch('engine.LayoutMapper', return_value=mapper):
                        instance = LanguageEngine(data)
                    keys = [(65 + index, False) for index in range(len(forms[source]))]
                    self.assertEqual((instance.decide_token(keys, source).replacement,
                                      instance.decide_token(keys, source).language),
                                     (forms[target], target))
                    prepare_early(instance)
                    live = suggest_live_early(instance, keys, source)
                    self.assertIsNotNone(live)
                    self.assertEqual((live.replacement, live.language),
                                     (forms[target], target))


if __name__ == '__main__':
    unittest.main()
