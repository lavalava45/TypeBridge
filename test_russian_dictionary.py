import unittest
from prepare_russian_dictionary import expand_suffixes


class RussianDictionaryTests(unittest.TestCase):
    def test_expands_suffix_and_preserves_stem(self):
        aff = 'SET UTF-8\nSFX A Y 2\nSFX A а ами а\nSFX A 0 у [бв]\n'
        dic = '3\nмама/A\nдуб/A\nEnglish\n'
        self.assertEqual(expand_suffixes(dic.encode(), aff.encode()), {'мама', 'мамами', 'дуб', 'дубу'})

    def test_unknown_features_fail_instead_of_partial_expansion(self):
        with self.assertRaises(ValueError):
            expand_suffixes(b'1\na\n', b'SET UTF-8\nNEEDAFFIX X\n')
        with self.assertRaises(ValueError):
            expand_suffixes('1\nдом/X\n'.encode(), b'SET UTF-8\n')

    def test_rule_counts_must_match(self):
        with self.assertRaises(ValueError):
            expand_suffixes('1\nдом\n'.encode(), b'SET UTF-8\nSFX A Y 1\n')

    def test_empty_strip_and_casefold(self):
        self.assertEqual(expand_suffixes('1\nДом/A\n'.encode(),
                         'SET UTF-8\nSFX A Y 1\nSFX A 0 ы .\n'.encode()), {'дом', 'домы'})


if __name__ == '__main__':
    unittest.main()
