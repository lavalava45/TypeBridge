"""Package-declared personal alphabet rules; no user data, HKL or hook."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from language_pack_loader import available_language_catalog
from language_pack_v2 import load_v2_pack
from user_lexicon import UserLexicon, normalize


def fixture_v2(root, code, langid, script, alphabet, word, personal_policy):
    folder = root / code
    folder.mkdir()
    content = {
        'words.txt': word.encode('utf-8'),
        'confident.txt': word.encode('utf-8'),
        'LICENSE.txt': b'Isolated test data - CC0-1.0\n',
    }
    for name, payload in content.items():
        (folder / name).write_bytes(payload)
    manifest = {
        'schema_version': 2, 'package_version': '1.0.0',
        'code': code, 'name': f'Fixture {code}', 'langid': langid,
        'unicode_script': script, 'alphabet': alphabet,
        'normalization': {'dictionary': 'casefold'},
        'dictionary_file': 'words.txt', 'confident_file': 'confident.txt',
        'additions': [], 'alternatives': ['en'],
        'behavior': {
            'ambiguous_destinations': 'reject_multiple',
            'early_prefix_aliases': [], 'source_nonletter_keys': [],
            'personal_alphabet_policy': personal_policy,
        },
        'source': {'name': 'Unit fixture', 'url': '', 'license_id': 'CC0-1.0',
                   'license_file': 'LICENSE.txt'},
        'files': {name: {'bytes': len(payload),
                          'sha256': hashlib.sha256(payload).hexdigest()}
                  for name, payload in content.items()},
    }
    (folder / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False) + '\n', encoding='utf-8')
    return folder


class PackagePersonalAlphabetTests(TestCase):
    def test_two_nonlegacy_codes_enforce_different_declared_policies(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            packages = base / 'language_packs'
            packages.mkdir()
            greek = fixture_v2(packages, 'el', 0x0408, 'GREEK',
                               'αβγδεζηθικλμνξοπρστυφχψω', 'κόσμος\n',
                               'unicode_script_name')
            latin = fixture_v2(packages, 'pt', 0x0416, 'LATIN',
                               'abcdefghijklmnopqrstuvwxyz', 'ola\n',
                               'ascii_latin_letters')
            self.assertEqual(load_v2_pack(greek).description.personal_alphabet_policy,
                             'unicode_script_name')
            self.assertEqual(load_v2_pack(latin).description.personal_alphabet_policy,
                             'ascii_latin_letters')
            catalog = available_language_catalog(packages)
            self.assertEqual(normalize('ΚΌΣΜΟΣ', 'el', catalog=catalog), 'κόσμοσ')
            self.assertEqual(normalize('OLA', 'pt', catalog=catalog), 'ola')
            for code, invalid in [('el', 'hello'), ('pt', 'café'), ('pt', 'κόσμος')]:
                with self.subTest(code=code, invalid=invalid), self.assertRaises(ValueError):
                    normalize(invalid, code, catalog=catalog)

    def test_roundtrip_and_uninstall_preserve_v2_personal_entries(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            packages = base / 'language_packs'
            packages.mkdir()
            fixture_v2(packages, 'el', 0x0408, 'GREEK',
                       'αβγδεζηθικλμνξοπρστυφχψω', 'κόσμος\n',
                       'unicode_script_name')
            catalog = available_language_catalog(packages)
            profile = base / 'profile'
            profile.mkdir()
            personal = UserLexicon(catalog=catalog)
            personal.add('words', 'el', 'ΚΌΣΜΟΣ')
            personal.add('exclusions', 'el', 'Λέξη', sensitive=True)
            personal.save(profile)
            legacy = (profile / 'user_lexicon.json').read_bytes()
            sidecar = (profile / 'user_lexicon_packages.json').read_bytes()
            self.assertEqual(json.loads(legacy)['version'], 1)
            reloaded = UserLexicon.load(profile, catalog=catalog)
            self.assertTrue(reloaded.contains('words', 'el', 'κόσμος'))
            absent = {code: item for code, item in catalog.items() if code != 'el'}
            dormant = UserLexicon.load(profile, catalog=absent)
            self.assertIn('el', dormant.unavailable['words'])
            with self.assertRaises(ValueError):
                dormant.contains('words', 'el', 'κόσμος')
            dormant.save(profile)
            self.assertEqual((profile / 'user_lexicon.json').read_bytes(), legacy)
            self.assertEqual((profile / 'user_lexicon_packages.json').read_bytes(), sidecar)
            self.assertTrue(UserLexicon.load(profile, catalog=catalog).contains(
                'words', 'el', 'κόσμος'))

