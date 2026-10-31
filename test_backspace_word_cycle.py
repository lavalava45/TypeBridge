"""Backspace/retyping with actual dictionaries and an in-memory document only."""
import unittest
from engine import LanguageEngine, source_data_dir
import test_word_cycle as cycle
from test_app_controls import key
from typebridge_app import VK_BACK, WM_KEYUP


class BackspaceWordCycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = LanguageEngine(source_data_dir())

    def test_editing_corrected_word_preserves_full_text_in_all_six_directions(self):
        for source, physical, target, expected in (
                ('ru','hello','en','hello'), ('he','hello','en','hello'),
                ('en','ghbdtn','ru','привет'), ('he','ghbdtn','ru','привет'),
                ('en','akuo','he','שלום'), ('ru','akuo','he','שלום')):
            with self.subTest(source=source, target=target):
                driver = cycle.WordCycleTests()
                driver.engine = self.engine
                driver.setUp()
                driver.language = driver.hook.last_language = source
                driver.type(physical + ' ')
                self.assertEqual((driver.text, driver.language), (expected + ' ', target))
                # Deliver the real logical Backspace once to the model document.
                for position in range(2):
                    self.assertEqual(key(driver.hook, VK_BACK), 0)
                    driver.text = driver.text[:-1]
                    key(driver.hook, VK_BACK, WM_KEYUP)
                    self.assertFalse(driver.hook.token_blocked)
                    self.assertEqual(len(driver.hook.keys), len(physical) - position)
                self.assertEqual(driver.text, expected[:-1])
                driver.type(physical[-1] + ' ')
                self.assertEqual((driver.text, driver.language), (expected + ' ', target))
                original = driver.text
                driver.cycle(); driver.cycle(); driver.cycle()
                self.assertEqual(driver.text, original)  # full word and its separator survive


if __name__ == '__main__':
    unittest.main()
