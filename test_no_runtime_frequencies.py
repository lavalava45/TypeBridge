"""Numeric frequency TSVs are preserved as data but unused by live selection."""
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import engine
from dictionary_diagnostics import dictionary_report


class NoRuntimeFrequenciesTests(TestCase):
    def test_missing_confidence_dictionary_fails_loudly_before_hook(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name in ('ru', 'en', 'he', 'he_custom', 'exclude'):
                (base / f'{name}.txt').write_text('', encoding='utf-8')
            for lang in ('en', 'he'):
                (base / f'confident_{lang}.txt').write_text('word\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'confident_ru.txt'):
                engine.LanguageEngine(base)

    def test_empty_confidence_dictionary_fails_before_hook(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name in ('ru', 'en', 'he', 'he_custom', 'exclude'):
                (base / f'{name}.txt').write_text('слово\n' if name == 'ru' else '', encoding='utf-8')
            for lang, word in (('ru', 'привет'), ('en', 'hello'), ('he', 'שלום')):
                (base / f'confident_{lang}.txt').write_text(word + '\n', encoding='utf-8')
            (base / 'confident_en.txt').write_text(' \n # comment only\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'confident_en.txt'):
                engine.LanguageEngine(base)

    def test_engine_initialization_never_opens_numeric_frequency_tables(self):
        # Real RU/EN/HE/confident dictionaries still load. Check that even a
        # broken manifest cannot affect startup or force numerical TSV reads.
        base = engine.source_data_dir()
        original = Path.read_bytes

        def deny_frequency_files(path):
            if path.name.startswith('frequency_') or path.name == 'FREQUENCY-SOURCES.json':
                raise AssertionError(f'Live startup tried to read {path.name}')
            return original(path)

        with patch.object(Path, 'read_bytes', autospec=True, side_effect=deny_frequency_files):
            loaded = engine.LanguageEngine(base)
            text = '\n'.join(dictionary_report(loaded, base))
        self.assertEqual(set(loaded.words), {'ru', 'en', 'he'})
        self.assertEqual(set(loaded.confident), {'ru', 'en', 'he'})
        self.assertFalse(hasattr(loaded, 'frequencies'))
        self.assertNotIn('frequency_', text)
        self.assertNotIn('Частоты', text)

    def test_numeric_frequency_files_are_optional_for_runtime(self):
        # Synthetic minimal dictionary fixture deliberately has no frequency
        # manifest or TSV files; the engine must still initialize normally.
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name in ('ru', 'en', 'he', 'he_custom', 'exclude'):
                (base / f'{name}.txt').write_text('слово\n' if name == 'ru' else '', encoding='utf-8')
            for lang in ('ru', 'en', 'he'):
                (base / f'confident_{lang}.txt').write_text(
                    {'ru': 'привет', 'en': 'hello', 'he': 'שלום'}[lang] + '\n', encoding='utf-8')
            obj = engine.LanguageEngine(base)
            self.assertIn('слово', obj.words['ru'])
            self.assertFalse(hasattr(obj, 'frequencies'))
