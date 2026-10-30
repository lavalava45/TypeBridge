"""R16/3a: isolated untrusted pack fixtures; no hooks, downloads or real data."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout

from language_pack_loader import (MAX_DICTIONARY_BYTES, MAX_MANIFEST_BYTES,
                                  available_language_catalog, discover_language_packs,
                                  load_language_pack, main)
from language_registry import (DEFAULT_ACTIVE_LANGUAGES, MAX_ACTIVE_LANGUAGES,
                               validate_active_languages)


EXTERNALS = (
    ('ar', 0x0401, 'ARABIC', 'ابتث', 'مرحبا\n'),
    ('es', 0x0C0A, 'LATIN', 'abcñ', 'hola\n'),
    ('fr', 0x040C, 'LATIN', 'abcdé', 'bonjour\n'),
    ('de', 0x0407, 'LATIN', 'abcdö', 'hallo\n'),
)


def write_pack(root, code='ar', *, langid=0x0401, script='ARABIC',
               alphabet='ابتث', word='مرحبا\n'):
    directory = Path(root) / code
    directory.mkdir()
    payloads = {
        'words.txt': word.encode('utf-8'),
        'confident.txt': word.encode('utf-8'),
        'LICENSE.txt': b'Fixture license: CC0-1.0\n',
    }
    for filename, payload in payloads.items():
        (directory / filename).write_bytes(payload)
    manifest = {
        'schema_version': 1,
        'package_version': '1.0.0',
        'code': code,
        'name': f'Fixture {code.upper()}',
        'langid': langid,
        'unicode_script': script,
        'alphabet': alphabet,
        'normalization': 'casefold',
        'dictionary_file': 'words.txt',
        'confident_file': 'confident.txt',
        'additions': [],
        'alternatives': ['ru', 'en'],
        'source': {'name': 'Test fixture', 'url': 'https://example.org/fixture',
                   'license_id': 'CC0-1.0', 'license_file': 'LICENSE.txt'},
        'files': {
            name: {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
            for name, payload in payloads.items()
        },
    }
    save_manifest(directory, manifest)
    return directory, manifest


def save_manifest(directory, manifest):
    (directory / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class LanguagePackTests(unittest.TestCase):
    def test_broken_inactive_es_is_isolated_but_selected_es_still_fails(self):
        with TemporaryDirectory() as folder:
            write_pack(folder, 'ar')
            es, _ = write_pack(folder, 'es', langid=0x0C0A,
                               script='LATIN', alphabet='abcñ', word='niños\n')
            (es / 'words.txt').write_bytes(b'broken dictionary\n')
            errors = {}
            catalog = available_language_catalog(folder, tolerant=True, errors=errors)
            self.assertEqual(set(catalog), {'ru','en','he','ar'})
            self.assertIn('es', errors)
            self.assertIn('ar', catalog)
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                discover_language_packs(folder, selected_codes=('es',))
            with self.assertRaises(ValueError):
                available_language_catalog(folder)  # Author validation is strict.

    def test_selected_codes_cannot_escape_pack_root(self):
        with TemporaryDirectory() as folder:
            for malicious in ('../outside','..', 'ar/../es', 'AR', '\\escape'):
                with self.subTest(malicious=malicious), self.assertRaises(ValueError):
                    discover_language_packs(folder, selected_codes=(malicious,))

    def test_builtin_behavior_and_three_active_limit(self):
        self.assertEqual(MAX_ACTIVE_LANGUAGES, 3)
        self.assertEqual(validate_active_languages(DEFAULT_ACTIVE_LANGUAGES),
                         ('ru', 'en', 'he'))
        self.assertEqual(validate_active_languages(('en',)), ('en',))
        with self.assertRaises(ValueError):
            validate_active_languages(('ru', 'en', 'he', 'ar'),
                                      available_codes=('ru', 'en', 'he', 'ar'))
        with self.assertRaises(ValueError):
            validate_active_languages(('ar',))  # Not part of built-in runtime.

    def test_all_installed_packages_discovered_but_only_three_can_be_active(self):
        with TemporaryDirectory() as folder:
            for code, langid, script, alphabet, word in EXTERNALS:
                write_pack(folder, code, langid=langid, script=script,
                           alphabet=alphabet, word=word)
            packs = discover_language_packs(folder)
            self.assertEqual(tuple(sorted(packs)), ('ar', 'de', 'es', 'fr'))
            catalog = available_language_catalog(folder)
            self.assertEqual(tuple(catalog)[:3], ('ru', 'en', 'he'))
            self.assertEqual(len(catalog), 7)
            self.assertEqual(validate_active_languages(('ru', 'ar', 'es'), catalog),
                             ('ru', 'ar', 'es'))
            self.assertEqual(validate_active_languages(('fr', 'de'), catalog),
                             ('fr', 'de'))
            with self.assertRaises(ValueError):
                validate_active_languages(('ru', 'ar', 'es', 'fr'), catalog)
            with self.assertRaises(ValueError):
                validate_active_languages(('xx',), catalog)
            self.assertEqual(packs['ar'].description.alternatives, ('ru', 'en'))

    def test_no_installed_directory_does_not_create_files(self):
        with TemporaryDirectory() as folder:
            absent = Path(folder) / 'language_packs'
            self.assertEqual(discover_language_packs(absent), {})
            self.assertFalse(absent.exists())
            self.assertEqual(tuple(available_language_catalog(absent)), ('ru', 'en', 'he'))

    def test_readonly_cli_lists_external_metadata_but_marks_it_disabled(self):
        with TemporaryDirectory() as folder:
            write_pack(folder)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['--root', folder]), 0)
            self.assertIn('ar: Fixture AR', output.getvalue())
            self.assertIn('verified package, not enabled', output.getvalue())
            self.assertIn('maximum three active', output.getvalue())
            self.assertFalse((Path(folder) / 'settings.json').exists())

    def test_manifest_duplicate_json_keys_rejected(self):
        with TemporaryDirectory() as folder:
            directory, _ = write_pack(folder)
            (directory / 'manifest.json').write_text('{"schema_version":1,"schema_version":1}',
                                                     encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate JSON'):
                load_language_pack(directory)

    def test_rejects_path_escape_absolute_and_nontext_payload(self):
        for bad in ('../secrets.txt', 'C:\\data\\leak.txt', '/tmp/leak.txt',
                    'words.py', 'payload.exe', '.hidden.txt'):
            with self.subTest(bad=bad), TemporaryDirectory() as folder:
                directory, manifest = write_pack(folder)
                manifest['dictionary_file'] = bad
                save_manifest(directory, manifest)
                with self.assertRaises(ValueError):
                    load_language_pack(directory)

    def test_rejects_missing_or_modified_files_before_catalog_publication(self):
        for violation in ('missing', 'changed', 'extra', 'directory'):
            with self.subTest(violation=violation), TemporaryDirectory() as folder:
                directory, _ = write_pack(folder)
                if violation == 'missing':
                    (directory / 'confident.txt').unlink()
                elif violation == 'changed':
                    (directory / 'words.txt').write_text('changed\n', encoding='utf-8')
                elif violation == 'extra':
                    (directory / 'untrusted.py').write_text('raise Exception("bad")')
                else:
                    (directory / 'nested').mkdir()
                with self.assertRaises(ValueError):
                    available_language_catalog(folder)

    def test_rejects_empty_or_invalid_utf8_confidence(self):
        for content in (b'# comments only\n', b'\xff\xfe', b'word\x00bad\n'):
            with self.subTest(content=content), TemporaryDirectory() as folder:
                directory, manifest = write_pack(folder)
                (directory / 'confident.txt').write_bytes(content)
                manifest['files']['confident.txt'] = {
                    'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                save_manifest(directory, manifest)
                with self.assertRaises((ValueError, UnicodeError)):
                    load_language_pack(directory)

    def test_rejects_oversized_manifest_and_sparse_dictionary(self):
        with TemporaryDirectory() as folder:
            directory, _ = write_pack(folder)
            (directory / 'manifest.json').write_bytes(b' ' * (MAX_MANIFEST_BYTES + 1))
            with self.assertRaisesRegex(ValueError, 'manifest too large'):
                load_language_pack(directory)
        with TemporaryDirectory() as folder:
            directory, manifest = write_pack(folder)
            with (directory / 'words.txt').open('r+b') as stream:
                stream.truncate(MAX_DICTIONARY_BYTES + 1)
            manifest['files']['words.txt']['bytes'] = MAX_DICTIONARY_BYTES + 1
            save_manifest(directory, manifest)
            with self.assertRaisesRegex(ValueError, 'size cap'):
                load_language_pack(directory)

    def test_rejects_bad_metadata_and_unsupported_normalization(self):
        revisions = (
            ('schema_version', 2), ('schema_version', True),
            ('langid', True), ('langid', 0x10000),
            ('unicode_script', 'JAVASCRIPT'), ('alphabet', 'abc'),
            ('normalization', 'custom_python'),
            ('alternatives', ['ru', 'ru']), ('alternatives', ['ar']),
            ('package_version', 'latest'),
        )
        for field, value in revisions:
            with self.subTest(field=field, value=value), TemporaryDirectory() as folder:
                directory, original = write_pack(folder)
                manifest = deepcopy(original)
                manifest[field] = value
                save_manifest(directory, manifest)
                with self.assertRaises(ValueError):
                    load_language_pack(directory)

    def test_cannot_override_builtin_ru_even_with_valid_payload(self):
        with TemporaryDirectory() as folder:
            directory, manifest = write_pack(folder, 'ru', langid=0x0419,
                                      script='CYRILLIC', alphabet='абв', word='дом\n')
            manifest['alternatives'] = ['en', 'he']
            save_manifest(directory, manifest)
            self.assertEqual(load_language_pack(directory).description.code, 'ru')
            with self.assertRaisesRegex(ValueError, 'may not override'):
                available_language_catalog(folder)

    def test_cannot_register_different_code_with_existing_langid(self):
        with TemporaryDirectory() as folder:
            write_pack(folder, 'ar', langid=0x0409)
            with self.assertRaisesRegex(ValueError, 'LANGID conflicts'):
                available_language_catalog(folder)

    def test_addition_roles_and_verified_contents_are_metadata_not_code(self):
        with TemporaryDirectory() as folder:
            directory, manifest = write_pack(folder)
            data = 'كلمة\n'.encode('utf-8')
            (directory / 'extra.txt').write_bytes(data)
            manifest['additions'] = [
                {'filename': 'extra.txt', 'for_words': False, 'for_confident': True}]
            manifest['files']['extra.txt'] = {
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            save_manifest(directory, manifest)
            pack = load_language_pack(directory)
            self.assertEqual(pack.description.additions[0].filename, 'extra.txt')
            self.assertFalse(pack.description.additions[0].for_words)
            self.assertTrue(pack.description.additions[0].for_confident)
            self.assertEqual(pack.license_id, 'CC0-1.0')
            self.assertIn('extra.txt', pack.verified_files)

    def test_rejects_pack_symlink_when_supported(self):
        with TemporaryDirectory() as folder:
            directory, manifest = write_pack(folder)
            actual = directory / 'words.txt'
            outside = Path(folder) / 'outside.txt'
            outside.write_bytes(actual.read_bytes())
            actual.unlink()
            try:
                actual.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest('Symlinks are not permitted on this Windows account')
            # Files and checksum are otherwise valid: link alone must be rejected.
            with self.assertRaises(ValueError):
                load_language_pack(directory)


if __name__ == '__main__':
    unittest.main()
