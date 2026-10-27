"""Offline build-contract checks; no PyInstaller invocation or Windows hook."""
import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from build_profiles import (PUBLIC_EXCLUDED_MODULES, build_both,
                            frozen_smoke_test, install_public_data, stage_sources,
                            verify_frozen_profile)


class ProfileContractTests(unittest.TestCase):
    def test_public_forbids_text_tracers_and_diagnostics_retains_them(self):
        with patch('build_profiles.frozen_module_names',
                   return_value={'build_variant', 'typebridge_app', 'engine'}):
            result = verify_frozen_profile('public.exe', 'public')
        self.assertEqual(result['diagnostic_modules'], [])
        for name in PUBLIC_EXCLUDED_MODULES:
            with self.subTest(module=name):
                with patch('build_profiles.frozen_module_names',
                           return_value={'build_variant', name}):
                    with self.assertRaisesRegex(ValueError, 'Unsafe public EXE'):
                        verify_frozen_profile('unsafe.exe', 'public')
        with patch('build_profiles.frozen_module_names',
                   return_value={'build_variant', 'word_trace', 'browser_readback'}):
            self.assertEqual(verify_frozen_profile('diag.exe', 'diagnostics')['profile_marker_present'], True)
        with patch('build_profiles.frozen_module_names',
                   return_value={'build_variant', 'word_trace'}):
            with self.assertRaisesRegex(ValueError, 'missing'):
                verify_frozen_profile('bad-diagnostics.exe', 'diagnostics')

    def test_stage_source_creates_distinct_baked_profiles(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'; source.mkdir()
            (source / 'typebridge_app.py').write_text('pass\n', encoding='utf-8')
            (source / 'dictionary_packs').mkdir()
            (source / 'dictionary_packs' / 'README.md').write_text('test', encoding='utf-8')
            for name in ('public', 'diagnostics'):
                target = root / name
                stage_sources(source, target, name)
                self.assertIn(f"PROFILE = '{name}'",
                              (target / 'build_variant.py').read_text(encoding='utf-8'))
                self.assertTrue((target / 'dictionary_packs' / 'README.md').exists())

    def test_local_profile_data_excludes_private_extra_dictionary_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'; source.mkdir()
            (source / 'data').mkdir()
            for name in ('ru.txt','en.txt','he.txt','confident_ru.txt',
                         'confident_en.txt','confident_he.txt','he_custom.txt'):
                (source / 'data' / name).write_text('test', encoding='utf-8')
            (source / 'typebridge-settings.json').write_text('{}', encoding='utf-8')
            package = root / 'Public'; package.mkdir()
            install_public_data(source, package)
            self.assertEqual((package / 'data' / 'he_custom.txt').read_text(encoding='utf-8'),
                             'מחשב\n')
            self.assertTrue((package / 'data' / 'exclude.txt').exists())
            self.assertTrue((package / 'data' / 'he.txt').exists())

    def test_build_both_uses_identical_source_revision(self):
        calls = []
        def record(source, root, profile, *, commit, version):
            calls.append((profile, commit, version))
            return profile
        with patch('build_profiles.build_one', side_effect=record):
            self.assertEqual(build_both('src', 'out', commit='abc123', version='1.2'),
                             {'public': 'public', 'diagnostics': 'diagnostics'})
        self.assertEqual(calls, [('public','abc123','1.2'),
                                 ('diagnostics','abc123','1.2')])

    def test_public_frozen_self_test_even_with_both_flags_cannot_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'TypeBridge.exe'
            exe.touch()
            def fake_run(cmd, **kwargs):
                self.assertTrue((exe.parent / 'TypeBridge-debug.flag').exists())
                self.assertTrue((exe.parent / 'TypeBridge-debug-words.flag').exists())
                Path(cmd[-1]).write_text(json.dumps({
                    'ok': True, 'build_profile': 'public',
                    'word_trace_enabled': False}), encoding='utf-8')
                return SimpleNamespace(returncode=0)
            with patch('build_profiles.subprocess.run', side_effect=fake_run):
                self.assertFalse(frozen_smoke_test(exe, 'public')['word_trace_enabled'])
            self.assertFalse((exe.parent / 'TypeBridge-debug.flag').exists())
            self.assertFalse((exe.parent / 'TypeBridge-debug-words.flag').exists())

    def test_frozen_profile_mismatch_refuses_even_when_self_test_ok(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'TypeBridge.exe'
            exe.touch()
            def bad_run(cmd, **kwargs):
                Path(cmd[-1]).write_text(json.dumps({
                    'ok': True, 'build_profile': 'public',
                    'word_trace_enabled': True}), encoding='utf-8')
                return SimpleNamespace(returncode=0)
            with patch('build_profiles.subprocess.run', side_effect=bad_run):
                with self.assertRaisesRegex(ValueError, 'mismatch'):
                    frozen_smoke_test(exe, 'public')
            self.assertFalse((exe.parent / 'TypeBridge-debug.flag').exists())

    def test_smoke_test_does_not_delete_user_existing_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'TypeBridge.exe'
            exe.touch()
            flag = exe.parent / 'TypeBridge-debug.flag'
            flag.write_text('user-made flag', encoding='utf-8')
            def fake_run(cmd, **kwargs):
                Path(cmd[-1]).write_text(json.dumps({
                    'ok': True, 'build_profile': 'diagnostics',
                    'word_trace_enabled': True}), encoding='utf-8')
                return SimpleNamespace(returncode=0)
            with patch('build_profiles.subprocess.run', side_effect=fake_run):
                frozen_smoke_test(exe, 'diagnostics')
            self.assertEqual(flag.read_text(encoding='utf-8'), 'user-made flag')
            self.assertFalse((exe.parent / 'TypeBridge-debug-words.flag').exists())


if __name__ == '__main__':
    unittest.main()
