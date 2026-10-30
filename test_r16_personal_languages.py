"""External personal languages: v1 stays byte-compatible; v2 never drops data."""

import json
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from language_pack_loader import available_language_catalog
from test_r16_language_packs import write_pack
from user_lexicon import UserLexicon, LexiconLoadError, normalize


class PersonalLanguageTests(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.profile = base / 'profile'
        self.profile.mkdir()
        self.packs = base / 'packs'
        self.packs.mkdir()
        write_pack(self.packs, 'ar')
        write_pack(self.packs, 'es', langid=0x0C0A, script='LATIN',
                   alphabet='abcñ', word='niños\n')
        self.catalog = available_language_catalog(self.packs)

    def test_legacy_file_remains_version_one_and_cannot_use_external_without_catalog(self):
        lexicon = UserLexicon()
        lexicon.add('words', 'ru', 'Привет')
        with self.assertRaises(ValueError):
            lexicon.add('words', 'ar', 'مرحبا')
        lexicon.save(self.profile)
        file = self.profile / 'user_lexicon.json'
        first = file.read_bytes()
        self.assertEqual(json.loads(first)['version'], 1)
        loaded = UserLexicon.load(self.profile, catalog=self.catalog)
        loaded.save(self.profile)
        self.assertEqual(file.read_bytes(), first)

    def test_roundtrip_external_words_and_exclusions_and_preserves_legacy(self):
        lexicon = UserLexicon(catalog=self.catalog)
        lexicon.add('words', 'ru', 'Привет')
        lexicon.add('words', 'ar', 'مرحبا')
        lexicon.add('words', 'es', 'NIÑOS')
        lexicon.add('exclusions', 'es', 'Niños', sensitive=True)
        lexicon.save(self.profile)
        file = self.profile / 'user_lexicon.json'
        payload = json.loads(file.read_text(encoding='utf-8'))
        self.assertEqual(payload['version'], 1)
        self.assertEqual(set(payload['entries']['words']), {'ru', 'en', 'he'})
        sidecar = self.profile / 'user_lexicon_packages.json'
        package_payload = json.loads(sidecar.read_text(encoding='utf-8'))
        self.assertEqual(package_payload['version'], 1)
        self.assertEqual(set(package_payload['entries']['words']), {'ar', 'es'})
        loaded = UserLexicon.load(self.profile, catalog=self.catalog)
        self.assertTrue(loaded.contains('words', 'ar', 'مرحبا'))
        self.assertTrue(loaded.contains('words', 'es', 'niños'))
        self.assertTrue(loaded.contains('exclusions', 'es', 'Niños'))
        self.assertFalse(loaded.contains('exclusions', 'es', 'niños'))
        self.assertTrue(loaded.contains('words', 'ru', 'привет'))
        loaded.save(self.profile)
        self.assertEqual(json.loads(file.read_text(encoding='utf-8')), payload)
        self.assertEqual(json.loads(sidecar.read_text(encoding='utf-8')),
                         package_payload)

    def test_uninstalled_language_is_dormant_and_preserved_without_using_its_words(self):
        lexicon = UserLexicon(catalog=self.catalog)
        lexicon.add('words', 'ar', 'مرحبا')
        lexicon.save(self.profile)
        file = self.profile / 'user_lexicon.json'
        previous = file.read_bytes()
        sidecar = self.profile / 'user_lexicon_packages.json'
        external = sidecar.read_bytes()
        self.assertEqual(set(UserLexicon.load(self.profile).data['words']),
                         {'ru', 'en', 'he'})
        without_ar = {code: value for code, value in self.catalog.items() if code != 'ar'}
        dormant = UserLexicon.load(self.profile, catalog=without_ar)
        self.assertNotIn('ar', dormant.data['words'])
        self.assertIn('مرحبا', dormant.unavailable['words']['ar']['insensitive'])
        with self.assertRaises(ValueError):
            dormant.contains('words', 'ar', 'مرحبا')
        dormant.save(self.profile)
        self.assertEqual(file.read_bytes(), previous)
        self.assertEqual(sidecar.read_bytes(), external)
        valid_again = UserLexicon.load(self.profile, catalog=self.catalog)
        self.assertTrue(valid_again.contains('words', 'ar', 'مرحبا'))

    def test_corrupt_inactive_es_keeps_words_when_valid_ar_is_modified(self):
        lexicon = UserLexicon(catalog=self.catalog)
        lexicon.add('words', 'es', 'niños')
        lexicon.add('words', 'ar', 'مرحبا')
        lexicon.save(self.profile)
        old_es = json.loads((self.profile / 'user_lexicon_packages.json').read_text(
            encoding='utf-8'))['entries']['words']['es']
        without_es = {code: item for code, item in self.catalog.items() if code != 'es'}
        dormant = UserLexicon.load(self.profile, catalog=without_es)
        dormant.add('words', 'ar', 'كتاب')
        dormant.save(self.profile)
        updated = json.loads((self.profile / 'user_lexicon_packages.json').read_text(encoding='utf-8'))
        self.assertEqual(updated['entries']['words']['es'], old_es)
        restored = UserLexicon.load(self.profile, catalog=self.catalog)
        self.assertTrue(restored.contains('words', 'es', 'niños'))
        self.assertTrue(restored.contains('words', 'ar', 'كتاب'))

    def test_rejects_cross_script_or_invalid_words_and_duplicate_schema_rows(self):
        lexicon = UserLexicon(catalog=self.catalog)
        for lang, word in (('ar', 'hello'), ('es', 'مرحبا'), ('es', 'ñ1'),
                           ('ar', 'مرحبا hello')):
            with self.subTest(lang=lang, word=word), self.assertRaises(ValueError):
                lexicon.add('words', lang, word)
        self.assertEqual(normalize('NIÑOS', 'es', catalog=self.catalog), 'niños')
        lexicon.add('words', 'ar', 'مرحبا')
        lexicon.save(self.profile)
        target = self.profile / 'user_lexicon.json'
        target = self.profile / 'user_lexicon_packages.json'
        payload = json.loads(target.read_text(encoding='utf-8'))
        payload['entries']['words']['ar']['insensitive'].append('مرحبا')
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        with self.assertRaises(LexiconLoadError):
            UserLexicon.load(self.profile, catalog=self.catalog)

    def test_preserves_inactive_language_entries_when_active_set_changes(self):
        lexicon = UserLexicon(catalog=self.catalog)
        lexicon.add('words', 'ar', 'مرحبا')
        lexicon.add('words', 'es', 'niños')
        lexicon.save(self.profile)
        loaded = UserLexicon.load(self.profile, catalog=self.catalog)
        # Active set is intentionally independent of saved personal words.
        loaded.add('words', 'ru', 'дом')
        loaded.save(self.profile)
        again = UserLexicon.load(self.profile, catalog=self.catalog)
        self.assertTrue(again.contains('words', 'ar', 'مرحبا'))
        self.assertTrue(again.contains('words', 'es', 'niños'))
        self.assertTrue(again.contains('words', 'ru', 'дом'))

    def test_lookup_does_not_create_empty_external_sidecar(self):
        lexicon = UserLexicon(catalog=self.catalog)
        self.assertFalse(lexicon.contains('words', 'ar', 'مرحبا'))
        self.assertEqual(set(lexicon.data['words']), {'ru', 'en', 'he'})
        lexicon.save(self.profile)
        self.assertFalse((self.profile / 'user_lexicon_packages.json').exists())

    def test_oversized_external_file_is_bounded_during_read_and_does_not_reset_personal_data(self):
        from user_lexicon import _MAX_BYTES
        from unittest.mock import Mock
        lexicon = UserLexicon(catalog=self.catalog)
        lexicon.add('words', 'ru', 'дом')
        lexicon.add('words', 'ar', 'مرحبا')
        lexicon.save(self.profile)
        primary = (self.profile / 'user_lexicon.json').read_bytes()
        sidecar = self.profile / 'user_lexicon_packages.json'
        # Size is exactly the limit to reach the bounded stream read; the
        # guard still rejects when the stream returns one extra byte.
        real_open = type(sidecar).open
        calls = []
        def bounded_open(path, *args, **kwargs):
            if path == sidecar:
                f = Mock()
                f.__enter__ = Mock(return_value=f)
                f.__exit__ = Mock(return_value=False)
                def read(limit):
                    calls.append(limit)
                    return b'x' * (_MAX_BYTES + 1)
                f.read.side_effect = read
                return f
            return real_open(path, *args, **kwargs)
        with patch.object(type(sidecar), 'open', bounded_open):
            with self.assertRaises(LexiconLoadError) as failure:
                UserLexicon.load(self.profile, catalog=self.catalog)
        self.assertIsNone(failure.exception.digest)
        self.assertEqual(calls, [_MAX_BYTES + 1])
        self.assertEqual((self.profile / 'user_lexicon.json').read_bytes(), primary)


if __name__ == '__main__':
    unittest.main()
