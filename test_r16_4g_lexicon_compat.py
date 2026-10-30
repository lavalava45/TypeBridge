"""Policy upgrades quarantine valid historical words without touching files."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
import json

from user_lexicon import LexiconLoadError, UserLexicon


class LexiconUpgradeCompatibilityTests(TestCase):
    def catalog(self, policy):
        return {**{code: SimpleNamespace(personal_alphabet_policy=None)
                   for code in ('ru', 'en', 'he')},
                'fr': SimpleNamespace(unicode_script='LATIN',
                                      personal_alphabet_policy=policy)}

    def test_disabled_language_policy_upgrade_and_rollback(self):
        v1 = self.catalog('unicode_script_name')
        v2 = self.catalog('ascii_latin_letters')
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = UserLexicon(catalog=v1)
            original.add('words', 'fr', 'café')
            original.add('words', 'fr', 'chat')
            original.add('exclusions', 'fr', 'été')
            original.save(root)
            sidecar = root / 'user_lexicon_packages.json'
            before = sidecar.read_bytes()
            upgraded = UserLexicon.load(root, catalog=v2)
            self.assertEqual(upgraded.unavailable['words']['fr']['insensitive'],
                             ('café', 'chat'))
            self.assertEqual(upgraded.data['words'].get('fr', {}), {})
            self.assertEqual(upgraded.unavailable['exclusions']['fr']['insensitive'], ('été',))
            self.assertEqual(sidecar.read_bytes(), before)
            upgraded.save(root)
            self.assertEqual(json.loads(sidecar.read_bytes()), json.loads(before))
            rollback = UserLexicon.load(root, catalog=v1)
            self.assertTrue(rollback.contains('words', 'fr', 'café'))
            self.assertTrue(rollback.contains('words', 'fr', 'chat'))
            self.assertTrue(rollback.contains('exclusions', 'fr', 'été'))

    def test_reject_malformed_sidecar_even_when_policy_incompatible(self):
        old = self.catalog('unicode_script_name')
        new = self.catalog('ascii_latin_letters')
        with TemporaryDirectory() as directory:
            root = Path(directory)
            lexicon = UserLexicon(catalog=old)
            lexicon.add('words', 'fr', 'café')
            lexicon.save(root)
            sidecar = root / 'user_lexicon_packages.json'
            document = json.loads(sidecar.read_text(encoding='utf-8'))
            document['entries']['exclusions']['fr']['sensitive'] = ['bad entry']
            sidecar.write_text(json.dumps(document), encoding='utf-8')
            with self.assertRaises(LexiconLoadError):
                UserLexicon.load(root, catalog=new)
