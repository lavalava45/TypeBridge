"""R16/3b/2 model engine integration, without Windows hooks or physical input."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from engine import (LanguageEngine, LayoutMapper, select_loaded_layouts,
                    _one_script_letters)
from incremental_recognition import (cached_early_index, prepare_early,
                                     suggest_live_early)
from language_pack_loader import available_language_catalog
from test_r16_language_packs import write_pack
from user_lexicon import UserLexicon


class SyntheticMapper:
    def __init__(self, forms):
        self.forms = forms

    def word(self, keys, language):
        return self.forms[language][:len(keys)]

    def char(self, vk, language, shift=False):
        return self.forms[language][vk - 65] if 0 <= vk - 65 < len(self.forms[language]) else ''


class DynamicEngineTests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(TemporaryDirectory()))
        self.data = folder / 'data'
        self.data.mkdir()
        self.pack_root = folder / 'packs'
        self.pack_root.mkdir()
        self.cache = folder / 'cache'
        (self.data / 'exclude.txt').write_text('', encoding='utf-8')

    def _builtin(self, code, word):
        (self.data / f'{code}.txt').write_text(word + '\n', encoding='utf-8')
        (self.data / f'confident_{code}.txt').write_text(word + '\n', encoding='utf-8')

    def _engine(self, selected, forms, *, cache=False, lexicon=None):
        mapper = SyntheticMapper(forms)
        with patch('engine.LayoutMapper', return_value=mapper) as constructor:
            engine = LanguageEngine(
                self.data, pack_root=self.pack_root, active_languages=selected,
                cache_dir=self.cache if cache else None, user_lexicon=lexicon)
        constructor.assert_called_once()
        self.assertEqual(constructor.call_args.kwargs['active_languages'], selected)
        self.assertEqual(engine.mapper, mapper)
        return engine

    def test_arabic_complete_word_and_before_space_model_decision(self):
        self._builtin('en', 'hello')
        write_pack(self.pack_root, 'ar', word='مرحبا\n')
        engine = self._engine(('en', 'ar'), {'en': 'xxxxx', 'ar': 'مرحبا'}, cache=True)
        keys = [(65 + i, False) for i in range(5)]
        result = engine.decide_token(keys, 'en')
        self.assertIsNotNone(result)
        self.assertEqual((result.language, result.replacement), ('ar', 'مرحبا'))
        prepare_early(engine)
        early = suggest_live_early(engine, keys, 'en')
        self.assertIsNotNone(early)
        self.assertEqual((early.language, early.replacement), ('ar', 'مرحبا'))
        self.assertEqual(set(engine._early_base_index), {'en', 'ar'})

    def test_arabic_user_lexicon_missing_external_bucket_is_safe(self):
        self._builtin('en', 'hello')
        write_pack(self.pack_root, 'ar', word='مرحبا\n')
        lexicon = UserLexicon()  # Existing schema has only ru/en/he.
        engine = self._engine(('en', 'ar'), {'en': 'xxxxx', 'ar': 'مرحبا'}, lexicon=lexicon)
        prepare_early(engine)
        self.assertEqual(set(cached_early_index(engine, lexicon)), {'en', 'ar'})
        self.assertEqual(suggest_live_early(engine, [(65 + i, False) for i in range(5)],
                                            'en').language, 'ar')
        self.assertFalse(engine._user_contains('words', 'ar', 'مرحبا'))

    def test_spanish_latin_shared_script_unique_word_and_early(self):
        self._builtin('en', 'hello')
        write_pack(self.pack_root, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='niños\n')
        engine = self._engine(('en', 'es'), {'en': 'xxxxx', 'es': 'niños'}, cache=True)
        self.assertTrue(_one_script_letters('niños', engine.language_catalog))
        keys = [(65 + i, False) for i in range(5)]
        decision = engine.decide_token(keys, 'en')
        self.assertEqual((decision.language, decision.replacement), ('es', 'niños'))
        prepare_early(engine)
        early = suggest_live_early(engine, keys, 'en')
        self.assertEqual((early.language, early.replacement), ('es', 'niños'))

    def test_known_english_source_vetoes_spanish_replacement(self):
        self._builtin('en', 'xxxxx')
        write_pack(self.pack_root, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='niños\n')
        engine = self._engine(('en', 'es'), {'en': 'xxxxx', 'es': 'niños'})
        keys = [(65 + i, False) for i in range(5)]
        self.assertIsNone(engine.decide_token(keys, 'en'))
        prepare_early(engine)
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))

    def test_no_guess_when_two_external_targets_match(self):
        self._builtin('en', 'hello')
        write_pack(self.pack_root, 'ar', word='مرحبا\n')
        write_pack(self.pack_root, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='niños\n')
        forms = {'en': 'xxxxx', 'ar': 'مرحبا', 'es': 'niños'}
        engine = self._engine(('en', 'ar', 'es'), forms)
        keys = [(65 + i, False) for i in range(5)]
        self.assertIsNone(engine.decide_token(keys, 'en'))
        prepare_early(engine)
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))

    def test_no_guess_for_disabled_language_even_if_installed(self):
        self._builtin('en', 'hello')
        write_pack(self.pack_root, 'ar', word='مرحبا\n')
        write_pack(self.pack_root, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='niños\n')
        engine = self._engine(('en', 'ar'), {'en': 'xxxxx', 'ar': 'مرحبا'})
        self.assertNotIn('es', engine.words)
        self.assertNotIn('es', engine.language_priority.get('en', ()))
        self.assertIsNone(engine.decide_token([(65 + i, False) for i in range(5)], 'es'))

    def test_corrupt_package_refuses_engine_before_mapper_creation(self):
        self._builtin('en', 'hello')
        folder, _ = write_pack(self.pack_root, 'ar', word='مرحبا\n')
        (folder / 'words.txt').write_text('tampered\n', encoding='utf-8')
        with patch('engine.LayoutMapper') as mapper:
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                LanguageEngine(self.data, pack_root=self.pack_root,
                               active_languages=('en', 'ar'), cache_dir=self.cache)
            mapper.assert_not_called()

    def test_explicit_catalog_required_to_activate_external_codes(self):
        self._builtin('en', 'hello')
        write_pack(self.pack_root, 'ar', word='مرحبا\n')
        with patch('engine.LayoutMapper') as mapper:
            with self.assertRaises(ValueError):
                LanguageEngine(self.data, active_languages=('en', 'ar'))
            with self.assertRaisesRegex(ValueError, 'explicit active_languages'):
                LanguageEngine(self.data, pack_root=self.pack_root)
            mapper.assert_not_called()

    def test_loaded_layout_selection_uses_external_langid_without_os_changes(self):
        write_pack(self.pack_root, 'ar', word='مرحبا\n')
        catalog = available_language_catalog(self.pack_root)
        selected = select_loaded_layouts(
            [0x04090409, 0x04010401], {}, ('en', 'ar'), catalog)
        self.assertEqual(selected, {'en': 0x04090409, 'ar': 0x04010401})
        with self.assertRaisesRegex(OSError, 'AR keyboard layout'):
            select_loaded_layouts([0x04090409], {}, ('en', 'ar'), catalog)

    def test_layout_mapper_verifies_existing_arabic_hkl_without_binding_new_one(self):
        write_pack(self.pack_root, 'ar', word='مرحبا\n')
        catalog = available_language_catalog(self.pack_root)
        api = Mock()
        def get_list(count, target):
            if target is not None:
                target[0], target[1] = 0x04090409, 0x04010401
            return 2
        api.GetKeyboardLayoutList.side_effect = get_list
        def unicode_map(vk, scan, state, output, size, flags, hkl):
            output.value = 'م' if hkl & 0xFFFF == 0x0401 else 'a'
            return 1
        api.ToUnicodeEx.side_effect = unicode_map
        with (patch('engine.ctypes.WinDLL', return_value=api),
              patch('engine.configured_layout_entries', return_value={})):
            mapper = LayoutMapper(active_languages=('en', 'ar'), catalog=catalog)
        self.assertEqual(mapper.layouts['ar'], 0x04010401)
        api.LoadKeyboardLayoutW.assert_not_called()


if __name__ == '__main__':
    unittest.main()
