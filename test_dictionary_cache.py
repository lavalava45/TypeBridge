"""Cache tests use synthetic files; never read user settings or bind layouts."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import os,tempfile,unittest
from dictionary_cache import load_or_build, policy_digest
from engine import LanguageEngine
from incremental_recognition import prepare_early

class CacheTests(unittest.TestCase):
 def test_policy_identity_survives_staging_but_tracks_logic(self):
  import types
  def original(): return 'same'
  moved=types.FunctionType(original.__code__.replace(co_filename='other/stage.py',co_firstlineno=100),{})
  self.assertEqual(policy_digest((original,),[]),policy_digest((moved,),[]))
  changed=types.FunctionType(original.__code__.replace(co_consts=(None,'changed')),{})
  self.assertNotEqual(policy_digest((original,),[]),policy_digest((changed,),[]))
 def setUp(self):
  self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
  self.data=self.folder/'data';self.data.mkdir();self.cache=self.folder/'cache'
  self.files=('a.txt','b.txt')
  for name in self.files:(self.data/name).write_text('one')
  self.builder=Mock(return_value={'words':{l:{'word'} for l in ('ru','en','he')},'confident':{l:{'word'} for l in ('ru','en','he')},'exclusions':set()})
 def load(self):return load_or_build(self.data,self.files,'policy',self.builder,self.cache)
 def test_warm_read_does_not_rebuild(self):
  cold,status=self.load();warm,status2=self.load()
  self.assertEqual((status,status2),('built','hit'));self.assertEqual(cold,warm);self.builder.assert_called_once()
 def test_same_size_timestamp_change_invalidates(self):
  self.load();p=self.data/'a.txt';stamp=p.stat();p.write_text('two');os.utime(p,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
  self.assertEqual(self.load()[1],'built');self.assertEqual(self.builder.call_count,2)
 def test_corrupt_cache_recovers_and_missing_source_is_fatal(self):
  self.load();(self.cache/'words-v1.cache').write_bytes(b'broken')
  self.assertEqual(self.load()[1],'built')
  (self.data/'a.txt').unlink()
  with self.assertRaises(FileNotFoundError):self.load()
 def test_readonly_cache_does_not_stop_dictionary_loading(self):
  with patch('dictionary_cache.os.replace',side_effect=PermissionError()):
   self.assertEqual(self.load()[1],'uncached')
 def test_real_engine_keeps_policy_and_prepared_prefixes(self):
  names={'ru.txt':'ещё\nтест','en.txt':'hello','he.txt':'שלום','he_custom.txt':'ספר','exclude.txt':'excluded',
         'confident_ru.txt':'ещё','confident_en.txt':'hello','confident_he.txt':'שלום'}
  for name,text in names.items():(self.data/name).write_text(text,encoding='utf-8')
  with patch('engine.LayoutMapper',return_value=SimpleNamespace()):
   reference=LanguageEngine(self.data)
   cold=LanguageEngine(self.data,cache_dir=self.cache)
   warm=LanguageEngine(self.data,cache_dir=self.cache)
  for engine in (cold,warm):
   self.assertEqual(engine.words,reference.words);self.assertEqual(engine.confident,reference.confident)
   self.assertEqual(engine.exclusions,reference.exclusions)
  self.assertEqual(warm.cache_status,'hit')
  with patch('incremental_recognition.build_early_index',side_effect=AssertionError('sorted twice')):
   prepare_early(warm)

if __name__=='__main__':unittest.main()
