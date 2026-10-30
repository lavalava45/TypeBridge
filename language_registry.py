"""Built-in language descriptions and the active-language selection policy.

Metadata only: importing this module never reads dictionaries or touches Windows
keyboard layouts. External packages can supply additional descriptions through
language_pack_loader without mutating these three compatibility constants.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DictionaryAddition:
    filename: str
    for_words: bool = True
    for_confident: bool = False


@dataclass(frozen=True)
class LanguageDescription:
    code: str
    name: str
    langid: int
    unicode_script: str
    alphabet: str
    dictionary_file: str
    confident_file: str
    additions: tuple[DictionaryAddition, ...]
    alternatives: tuple[str, ...]
    personal_alphabet_policy: str | None = field(default=None, compare=False)


# Order is significant: RU, EN, HE is the current engine and dictionary order.
# Alphabets describe Unicode letters, including ё and Hebrew final forms. The
# existing Unicode-name script check and word normalization remain unchanged.
LANGUAGE_REGISTRY: tuple[LanguageDescription, ...] = (
    LanguageDescription(
        code="ru", name="Русский", langid=0x0419, unicode_script="CYRILLIC",
        alphabet="абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        dictionary_file="ru.txt", confident_file="confident_ru.txt",
        additions=(), alternatives=("en", "he"),
    ),
    LanguageDescription(
        code="en", name="Английский", langid=0x0409, unicode_script="LATIN",
        alphabet="abcdefghijklmnopqrstuvwxyz",
        dictionary_file="en.txt", confident_file="confident_en.txt",
        additions=(), alternatives=("ru", "he"),
    ),
    LanguageDescription(
        code="he", name="Иврит", langid=0x040D, unicode_script="HEBREW",
        alphabet="אבגדהוזחטיכלמנסעפצקרשתךםןףץ",
        dictionary_file="he.txt", confident_file="confident_he.txt",
        additions=(DictionaryAddition("he_custom.txt", for_confident=True),),
        alternatives=("ru", "en"),
    ),
)

LANGUAGE_BY_CODE = {language.code: language for language in LANGUAGE_REGISTRY}
DEFAULT_ACTIVE_LANGUAGES = tuple(language.code for language in LANGUAGE_REGISTRY)
MAX_ACTIVE_LANGUAGES = 3


def validate_active_languages(codes, available_codes=None) -> tuple[str, ...]:
    """Allow any installed codes, but at most three active languages at once.

    Without an explicit catalog keep the old RU/EN/HE-only behavior. The
    package catalog is opt-in until the engine and profile support its data.
    """
    known = LANGUAGE_BY_CODE if available_codes is None else available_codes
    if (not isinstance(codes, (tuple, list)) or not codes
            or len(codes) > MAX_ACTIVE_LANGUAGES
            or any(not isinstance(code, str) or code not in known
                   for code in codes)
            or len(set(codes)) != len(codes)):
        raise ValueError("Select one to three unique available language codes")
    return tuple(codes)

# Shared exclusions are not a fourth language dictionary.
SHARED_DICTIONARY_FILES = ("exclude.txt",)
