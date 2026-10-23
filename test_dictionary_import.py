"""Import tests use temporary lexicons and withdrawn Tk, no hooks or live input."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dictionary_import import parse_import, read_import, merge_exclusions, PACK_DIR, BUILTINS, MAX_BYTES
from user_lexicon import UserLexicon
from typebridge_app import TypeBridgeGui


class ParsingTests(unittest.TestCase):
    def test_auto_language_bom_comments_canonical_duplicates_and_invalid_tokens(self):
        text = '\ufeff# comment\nHello\nHELLO # duplicate\nПривет\nשלום\nstd::vector\n__init__\nC++\nC#\nCOVID19\nhello world\n'
        preview = parse_import(text.encode('utf-8'))
        self.assertEqual([(x.language, x.word) for x in preview.entries],
                         [('en', 'hello'), ('ru', 'привет'), ('he', 'שלום')])
        self.assertEqual(preview.duplicates, 1)
        self.assertEqual(len(preview.skipped), 6)
        self.assertNotIn('c', [x.word for x in preview.entries])

    def test_explicit_language_and_exact_case(self):
        preview = parse_import('API\napi\nПривет'.encode(), language='en', sensitive=True)
        self.assertEqual([x.word for x in preview.entries], ['API', 'api'])
        self.assertEqual(len(preview.skipped), 1)

    def test_json_preserves_per_entry_language_and_case(self):
        doc = {'format': 'typebridge-exclusions', 'version': 1, 'name': 'My terms',
               'entries': [{'language': 'en', 'word': 'API', 'sensitive': True},
                           {'language': 'he', 'word': 'שלום'}]}
        preview = parse_import(json.dumps(doc).encode(), filename='terms.json')
        self.assertEqual(preview.entries[0].word, 'API')
        self.assertTrue(preview.entries[0].sensitive)
        self.assertEqual(preview.entries[1].language, 'he')
        doc['entries'][1]['sensitive'] = 'false'
        with self.assertRaises(ValueError):
            parse_import(json.dumps(doc).encode(), filename='terms.json')

    def test_bounds_encoding_and_unknown_schemas(self):
        for raw, name in ((b'a' * (MAX_BYTES + 1), 'x.txt'), (b'\xff', 'x.txt'),
                          (b'a\n' * 50_001, 'x.txt'), (b'{}', 'x.json'),
                          (b'{', 'x.json'), (b'[]', 'x.json')):
            with self.subTest(name=name, length=len(raw)), self.assertRaises(ValueError):
                parse_import(raw, filename=name)


class MergeTests(unittest.TestCase):
    def test_merge_preserves_existing_words_and_original_snapshot(self):
        original = UserLexicon()
        original.add('words', 'ru', 'Ёж')
        original.add('exclusions', 'en', 'API')
        preview = parse_import(b'API\nasync\nawait')
        merged, added, existing = merge_exclusions(original, preview)
        self.assertEqual((added, existing), (2, 1))
        self.assertTrue(merged.contains('words', 'ru', 'ёж'))
        self.assertFalse(original.contains('exclusions', 'en', 'async'))
        _, added, existing = merge_exclusions(merged, preview)
        self.assertEqual((added, existing), (0, 3))

    def test_insensitive_existing_covers_exact_but_not_reverse(self):
        original = UserLexicon()
        original.add('exclusions', 'en', 'api')
        _, added, existing = merge_exclusions(original, parse_import(b'API', sensitive=True))
        self.assertEqual((added, existing), (0, 1))
        original = UserLexicon()
        original.add('exclusions', 'en', 'API', sensitive=True)
        merged, added, existing = merge_exclusions(original, parse_import(b'api'))
        self.assertEqual((added, existing), (1, 0))
        self.assertTrue(merged.contains('exclusions', 'en', 'Api'))

    def test_app_publishes_only_after_successful_atomic_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            original = UserLexicon()
            original.save(folder)
            app = TypeBridgeGui.__new__(TypeBridgeGui)
            app.lexicon = original
            app.hook = Mock()
            app.emit = Mock()
            app._refresh_words = Mock()
            preview = parse_import(b'async\nawait')
            with (patch('typebridge_app.USER_DIR', folder),
                  patch('typebridge_app.load_user_lexicon', side_effect=lambda: UserLexicon.load(folder))):
                with patch.object(UserLexicon, 'save', side_effect=OSError('disk full')):
                    with self.assertRaises(OSError):
                        app.import_exclusion_preview(preview)
                self.assertIs(app.lexicon, original)
                app.hook.request_config.assert_not_called()
                self.assertFalse(UserLexicon.load(folder).contains('exclusions', 'en', 'async'))
                self.assertEqual(app.import_exclusion_preview(preview), (2, 0))
                self.assertTrue(UserLexicon.load(folder).contains('exclusions', 'en', 'async'))
                app.hook.request_config.assert_called_once()
                self.assertEqual(app.import_exclusion_preview(preview), (0, 2))
                self.assertEqual(app.hook.request_config.call_count, 1)


class BundledAndGuiTests(unittest.TestCase):
    def test_bundled_examples_and_pinned_provenance(self):
        source_names = {'python.txt': 'python.txt', 'typescript.txt': 'javascript.txt',
                        'lang-keywords.txt': 'cpp.txt', 'gaming-terms.txt': 'gaming.txt'}
        for filename, title in BUILTINS:
            path = PACK_DIR / filename
            preview = read_import(path)
            self.assertTrue(preview.entries)
            self.assertFalse(preview.skipped)
            self.assertFalse(preview.duplicates)
            doc = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(doc['license'], 'MIT')
            for source in doc['sources']:
                raw = (PACK_DIR / 'upstream' / source_names[source['url'].rsplit('/', 1)[1]]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), source['sha256'])
                self.assertIn(doc['upstream_commit'], source['url'])
                self.assertIn('MIT', (PACK_DIR / source['license_file']).read_text())
        programming = read_import(PACK_DIR / 'programming.json')
        self.assertTrue({'def', 'async', 'const', 'namespace'} <= {x.word for x in programming.entries})

    def test_hidden_window_one_click_builtin_and_custom_preview(self):
        import tkinter as tk
        from dictionary_import_gui import DictionaryImportWindow
        root = tk.Tk()
        root.withdraw()
        app = SimpleNamespace(import_exclusion_preview=Mock(return_value=(41, 0)))
        try:
            editor = DictionaryImportWindow(root, app)
            editor.window.withdraw()
            root.update_idletasks()
            self.assertFalse(editor.window.winfo_ismapped())
            self.assertLessEqual(editor.window.winfo_reqheight(), 640)
            editor.add_builtin('gaming.json')
            app.import_exclusion_preview.assert_called_once()
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'words.txt'
                path.write_text('custom\nслово', encoding='utf-8')
                editor.file_path = path
                editor.reload()
                self.assertEqual(len(editor.preview.entries), 2)
                self.assertEqual(app.import_exclusion_preview.call_count, 1)
                editor.apply()
                self.assertEqual(app.import_exclusion_preview.call_count, 2)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
