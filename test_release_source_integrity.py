"""A public ZIP must not silently discard current uncommitted fixes."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from build_public_release import require_clean_source_snapshot, run_isolated_tests


class ReleaseSourceIntegrityTests(unittest.TestCase):
    def test_dirty_or_untracked_source_blocks_public_release(self):
        with patch('build_public_release.subprocess.check_output',
                   return_value=' M typebridge_app.py\n?? native_transaction_queue.py\n'):
            with self.assertRaisesRegex(RuntimeError, 'git archive HEAD would silently omit'):
                require_clean_source_snapshot(Path('repo'))

    def test_clean_repository_passes(self):
        with patch('build_public_release.subprocess.check_output', return_value=''):
            self.assertIsNone(require_clean_source_snapshot(Path('repo')))

    def test_release_tests_run_every_module_in_sorted_isolated_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('test_zeta.py', 'test_alpha.py'):
                (root / name).write_text('import unittest\n', encoding='utf-8')
            env = {'TYPEBRIDGE_DICTIONARY_TEST_DIR': str(root / 'data')}
            with patch('build_public_release.run') as invoke:
                modules = run_isolated_tests(root, env)
            self.assertEqual(modules, ['test_alpha', 'test_zeta'])
            self.assertEqual(invoke.call_count, 2)
            self.assertEqual([call.args[3] for call in invoke.call_args_list], modules)
            for call in invoke.call_args_list:
                self.assertEqual(call.args[1:3], ('-m', 'unittest'))
                self.assertEqual(call.args[4], '-q')
                self.assertEqual(call.kwargs, {'cwd': root, 'env': env})

    def test_no_tests_or_failed_test_blocks_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(RuntimeError, 'no unittest modules'):
                run_isolated_tests(root, {})
            (root / 'test_failure.py').write_text('import unittest\n', encoding='utf-8')
            with patch('build_public_release.run', side_effect=RuntimeError('failed')):
                with self.assertRaisesRegex(RuntimeError, 'failed'):
                    run_isolated_tests(root, {})


if __name__ == '__main__':
    unittest.main()
