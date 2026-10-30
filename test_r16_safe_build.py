"""R16 P1: mock-only EXE build publication. No installed binary is touched."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import build_local


class SafeLocalBuildTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.dist = self.root / 'dist'
        self.diagnostics = self.dist / 'profiles' / 'Diagnostics'
        self.destinations = {'public': self.dist, 'diagnostics': self.diagnostics}
        self.candidates = {}
        for code, destination in self.destinations.items():
            destination.mkdir(parents=True)
            (destination / 'data').mkdir()
            (destination / 'TypeBridge.exe').write_bytes(f'ORIGINAL-{code}'.encode())
            (destination / 'BUILD-PROFILE.json').write_text('old manifest ' + code)
            if code == 'public':
                (destination / 'BUILD-INFO.json').write_text('old info')
            candidate = self.root / 'build' / code
            candidate.mkdir(parents=True)
            (candidate / 'TypeBridge.exe').write_bytes(f'NEW-{code}'.encode())
            self.candidates[code] = (candidate, {'profile': code, 'exe_sha256': 'fixture'})
        self.original = {
            path.relative_to(self.dist): path.read_bytes()
            for path in self.dist.rglob('*') if path.is_file()
        }
        self.rollback = self.root / 'rollback'

    def assert_originals(self):
        for relative, content in self.original.items():
            self.assertEqual((self.dist / relative).read_bytes(), content, relative)

    def test_failed_candidate_self_test_does_not_touch_existing_exe(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'typebridge_app.py').write_text('pass\n')
        (source / 'dictionary_packs').mkdir()
        (source / 'dictionary_packs' / 'README.md').write_text('fixture')
        # Simulate PyInstaller producing an EXE in the candidate directory,
        # followed by a failure in verification.
        def fake_pyinstaller(command, **kwargs):
            output = Path(command[command.index('--distpath') + 1])
            output.mkdir(exist_ok=True, parents=True)
            (output / 'TypeBridge.exe').write_bytes(b'UNVERIFIED')
        with (patch('build_local.subprocess.run', side_effect=fake_pyinstaller),
              patch('build_local.verify_frozen_profile', side_effect=ValueError('bad PYZ'))):
            with self.assertRaisesRegex(ValueError, 'bad PYZ'):
                build_local.build_profile(source, self.candidates['public'][0], 'public')
        self.assert_originals()

    def test_failure_during_second_exe_publication_restores_both_profiles(self):
        real_replace = build_local.os.replace
        def simulate_failed_install(src, dst):
            if Path(dst) == self.diagnostics / 'TypeBridge.exe':
                raise OSError('failed second EXE publication')
            return real_replace(src, dst)
        with (patch('build_local.assert_typebridge_stopped'),
              patch('build_local.os.replace', side_effect=simulate_failed_install)):
            with self.assertRaisesRegex(OSError, 'failed second EXE publication'):
                build_local.publish_verified(self.candidates, self.destinations,
                                             self.rollback)
        self.assert_originals()

    def test_success_only_after_two_verified_candidates(self):
        with patch('build_local.assert_typebridge_stopped') as check:
            build_local.publish_verified(self.candidates, self.destinations, self.rollback)
        self.assertGreaterEqual(check.call_count, 2)
        for profile, directory in self.destinations.items():
            self.assertEqual((directory / 'TypeBridge.exe').read_bytes(),
                             f'NEW-{profile}'.encode())
            self.assertEqual(json.loads((directory / 'BUILD-PROFILE.json').read_text())['profile'],
                             profile)

    def test_bundled_package_publication_rolls_back_on_second_profile_failure(self):
        from test_r16_4d_integration import MigrationR164dTests
        # Use a real valid but tiny v2 manifest bundle, not any running EXE.
        for profile, destination in self.destinations.items():
            existing = destination / 'bundled_language_packs'
            existing.mkdir()
            (existing / 'legacy-marker.txt').write_text('OLD-' + profile)
            source = self.candidates[profile][0] / 'bundled_language_packs'
            source.mkdir()
            for code in ('ru', 'en', 'he'):
                (source / code).mkdir()
        with (patch('build_local.assert_typebridge_stopped'),
              patch('language_pack_v2.load_v2_pack') as verified):
            real_replace = build_local.os.replace
            def broken_publish(src, dst):
                if (Path(dst) == self.diagnostics / 'bundled_language_packs'
                        and Path(src).parent == self.candidates['diagnostics'][0]):
                    raise OSError('failed diagnostics bundle replacement')
                return real_replace(src, dst)
            with patch('build_local.os.replace', side_effect=broken_publish):
                with self.assertRaisesRegex(OSError, 'failed diagnostics bundle replacement'):
                    build_local.publish_verified(self.candidates, self.destinations,
                                                 self.rollback)
        self.assert_originals()
        for profile, destination in self.destinations.items():
            self.assertEqual((destination / 'bundled_language_packs' /
                              'legacy-marker.txt').read_text(), 'OLD-' + profile)

    def test_main_does_not_publish_public_when_diagnostics_verification_fails(self):
        source = self.root / 'src'
        source.mkdir()
        (source / 'typebridge_app.py').write_text('pass\n')
        (source / 'build_local.py').write_text('pass\n')
        (source / 'build').mkdir()
        # Main() uses its script path to choose the root; mimic a checkout
        # containing the actual dist directories.
        for profile, destination in self.destinations.items():
            mapped = source / destination.relative_to(self.root)
            mapped.mkdir(parents=True)
            (mapped / 'data').mkdir()
            (mapped / 'TypeBridge.exe').write_bytes(f'ORIGINAL-{profile}'.encode())
            (mapped / 'BUILD-PROFILE.json').write_text(f'original {profile}')
        calls = []
        def fail_second(root, destination, profile):
            calls.append(profile)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / 'TypeBridge.exe').write_bytes(b'NEW-CANDIDATE')
            if profile == 'diagnostics':
                raise ValueError('failed diagnostics verification')
            return {'module_count': 1}, {'ok': True}
        with (patch.object(build_local, '__file__', str(source / 'build_local.py')),
              patch('build_local.assert_typebridge_stopped'),
              patch('build_local.DICTIONARY_FILES', ()),
              patch('build_local.require_dictionary_files'),
              patch('build_local.subprocess.check_output', return_value='commit123'),
              patch('build_local.build_profile', side_effect=fail_second)):
            with self.assertRaisesRegex(ValueError, 'failed diagnostics verification'):
                build_local.main()
        self.assertEqual(calls, ['public', 'diagnostics'])
        for profile, directory in self.destinations.items():
            mapped = source / directory.relative_to(self.root)
            self.assertEqual((mapped / 'TypeBridge.exe').read_bytes(),
                             f'ORIGINAL-{profile}'.encode())
            self.assertEqual((mapped / 'BUILD-PROFILE.json').read_text(),
                             f'original {profile}')
        self.assertFalse(tuple((source / 'build').glob('verified-typebridge-*')))

    def test_staged_candidate_self_test_receives_verified_external_dictionaries(self):
        source = self.root / 'fixture-src'
        source.mkdir()
        (source / 'typebridge_app.py').write_text('pass\n')
        (source / 'build_local.py').write_text('pass\n')
        for profile, destination in self.destinations.items():
            mapped = source / destination.relative_to(self.root)
            (mapped / 'data').mkdir(parents=True)
            (mapped / 'data' / 'ru.txt').write_bytes(b'unmodified user lexicon source')
            (mapped / 'TypeBridge.exe').write_bytes(f'OLD-{profile}'.encode())
        seen = []
        def assert_staged(root, candidate, profile):
            self.assertEqual((candidate / 'data' / 'ru.txt').read_bytes(),
                             b'unmodified user lexicon source')
            seen.append(profile)
            if profile == 'diagnostics':
                raise ValueError('stop before publication')
            (candidate / 'TypeBridge.exe').write_bytes(b'verified only in fixture')
            return {'module_count': 1}, {'ok': True}
        with (patch.object(build_local, '__file__', str(source / 'build_local.py')),
              patch('build_local.assert_typebridge_stopped'),
              patch('build_local.DICTIONARY_FILES', ('ru.txt',)),
              patch('build_local.require_dictionary_files'),
              patch('build_local.subprocess.check_output', return_value='commit123'),
              patch('build_local.build_profile', side_effect=assert_staged)):
            with self.assertRaisesRegex(ValueError, 'stop before publication'):
                build_local.main()
        self.assertEqual(seen, ['public', 'diagnostics'])
        for profile, destination in self.destinations.items():
            installed = source / destination.relative_to(self.root)
            self.assertEqual((installed / 'TypeBridge.exe').read_bytes(),
                             f'OLD-{profile}'.encode())
            self.assertEqual((installed / 'data' / 'ru.txt').read_bytes(),
                             b'unmodified user lexicon source')


if __name__ == '__main__':
    unittest.main()
