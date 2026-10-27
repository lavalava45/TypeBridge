"""Offline deterministic tests: no Windows layout activation or typing."""

import unittest
import time
from types import SimpleNamespace
from pathlib import Path

from incremental_recognition import (prepare_early, suggest_early,
                                     evaluate_unique_language, suggest_live_early,
                                     _prefix_matches)
from engine import read_words, SAMPLE_HEBREW


class FixtureMapper:
    def __init__(self, representations):
        self.representations = representations

    def word(self, keys, language):
        return self.representations[language][:len(keys)]


def fixture(representations, source, target, completion, *, competitors=()):
    words = {lang: set() for lang in ("ru", "en", "he")}
    confident = {lang: set() for lang in words}
    words[target].add(completion)
    confident[target].add(completion)
    for language, word in competitors:
        words[language].add(word)
    engine = SimpleNamespace(mapper=FixtureMapper(representations), words=words,
                             confident=confident, exclusions=set())
    prepare_early(engine)
    keys = [(65 + index % 26, False) for index in range(len(representations[source]))]
    return engine, keys


class EarlyRecognitionTests(unittest.TestCase):
    def test_rare_mode_accepts_unique_complete_word_before_space(self):
        forms = {'en': 'lbdthcbz', 'ru': 'диверсия', 'he': 'ךנגאירנז'}
        engine, keys = fixture(forms, 'en', 'ru', 'диверсия')
        engine.confident['ru'].clear()
        engine.conservative = True
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))
        engine.conservative = False
        decision = suggest_live_early(engine, keys, 'en')
        self.assertIsNotNone(decision)
        self.assertEqual((decision.typed, decision.replacement, decision.language),
                         ('lbdthcbz', 'диверсия', 'ru'))
        self.assertEqual(decision.reason, 'unique rare exact word')

    def test_rare_exact_matching_covers_all_six_language_directions(self):
        for source, target, representations in (
            ('ru', 'en', {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}),
            ('he', 'en', {'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'}),
            ('en', 'ru', {'en': 'ghbdtn', 'ru': 'привет', 'he': 'עינגאמ'}),
            ('he', 'ru', {'en': 'ghbdtn', 'ru': 'привет', 'he': 'עינגאמ'}),
            ('en', 'he', {'en': 'akuoy', 'ru': 'флгщн', 'he': 'שלוםו'}),
            ('ru', 'he', {'en': 'akuoy', 'ru': 'флгщн', 'he': 'שלוםו'}),
        ):
            with self.subTest(source=source, target=target):
                expected = representations[target]
                engine, keys = fixture(representations, source, target, expected)
                engine.confident[target].clear()
                engine.conservative = False
                decision = suggest_live_early(engine, keys, source)
                self.assertIsNotNone(decision)
                self.assertEqual((decision.replacement, decision.language), (expected, target))

    def test_rare_mode_rejects_unfinished_words_and_short_tokens(self):
        forms = {'en': 'lbdthcbz', 'ru': 'диверсия', 'he': 'ךנגאירנז'}
        engine, keys = fixture(forms, 'en', 'ru', 'диверсиями')
        engine.confident['ru'].clear()
        engine.conservative = False
        for size in range(4, len(keys) + 1):
            self.assertIsNone(suggest_live_early(engine, keys[:size], 'en'))
        short, skeys = fixture({'en': 'qwer', 'ru': 'йцук', 'he': 'קוןר'},
                               'en', 'ru', 'йцук')
        short.confident['ru'].clear()
        short.conservative = False
        self.assertIsNone(suggest_live_early(short, skeys, 'en'))

    def test_rare_mode_rejects_competing_prefix_and_known_source_prefix(self):
        forms = {'en': 'lbdthcbz', 'ru': 'диверсия', 'he': 'ךנגאירנז'}
        engine, keys = fixture(forms, 'en', 'ru', 'диверсия',
                               competitors=[('he', 'ךנגאירנזים')])
        engine.confident['ru'].clear()
        engine.conservative = False
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))
        engine.words['he'].clear()
        engine.words['en'].add('lbdthcbzz')
        prepare_early(engine)
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))

    def test_rare_mode_rejects_source_word_exclusion_and_modified_keys(self):
        forms = {'en': 'lbdthcbz', 'ru': 'диверсия', 'he': 'ךנגאירנז'}
        engine, keys = fixture(forms, 'en', 'ru', 'диверсия')
        engine.confident['ru'].clear()
        engine.conservative = False
        self.assertIsNone(suggest_live_early(engine, keys[:-1] + [(90, True)], 'en'))
        engine.exclusions.add('диверсия')
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))
        engine.exclusions.clear()
        engine.words['en'].add('lbdthcbz')
        prepare_early(engine)
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))

    def test_relaxed_candidate_negative_guards(self):
        reps = {"ru": "руддщ", "en": "hello", "he": "אקךךם"}
        engine, keys = fixture(reps, "ru", "en", "hellos",
                               competitors=[("en", "helloy")])
        self.assertIsNotNone(evaluate_unique_language(engine, keys, "ru"))
        engine.words["ru"].add("руддщен")
        prepare_early(engine)
        self.assertIsNone(evaluate_unique_language(engine, keys, "ru"))
        engine.words["ru"].clear()
        engine.words["he"].add("אקךךםא")
        prepare_early(engine)
        self.assertIsNone(evaluate_unique_language(engine, keys, "ru"))

    def test_relaxed_four_letter_requires_confident_exact(self):
        reps = {"en": "akuo", "ru": "флгщ", "he": "שלום"}
        engine, keys = fixture(reps, "en", "he", "שלום")
        self.assertIsNone(suggest_early(engine, keys, "en"))
        self.assertEqual(evaluate_unique_language(engine, keys, "en").language, "he")
        engine.confident["he"].clear()
        self.assertIsNone(evaluate_unique_language(engine, keys, "en"))

    def test_sampled_valid_english_word_prefixes_zero_false_positives(self):
        """Offline keyboard-map proxy: real EN dictionary, standard RU key map."""
        data = Path(__file__).parent / "data"
        english = read_words(data / "en.txt")
        russian = read_words(data / "ru.txt")
        hebrew = read_words(data / "he.txt") | SAMPLE_HEBREW
        confident = {lang: read_words(data / f"confident_{lang}.txt")
                     for lang in ("en", "ru", "he")}
        engine = SimpleNamespace(words={"en": english, "ru": russian, "he": hebrew},
                                 confident=confident, exclusions=set())
        prepare_early(engine)
        us = "qwertyuiopasdfghjklzxcvbnm"
        ru = "йцукенгшщзфывапролдячсмить"
        translate = str.maketrans(us, ru)
        samples = [word for word in sorted(confident["en"])
                   if 5 <= len(word) <= 9 and word.isascii()
                   and word.isalpha() and set(word) <= set(us)][::15][:1500]
        results = {"valid": [0, 0], "mutated": [0, 0]}
        for word in samples:
            for kind, token in (("valid", word),
                                ("mutated", word[:-1] + ("z" if word[-1] != "z" else "q"))):
                engine.mapper = FixtureMapper({"en": token, "ru": token.translate(translate),
                                               "he": ""})
                keys = [(65 + i % 26, False) for i in range(len(token))]
                for idx, classify in enumerate((suggest_early, evaluate_unique_language)):
                    if classify(engine, keys, "en") is not None:
                        results[kind][idx] += 1
        print(f"EARLY_SAMPLE n={len(samples)} full_word_counts={results}")
        self.assertEqual(results["valid"], [0, 0])

    def test_no_index_fails_closed(self):
        engine, keys = fixture({"ru": "руддщ", "en": "hello", "he": "אקךךם"},
                               "ru", "en", "hellos")
        del engine._early_prefix_index
        self.assertIsNone(suggest_early(engine, keys, "ru"))

    def test_real_dictionary_benchmark_and_prefix_coverage(self):
        data = Path(__file__).parent / "data"
        words = {lang: read_words(data / f"{lang}.txt") for lang in ("ru", "en", "he")}
        words["he"] |= read_words(data / "he_custom.txt") | SAMPLE_HEBREW
        confident = {lang: read_words(data / f"confident_{lang}.txt")
                     for lang in words}
        confident["he"] |= read_words(data / "he_custom.txt") | SAMPLE_HEBREW
        # Fixed physical-key transliterations from known engine tests.
        cases = [
            ("ghjdthrf", "en", {"en": "ghjdthrf", "ru": "проверка", "he": "עיחגאירכ"}),
            ("руддщ", "ru", {"ru": "руддщ", "en": "hello", "he": "אקךךם"}),
            ("akuo", "en", {"en": "akuo", "ru": "флгщ", "he": "שלום"}),
        ]
        engine = SimpleNamespace(words=words, confident=confident,
                                 exclusions=read_words(data / "exclude.txt"))
        started = time.perf_counter()
        prepare_early(engine)
        build = time.perf_counter() - started
        outcomes = {}
        relaxed_outcomes = {}
        diagnostics = {}
        for label, source, representations in cases:
            engine.mapper = FixtureMapper(representations)
            stages = []
            for length in range(1, len(label) + 1):
                keys = [(65 + i % 26, False) for i in range(length)]
                result = suggest_early(engine, keys, source)
                stages.append(result.language if result else "-")
            outcomes[label] = stages
            relaxed_outcomes[label] = [
                (result.language if (result := evaluate_unique_language(
                    engine, [(65 + i % 26, False) for i in range(length)], source)) else "-")
                for length in range(1, len(label) + 1)
            ]
            diagnostics[label] = {
                lang: {"prefix": token,
                       "first_two": _prefix_matches(engine._early_prefix_index[lang], token),
                       "exact": token in words[lang],
                       "confident_exact": token in confident[lang]}
                for lang, token in representations.items()
            }
        engine.mapper = FixtureMapper(cases[0][2])
        keys = [(65 + i, False) for i in range(8)]
        rounds = 5000
        started = time.perf_counter()
        for _ in range(rounds):
            suggest_early(engine, keys, "en")
        elapsed = (time.perf_counter() - started) / rounds
        print(f"EARLY_REAL_LEXICON counts={ {k: len(v) for k,v in words.items()} } "
              f"index_build_s={build:.4f} call_us={elapsed*1e6:.2f} "
              f"stages={outcomes} relaxed={relaxed_outcomes} "
              f"diagnostics={ascii(diagnostics)}")
        self.assertEqual(len(outcomes), 3)

    def test_all_supported_directions_with_unique_unfinished_words(self):
        cases = [
            ({"ru": "руддщ", "en": "hello", "he": "אקךךם"}, "ru", "en", "hellos"),
            ({"en": "ghjdth", "ru": "провер", "he": "עיחגאי"}, "en", "ru", "проверка"),
            ({"en": "akuoy", "ru": "флгщн", "he": "שלוםו"}, "en", "he", "שלוםות"),
            ({"he": "עיחגאי", "ru": "провер", "en": "ghjdth"}, "he", "ru", "проверка"),
            ({"he": "אקךךם", "en": "hello", "ru": "руддщ"}, "he", "en", "hellos"),
            ({"ru": "флгщн", "en": "akuoy", "he": "שלוםו"}, "ru", "he", "שלוםות"),
        ]
        for representations, source, target, completion in cases:
            with self.subTest(source=source, target=target):
                engine, keys = fixture(representations, source, target, completion)
                answer = suggest_early(engine, keys, source)
                self.assertIsNotNone(answer)
                self.assertEqual((answer.typed, answer.replacement, answer.language),
                                 (representations[source], representations[target], target))

    def test_source_valid_prefix_blocks_suggestion(self):
        representations = {"ru": "руддщ", "en": "hello", "he": "אקךךם"}
        engine, keys = fixture(representations, "ru", "en", "hellos")
        engine.words["ru"].add("руддщен")
        prepare_early(engine)
        self.assertIsNone(suggest_early(engine, keys, "ru"))

    def test_competing_rare_destination_prefix_blocks(self):
        reps = {"ru": "руддщ", "en": "hello", "he": "אקךךם"}
        engine, keys = fixture(reps, "ru", "en", "hellos",
                               competitors=[("he", "אקךךםא")])
        self.assertIsNone(suggest_early(engine, keys, "ru"))

    def test_multiple_completions_in_target_block(self):
        reps = {"ru": "руддщ", "en": "hello", "he": "אקךךם"}
        engine, keys = fixture(reps, "ru", "en", "hellos",
                               competitors=[("en", "helloy")])
        self.assertIsNone(suggest_early(engine, keys, "ru"))

    def test_short_uppercase_punctuation_mixed_or_long_completion(self):
        reps = {"ru": "руддщ", "en": "hello", "he": "אקךךם"}
        engine, keys = fixture(reps, "ru", "en", "hellos")
        self.assertIsNone(suggest_early(engine, keys[:4], "ru"))
        self.assertIsNone(suggest_early(engine, keys[:-1] + [(69, True)], "ru"))
        engine.mapper.representations["ru"] = "руд.щ"
        self.assertIsNone(suggest_early(engine, keys, "ru"))
        engine.mapper.representations["ru"] = "руdдщ"
        self.assertIsNone(suggest_early(engine, keys, "ru"))
        engine.mapper.representations["ru"] = "руддщ"
        engine.words["en"] = {"helloworld"}
        engine.confident["en"] = {"helloworld"}
        prepare_early(engine)
        self.assertIsNone(suggest_early(engine, keys, "ru"))

    def test_evolving_word_reconsidered_on_each_keystroke(self):
        reps = {"ru": "руддщ", "en": "hello", "he": "אקךךם"}
        engine, keys = fixture(reps, "ru", "en", "hellos")
        self.assertIsNotNone(suggest_early(engine, keys, "ru"))
        engine.mapper.representations = {"ru": "руддщф", "en": "helloa", "he": "אקךךםא"}
        self.assertIsNone(suggest_early(engine, keys + [(70, False)], "ru"))


class LiveEarlyPolicyCharacterizationTests(unittest.TestCase):
    """Freeze observed LIVE decisions, not an approval of their safety.

    The older suggest_early/evaluate_unique_language tests do not establish
    what suggest_live_early actually returns. In particular, the live caller
    uses relaxed four-letter and multiple-completion rules. These synthetic
    dictionary tests cannot prove password-field safety or editor delivery.
    """

    @staticmethod
    def live_case(representations, source, target, completion, *, competitors=()):
        engine, keys = fixture(representations, source, target, completion,
                               competitors=competitors)
        engine.conservative = True
        # The production LanguageEngine exposes this method; the lightweight
        # dictionary fixture otherwise lacks it and never reaches the live gate.
        engine._confident = lambda language, word: word.casefold() in engine.confident[language]
        return engine, keys

    def test_live_four_letter_confident_exact_is_allowed_before_space(self):
        engine, keys = self.live_case({'en': 'akuo', 'ru': 'флгщ', 'he': 'שלום'},
                                      'en', 'he', 'שלום')
        self.assertIsNone(suggest_live_early(engine, keys[:3], 'en'))
        self.assertIsNone(suggest_early(engine, keys, 'en'))
        choice = suggest_live_early(engine, keys, 'en')
        self.assertIsNotNone(choice)
        self.assertEqual((choice.typed, choice.replacement, choice.language,
                          choice.reason), ('akuo', 'שלום', 'he', 'confident early prefix'))

    def test_live_four_letter_unknown_word_is_refused_even_with_rare_mode(self):
        engine, keys = self.live_case({'en': 'akuo', 'ru': 'флгщ', 'he': 'שלום'},
                                      'en', 'he', 'שלום')
        engine.confident['he'].clear()
        for rare_enabled in (False, True):
            with self.subTest(rare_enabled=rare_enabled):
                engine.conservative = not rare_enabled
                self.assertIsNone(suggest_live_early(engine, keys, 'en'))

    def test_live_five_letter_confident_exact_allows_multiple_same_language_completions(self):
        engine, keys = self.live_case({'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'},
                                      'ru', 'en', 'hello')
        engine.words['en'].update(('hellos', 'helloy'))
        prepare_early(engine)
        self.assertIsNone(suggest_early(engine, keys, 'ru'))
        choice = suggest_live_early(engine, keys, 'ru')
        self.assertIsNotNone(choice)
        self.assertEqual((choice.replacement, choice.language, choice.reason),
                         ('hello', 'en', 'confident early prefix'))

    def test_live_multiple_completions_without_confident_exact_are_refused(self):
        engine, keys = self.live_case({'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'},
                                      'ru', 'en', 'hellos',
                                      competitors=[('en', 'helloy')])
        engine.confident['en'].add('helloy')
        self.assertIsNone(suggest_early(engine, keys, 'ru'))
        self.assertIsNone(suggest_live_early(engine, keys, 'ru'))

    def test_live_unique_unfinished_prefix_uses_strict_fallback(self):
        engine, keys = self.live_case({'ru': 'руддщ', 'en': 'hello', 'he': 'אקךךם'},
                                      'ru', 'en', 'hellos')
        self.assertNotIn('hello', engine.confident['en'])
        self.assertIsNotNone(suggest_early(engine, keys, 'ru'))
        choice = suggest_live_early(engine, keys, 'ru')
        self.assertIsNotNone(choice)
        self.assertEqual((choice.replacement, choice.language), ('hello', 'en'))

    def test_live_competing_language_prefix_vetoes_confident_four_letter_candidate(self):
        engine, keys = self.live_case({'en': 'akuo', 'ru': 'флгщ', 'he': 'שלום'},
                                      'en', 'he', 'שלום',
                                      competitors=[('ru', 'флгщина')])
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))

    def test_live_competing_exact_language_requires_explicit_resolution(self):
        engine, keys = self.live_case({'en': 'akuo', 'ru': 'флгщ', 'he': 'שלום'},
                                      'en', 'he', 'שלום',
                                      competitors=[('ru', 'флгщ')])
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))
        engine.resolve_ambiguity = lambda matches, current: next(
            (match for match in matches if match[0] == 'he'), None)
        choice = suggest_live_early(engine, keys, 'en')
        self.assertIsNotNone(choice)
        self.assertEqual((choice.replacement, choice.language), ('שלום', 'he'))

    def test_live_known_source_prefix_and_excluded_destination_block_relaxed_path(self):
        engine, keys = self.live_case({'en': 'akuo', 'ru': 'флгщ', 'he': 'שלום'},
                                      'en', 'he', 'שלום')
        engine.words['en'].add('akuotest')
        prepare_early(engine)
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))
        engine.words['en'].clear()
        engine.exclusions.add('שלום')
        prepare_early(engine)
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))

    def test_live_shifted_input_and_missing_index_do_not_offer_four_letter_conversion(self):
        engine, keys = self.live_case({'en': 'akuo', 'ru': 'флгщ', 'he': 'שלום'},
                                      'en', 'he', 'שלום')
        self.assertIsNone(suggest_live_early(engine, keys[:-1] + [(79, True)], 'en'))
        del engine._early_prefix_index
        self.assertIsNone(suggest_live_early(engine, keys, 'en'))


if __name__ == "__main__":
    unittest.main()
