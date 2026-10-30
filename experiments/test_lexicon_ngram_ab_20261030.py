"""Offline controls for wordfreq/n-gram study. No native input or user data."""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lexicon_ngram_ab_20261030 import (
    CharacterNgramModel, split_wikipedia_oov, model_variants,
)
from windows_spellcheck_benchmark import keyboard_maps, keys_for


class ExperimentsOnlyTests(unittest.TestCase):
    def test_character_ngrams_are_finite_and_rank_obvious_noise(self):
        model = CharacterNgramModel(['hello', 'helper', 'helped', 'helping',
                                     'helpful', 'helping', 'hello'])
        for n in (3, 4, 5):
            self.assertGreater(model.score('hello', n=n),
                               model.score('zzqxz', n=n))

    def test_wikimedia_train_holdout_partition_independent_of_spellcheck(self):
        packs = {
            lang: SimpleNamespace(description=SimpleNamespace(unicode_script=script))
            for lang, script in [('en','LATIN'), ('ru','CYRILLIC'), ('he','HEBREW')]
        }
        policy = SimpleNamespace(packs=packs,
                                 words={lang:set() for lang in packs},
                                 normalize=lambda lang, word:word.casefold())
        titles = {'ru':['Машинка','Птичка'], 'en':['Computer','Computerized'],
                  'he':['מחשבים','שכונות']}
        snapshot = {'languages':{
            lang:{'titles':[{'title':name,'pageid':3+i}
                             for i,name in enumerate(values)]}
            for lang,values in titles.items()}}
        train, holdouts, stats = split_wikipedia_oov(
            snapshot, policy, keyboard_maps())
        self.assertEqual(len(train['en']),1)
        self.assertIn(('en',4),holdouts)
        self.assertNotIn(('en',3),holdouts)
        for lang in titles:
            # The synthetic mapper does not cover every character position
            # in every language, so count only map-representable fixtures.
            eligible = sum(1 for value in titles[lang]
                           if keys_for(value.casefold(), lang, keyboard_maps()))
            self.assertEqual(stats[lang]['train_names']+
                             stats[lang]['holdout_names'],eligible)

    def test_inclusion_tiers_are_separate_and_do_not_mutate_baseline(self):
        base = SimpleNamespace(
            words={lang:{'base'} for lang in ('ru','en','he')},
            confident={lang:{'base'} for lang in ('ru','en','he')},
            exclusions=set())
        policies = model_variants(base,
            languages={lang:SimpleNamespace() for lang in ('ru','en','he')},
            mapper=SimpleNamespace(),
            frequency_oov={lang:[{'word':'candidate','zipf':2.5},
                                 {'word':'frequent','zipf':4.0}]
                           for lang in ('ru','en','he')},
            wikipedia_names={lang:{'propername'} for lang in ('ru','en','he')})
        self.assertNotIn('candidate',base.words['en'])
        self.assertIn('candidate',policies['protect_only'].words['en'])
        self.assertNotIn('candidate',policies['protect_only'].confident['en'])
        self.assertIn('candidate',policies['all_confident'].confident['en'])
        self.assertNotIn('candidate',policies['frequency_confident'].confident['en'])
        self.assertIn('frequent',policies['frequency_confident'].confident['en'])
        self.assertNotIn('propername',policies['frequency_confident'].confident['en'])


if __name__ == '__main__':
    unittest.main()
