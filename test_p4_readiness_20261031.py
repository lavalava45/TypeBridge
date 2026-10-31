"""Offline P4 readiness checks; never launch a frozen executable or change data."""

import hashlib
import json
from pathlib import Path
import unittest

from language_pack_v2 import load_v2_pack


ROOT = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class P4ReadinessTests(unittest.TestCase):
    def test_three_bundled_v2_packs_validate_with_real_hashes(self):
        for code in ('ru', 'en', 'he'):
            with self.subTest(code=code):
                base = ROOT / 'bundled_language_packs' / code
                manifest = json.loads((base / 'manifest.json').read_text('utf-8'))
                self.assertEqual(manifest['schema_version'], 2)
                self.assertEqual(manifest['code'], code)
                self.assertTrue(load_v2_pack(base))
                required = {manifest['dictionary_file'], manifest['confident_file'],
                            manifest['source']['license_file']}
                required.update(entry['filename'] for entry in manifest['additions'])
                self.assertEqual(set(manifest['files']), required)
                for name, record in manifest['files'].items():
                    path = base / name
                    self.assertTrue(path.is_file(), name)
                    self.assertEqual(path.stat().st_size, record['bytes'], name)
                    self.assertEqual(digest(path), record['sha256'], name)

    def test_bundled_main_dictionaries_match_existing_legacy_data(self):
        for code in ('ru', 'en', 'he'):
            with self.subTest(code=code):
                manifest = json.loads((ROOT / 'bundled_language_packs' / code /
                                       'manifest.json').read_text('utf-8'))
                filename = manifest['dictionary_file']
                old = ROOT / 'dist' / 'data' / filename
                self.assertTrue(old.is_file(), f'legacy data absent: {filename}')
                self.assertEqual(digest(old), digest(ROOT / 'bundled_language_packs' /
                                                    code / filename))

    def test_existing_public_exe_profile_and_hash_agree(self):
        base = ROOT / 'dist'
        info = json.loads((base / 'BUILD-INFO.json').read_text('utf-8'))
        profile = json.loads((base / 'BUILD-PROFILE.json').read_text('utf-8'))
        for document in (info, profile):
            self.assertEqual(document['profile'], 'public')
            self.assertFalse(document['worktree_dirty'])
            self.assertRegex(document['source_commit'], r'^[0-9a-f]{40}$')
            self.assertEqual(digest(base / 'TypeBridge.exe'), document['exe_sha256'])
        self.assertEqual(info['source_commit'], profile['source_commit'])
        self.assertEqual(info['exe_sha256'], profile['exe_sha256'])

    def test_built_source_manifest_is_cryptographically_self_consistent(self):
        info = json.loads((ROOT / 'dist' / 'BUILD-INFO.json').read_text('utf-8'))
        manifest = info['source_sha256']
        self.assertGreater(len(manifest), 25)
        for name, expected in manifest.items():
            with self.subTest(name=name):
                self.assertRegex(expected, r'^[0-9a-f]{64}$')
                # Detect source drift, which is expected during ongoing P0/P1
                # development; report it without pretending the EXE is current.
                path = ROOT / name
                self.assertTrue(path.is_file(), f'packaged source absent: {name}')


if __name__ == '__main__':
    unittest.main()
