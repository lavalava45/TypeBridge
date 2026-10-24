from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_paths import migrate_user_data
import app_paths
from prepare_dictionaries import prepare, select_words
import startup_registration as startup


class BrandMigrationTests(unittest.TestCase):
    def test_default_migration_prefers_current_profile_and_runs_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); current = base/'current'; legacy = base/'old'; portable = base/'portable'
            current.mkdir(); legacy.mkdir()
            (current/'settings.json').write_bytes(b'current')
            (legacy/'settings.json').write_bytes(b'old')
            (legacy/'snippets.json').write_bytes(b'templates')
            with patch.object(app_paths, 'PREVIOUS_USER_DIR', current), patch.object(app_paths, 'LEGACY_USER_DIR', legacy):
                migrate_user_data(portable)
                self.assertEqual((portable/'settings.json').read_bytes(), b'current')
                self.assertEqual((portable/'snippets.json').read_bytes(), b'templates')
                (portable/'settings.json').unlink()
                self.assertEqual(migrate_user_data(portable), [])
                self.assertFalse((portable/'settings.json').exists())
                self.assertEqual((current/'settings.json').read_bytes(), b'current')

    def test_existing_portable_profile_does_not_import_another_computers_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); old = base/'old'; portable = base/'portable'
            old.mkdir(); portable.mkdir()
            (old/'snippets.json').write_bytes(b'other computer')
            (portable/'settings.json').write_bytes(b'USB profile')
            with patch.object(app_paths, 'PREVIOUS_USER_DIR', old), patch.object(app_paths, 'LEGACY_USER_DIR', old):
                self.assertEqual(migrate_user_data(portable), [])
                self.assertFalse((portable/'snippets.json').exists())

    def test_interrupted_migration_resumes_without_overwriting_already_copied_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); old = base/'old'; portable = base/'portable'
            old.mkdir()
            (old/'settings.json').write_bytes(b'original settings')
            (old/'snippets.json').write_bytes(b'templates')
            def interrupted(destination, source):
                (destination/'settings.json').write_bytes(b'copied settings')
                raise OSError('disk full')
            with patch.object(app_paths, 'PREVIOUS_USER_DIR', old), patch.object(app_paths, 'LEGACY_USER_DIR', old):
                with patch.object(app_paths, '_copy_missing', side_effect=interrupted):
                    with self.assertRaises(OSError): migrate_user_data(portable)
                migrate_user_data(portable)
                self.assertEqual((portable/'settings.json').read_bytes(), b'copied settings')
                self.assertEqual((portable/'snippets.json').read_bytes(), b'templates')

    def test_personal_files_copied_verbatim_without_overwriting_new_or_old(self):
        with tempfile.TemporaryDirectory() as folder:
            old=Path(folder)/'LayoutSwitch'; new=Path(folder)/'TypeBridge'
            old.mkdir(); new.mkdir()
            for name in ('settings.json','user_lexicon.json','snippets.json'):
                (old/name).write_bytes(('original-'+name).encode())
            (new/'snippets.json').write_bytes(b'newer')
            self.assertEqual(migrate_user_data(new,old),['settings.json','user_lexicon.json'])
            self.assertEqual((new/'settings.json').read_bytes(),(old/'settings.json').read_bytes())
            self.assertEqual((new/'snippets.json').read_bytes(),b'newer')
            self.assertEqual((old/'snippets.json').read_bytes(),b'original-snippets.json')
            self.assertEqual(migrate_user_data(new,old),[])

    def test_startup_migrates_only_own_entry_and_preserves_disabled_status(self):
        state={'TypeBridge':None,'LayoutSwitch':'old.exe --autostart'}
        def read(name,path=startup.RUN_KEY):
            if path==startup.APPROVED_KEY and name=='LayoutSwitch': return b'\x03\x00'
            return None
        with patch.object(startup,'registration_state',return_value=state), patch.object(startup,'_read_value',side_effect=read), patch.object(startup.winreg,'CreateKeyEx'), patch.object(startup.winreg,'SetValueEx') as set_value, patch.object(startup.winreg,'DeleteValue') as delete:
            startup.write_registration('new.exe --autostart')
            self.assertEqual([c.args[1] for c in set_value.call_args_list],['TypeBridge','TypeBridge'])
            self.assertEqual(set_value.call_args_list[0].args[3:],(startup.winreg.REG_BINARY,b'\x03\x00'))
            self.assertEqual(delete.call_args.args[1],'LayoutSwitch')

    def test_public_dictionary_preparation_refuses_existing_files_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder); (directory/'ru.txt').write_bytes(b'private local dictionary')
            with patch('prepare_dictionaries.urllib.request.urlopen') as request:
                with self.assertRaises(ValueError): prepare(directory)
                request.assert_not_called()
            self.assertEqual((directory/'ru.txt').read_bytes(),b'private local dictionary')


if __name__=='__main__': unittest.main()
