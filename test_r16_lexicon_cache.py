"""R16/3b/1: external+legacy selected data with isolated cache; no hooks/HKL."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from dictionary_cache import load_or_build, load_or_build_sources
from engine import _read_lexical_data
from language_lexicon import load_selected_lexicon
from test_r16_language_packs import save_manifest, write_pack


LEGACY = {
    'ru.txt': 'ещё\nдом\n', 'en.txt': 'hello\n', 'he.txt': 'שלום\n',
    'confident_ru.txt': 'ещё\n', 'confident_en.txt': 'hello\n',
    'confident_he.txt': 'שלום\n', 'he_custom.txt': 'מלך\n',
    'exclude.txt': 'forbidden\n',
}


def write_legacy(path, names=None):
    path.mkdir()
    for name in (LEGACY if names is None else names):
        (path / name).write_text(LEGACY[name], encoding='utf-8')


class SelectedLexiconTests(unittest.TestCase):
    def test_one_selected_package_validation_per_engine_initialization(self):
        from unittest.mock import patch
        from engine import LanguageEngine
        from language_pack_loader import _text_hash
        from test_r16_dynamic_engine import SyntheticMapper
        for name in ('en.txt', 'confident_en.txt', 'exclude.txt'):
            (self.data / name).write_text(LEGACY[name], encoding='utf-8')
        write_pack(self.packs, 'ar')
        write_pack(self.packs, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='niños\n')
        checked = []
        def count(path, *args, **kwargs):
            checked.append((path.parent.name, path.name))
            return _text_hash(path, *args, **kwargs)
        with (patch('language_pack_loader._text_hash', side_effect=count),
              patch('engine.LayoutMapper', return_value=SyntheticMapper(
                  {'en': 'hello', 'ar': 'مرحبا'}))):
            first = LanguageEngine(self.data, active_languages=('en', 'ar'),
                                   pack_root=self.packs, cache_dir=self.cache)
            second = LanguageEngine(self.data, active_languages=('en', 'ar'),
                                    pack_root=self.packs, cache_dir=self.cache)
        expected = {('ar', filename) for filename in
                    ('words.txt','confident.txt','LICENSE.txt')}
        self.assertEqual(set(checked), expected)
        self.assertEqual(len(checked), 6)  # once per file per engine creation
        self.assertFalse(any(code == 'es' for code, _ in checked))
        self.assertEqual((first.cache_status, second.cache_status), ('built', 'hit'))

    def test_corrupt_disabled_es_never_read_by_selected_ru_ar_lexicon(self):
        from unittest.mock import patch
        for name in ('ru.txt', 'confident_ru.txt', 'exclude.txt'):
            (self.data / name).write_text(LEGACY[name], encoding='utf-8')
        write_pack(self.packs, 'ar')
        invalid, _ = write_pack(self.packs, 'es', langid=0x0C0A,
                                script='LATIN', alphabet='abcñ', word='niños\n')
        (invalid / 'words.txt').write_text('tampered\n', encoding='utf-8')
        from language_pack_loader import load_language_pack
        validated = []
        def observe(path):
            validated.append(Path(path).name)
            return load_language_pack(path)
        with patch('language_pack_loader.load_language_pack', side_effect=observe):
            data, _ = load_selected_lexicon(self.data, self.packs, ('ru', 'ar'),
                                            cache_dir=self.cache)
        self.assertEqual(validated, ['ar'])
        self.assertEqual(set(data['words']), {'ru','ar'})
        self.assertFalse((self.packs / 'es' / 'words.txt').is_symlink())
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.data = root / 'data'
        self.packs = root / 'language_packs'
        self.packs.mkdir()
        self.cache = root / 'dictionary-cache'
        self.data.mkdir()

    def test_original_three_language_snapshot_is_identical(self):
        for name, data in LEGACY.items():
            (self.data / name).write_text(data, encoding='utf-8')
        legacy = _read_lexical_data(self.data)
        new, status = load_selected_lexicon(self.data, self.packs,
                                             ('ru', 'en', 'he'))
        self.assertEqual(status, 'disabled')
        self.assertEqual(new, legacy)
        self.assertIn('еще', new['words']['ru'])
        self.assertIn('מלך', new['confident']['he'])

    def test_mixed_ru_ar_only_requires_ru_and_ar_data(self):
        for name in ('ru.txt', 'confident_ru.txt', 'exclude.txt'):
            (self.data / name).write_text(LEGACY[name], encoding='utf-8')
        write_pack(self.packs, 'ar', word='مرحبا\n')
        snapshot, status = load_selected_lexicon(self.data, self.packs,
                                                  ('ru', 'ar'))
        self.assertEqual(status, 'disabled')
        self.assertEqual(set(snapshot['words']), {'ru', 'ar'})
        self.assertEqual(set(snapshot['confident']), {'ru', 'ar'})
        self.assertIn('مرحبا', snapshot['words']['ar'])
        self.assertIn('مرحبا', snapshot['confident']['ar'])
        self.assertEqual(snapshot['exclusions'], {'forbidden'})
        self.assertIn('еще', snapshot['confident']['ru'])
        self.assertFalse((self.data / 'he.txt').exists())

    def test_external_latin_casefold_lexicon_preparation(self):
        (self.data / 'exclude.txt').write_text('', encoding='utf-8')
        write_pack(self.packs, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='NIÑO\n')
        snapshot, status = load_selected_lexicon(self.data, self.packs, ('es',))
        self.assertEqual(status, 'disabled')
        self.assertEqual(snapshot['words']['es'], {'niño'})
        self.assertEqual(snapshot['confident']['es'], {'niño'})

    def test_v2_warm_invalidate_on_signed_edit_and_isolate_active_sets(self):
        for name in ('ru.txt', 'confident_ru.txt', 'exclude.txt'):
            (self.data / name).write_text(LEGACY[name], encoding='utf-8')
        directory, manifest = write_pack(self.packs, 'ar')
        cold, status = load_selected_lexicon(self.data, self.packs,
                                              ('ru', 'ar'), cache_dir=self.cache)
        warm, status2 = load_selected_lexicon(self.data, self.packs,
                                               ('ru', 'ar'), cache_dir=self.cache)
        self.assertEqual((status, status2), ('built', 'hit'))
        self.assertEqual(cold, warm)
        other, other_status = load_selected_lexicon(self.data, self.packs,
                                                    ('ar', 'ru'), cache_dir=self.cache)
        self.assertEqual(other_status, 'built')
        self.assertEqual(cold, other)
        self.assertEqual(len(tuple(self.cache.glob('words-v2-*.cache'))), 2)
        self.assertFalse((self.cache / 'words-v1.cache').exists())
        new_words = 'مرحبا\nكتاب\n'.encode('utf-8')
        (directory / 'words.txt').write_bytes(new_words)
        manifest['files']['words.txt'] = {
            'bytes': len(new_words), 'sha256': hashlib.sha256(new_words).hexdigest()}
        save_manifest(directory, manifest)
        changed, status3 = load_selected_lexicon(self.data, self.packs,
                                                  ('ru', 'ar'), cache_dir=self.cache)
        self.assertEqual(status3, 'built')
        self.assertIn('كتاب', changed['words']['ar'])
        self.assertNotIn('كتاب', warm['words']['ar'])

    def test_modified_pack_denied_even_on_warm_cache(self):
        (self.data / 'exclude.txt').write_text('', encoding='utf-8')
        directory, _ = write_pack(self.packs, 'ar')
        load_selected_lexicon(self.data, self.packs, ('ar',), cache_dir=self.cache)
        (directory / 'words.txt').write_text('tampered\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            load_selected_lexicon(self.data, self.packs, ('ar',), cache_dir=self.cache)

    def test_missing_selected_builtin_confidence_is_fatal(self):
        for name in ('ru.txt', 'exclude.txt'):
            (self.data / name).write_text(LEGACY[name], encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'confident_ru.txt'):
            load_selected_lexicon(self.data, self.packs, ('ru',), cache_dir=self.cache)
        self.assertFalse(self.cache.exists())

    def test_invalid_word_wrong_script_is_not_silently_accepted(self):
        (self.data / 'exclude.txt').write_text('', encoding='utf-8')
        directory, manifest = write_pack(self.packs, 'ar')
        bad = 'latin\n'.encode('utf-8')
        (directory / 'words.txt').write_bytes(bad)
        manifest['files']['words.txt'] = {
            'bytes': len(bad), 'sha256': hashlib.sha256(bad).hexdigest()}
        save_manifest(directory, manifest)
        with self.assertRaisesRegex(ValueError, 'Unsupported language pack word'):
            load_selected_lexicon(self.data, self.packs, ('ar',), cache_dir=self.cache)
        self.assertFalse(self.cache.exists())

    def test_legacy_v1_cache_and_selected_v2_coexist(self):
        for name, data in LEGACY.items():
            (self.data / name).write_text(data, encoding='utf-8')
        from unittest.mock import Mock
        builder = Mock(return_value=_read_lexical_data(self.data))
        filenames = tuple(LEGACY)
        old_snapshot, status = load_or_build(self.data, filenames,
                                              'legacy-test', builder, self.cache)
        legacy_file = self.cache / 'words-v1.cache'
        digest = hashlib.sha256(legacy_file.read_bytes()).hexdigest()
        selected, status2 = load_selected_lexicon(self.data, self.packs,
                                                  ('en', 'ru'), cache_dir=self.cache)
        self.assertEqual((status, status2), ('built', 'built'))
        self.assertEqual(hashlib.sha256(legacy_file.read_bytes()).hexdigest(), digest)
        again, status3 = load_or_build(self.data, filenames,
                                       'legacy-test', builder, self.cache)
        self.assertEqual(status3, 'hit')
        self.assertEqual(old_snapshot, again)
        self.assertEqual(set(selected['words']), {'en', 'ru'})
        builder.assert_called_once()

    def test_rejects_more_than_three_active_even_if_four_packages_verified(self):
        (self.data / 'exclude.txt').write_text('', encoding='utf-8')
        for code, langid, script, alphabet, word in (
            ('ar', 0x0401, 'ARABIC', 'ابتث', 'مرحبا\n'),
            ('es', 0x0C0A, 'LATIN', 'abcñ', 'hola\n'),
            ('fr', 0x040C, 'LATIN', 'abcdé', 'bonjour\n'),
            ('de', 0x0407, 'LATIN', 'abcdö', 'hallo\n'),
        ):
            write_pack(self.packs, code, langid=langid, script=script,
                       alphabet=alphabet, word=word)
        with self.assertRaisesRegex(ValueError, 'one to three'):
            load_selected_lexicon(self.data, self.packs,
                                  ('ar', 'es', 'fr', 'de'), cache_dir=self.cache)

    def test_corrupt_v2_cache_rebuilds_without_touching_other_profiles(self):
        (self.data / 'exclude.txt').write_text('', encoding='utf-8')
        write_pack(self.packs, 'ar')
        first, status = load_selected_lexicon(self.data, self.packs,
                                              ('ar',), cache_dir=self.cache)
        self.assertEqual(status, 'built')
        path, = tuple(self.cache.glob('words-v2-*.cache'))
        path.write_bytes(b'corrupt')
        second, status2 = load_selected_lexicon(self.data, self.packs,
                                                ('ar',), cache_dir=self.cache)
        self.assertEqual(status2, 'built')
        self.assertEqual(second, first)
        self.assertFalse((self.cache / 'words-v1.cache').exists())

    def test_source_changed_during_build_does_not_publish_v2_cache(self):
        source = self.data / 'source.txt'
        source.write_text('original', encoding='utf-8')
        def change_source():
            source.write_text('modified', encoding='utf-8')
            return {'words': {'ar': {'مرحبا'}},
                    'confident': {'ar': {'مرحبا'}}, 'exclusions': set()}
        with self.assertRaisesRegex(ValueError, 'sources changed'):
            load_or_build_sources((('ar/source.txt', source),), 'test-policy',
                                  change_source, self.cache, ('ar',))
        self.assertFalse(self.cache.exists())


if __name__ == '__main__':
    unittest.main()
