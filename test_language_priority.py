import unittest
from engine import LanguageEngine
from test_frequency_rank import sample_engine


class LanguagePriorityTests(unittest.TestCase):
    def test_all_three_orders_without_frequency_data(self):
        forms = {'ru': 'абв', 'en': 'xyz', 'he': 'אבג'}
        for source, expected in [('ru', 'en'), ('en', 'ru'), ('he', 'ru')]:
            engine, keys = sample_engine(forms)
            engine.words = {lang: {word} for lang, word in forms.items()}
            engine.words[source].clear()
            engine.confident = {lang: {word} for lang, word in forms.items()}
            engine.frequencies = {}
            self.assertEqual(engine.decide(keys, source).language, expected)

    def test_known_source_is_preserved(self):
        engine, keys = sample_engine()
        engine.words['en'].add('abc')
        self.assertIsNone(engine.decide(keys, 'en'))

    def test_unique_hebrew_candidate_is_preserved(self):
        engine, keys = sample_engine()
        engine.words['ru'].clear()
        self.assertEqual(engine.decide(keys, 'en').language, 'he')


if __name__ == '__main__': unittest.main()
