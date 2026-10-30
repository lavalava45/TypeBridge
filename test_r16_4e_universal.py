"""Static startup safety gate for recoverable language-package transactions.

This intentionally does not launch TypeBridge, touch user settings, or install
keyboard hooks. Package transaction behavior has separate isolated tests.
"""

import ast
from pathlib import Path
import unittest


class PackageRecoveryStartupTests(unittest.TestCase):
    def test_unified_selected_loader_uses_verified_packages(self):
        tree = ast.parse(Path(__file__).with_name('language_unified_runtime.py')
                         .read_text(encoding='utf-8'))
        loader = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'prepare_selected')
        names = {node.id for node in ast.walk(loader)
                 if isinstance(node, ast.Name)}
        self.assertNotIn('LANGUAGE_BY_CODE', names)
        self.assertIn('verified', names)
        self.assertIn('installed_language', names)
        self.assertIn('_selected_package_sources', names)

    def test_active_runtime_always_receives_package_root(self):
        """Bundled-only and mixed selections enter identical runtime loading."""
        tree = ast.parse(Path(__file__).with_name('typebridge_app.py').read_text(
            encoding='utf-8'))
        constructions = []
        for function in (node for node in ast.walk(tree)
                         if isinstance(node, ast.FunctionDef)
                         and node.name in ('worker', 'main')):
            constructions.extend(node for node in ast.walk(function)
                                 if isinstance(node, ast.Call)
                                 and isinstance(node.func, ast.Name)
                                 and node.func.id == 'LanguageEngine')
        self.assertGreaterEqual(len(constructions), 2)
        for node in constructions:
            with self.subTest(line=node.lineno):
                self.assertTrue(any(keyword.arg == 'pack_root' for keyword in node.keywords))

    def test_incremental_recognition_uses_selected_languages(self):
        source = Path(__file__).with_name('incremental_recognition.py').read_text(
            encoding='utf-8')
        self.assertNotIn('from engine import Decision, LANGS', source)
        self.assertNotIn("getattr(engine, 'active_languages', LANGS)", source)

    def test_gui_and_headless_recover_before_catalog_enumeration(self):
        tree = ast.parse(Path(__file__).with_name('typebridge_app.py').read_text(
            encoding='utf-8'))
        gui = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'TypeBridgeGui')
        init = next(node for node in gui.body if isinstance(node, ast.FunctionDef)
                    and node.name == '__init__')
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'main')

        def calls(function, name):
            return sorted(node.lineno for node in ast.walk(function)
                          if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Name)
                          and node.func.id == name)

        for function in (init, main):
            with self.subTest(function=function.name):
                recovery = calls(function, 'recover_package_transactions')
                catalog = calls(function, 'available_language_catalog')
                self.assertEqual(len(recovery), 1)
                self.assertEqual(len(catalog), 1)
                self.assertLess(recovery[0], catalog[0])


if __name__ == '__main__':
    unittest.main()
