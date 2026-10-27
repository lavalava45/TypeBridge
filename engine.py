"""Three-layout word correction engine; no keyboard hooks or UI in this module."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import ctypes
from ctypes import wintypes
import sys
import unicodedata
import winreg
from frequency_rank import normalize_word


LANGS = {"ru": 0x0419, "en": 0x0409, "he": 0x040D}
ALTERNATIVE_PRIORITY = {"ru": ("en", "he"), "en": ("ru", "he"), "he": ("ru", "en")}
# Physical OEM keys used for letters in another supported layout.
LETTER_PUNCTUATION_KEYS = {0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xC0,
                           0xDB, 0xDC, 0xDD, 0xDE}
AMBIGUOUS_LAYOUT_SYMBOLS = frozenset("[];,.'`/\\-=<>:{}\"~")
WORD_AFFIXES = frozenset(".,;:'\"`()[]{}<>!?…")
HEBREW_NONLETTER_VKS = {0x51: "/", 0x57: "'"}  # Q/W are letters in RU/EN.


def candidate_token_key(mapper, vk: int, shift: bool, current: str,
                        has_prefix: bool) -> bool:
    """Keep keys that are letters in any supported layout, even at word start.

    For example EN comma is RU б. The complete token still has to pass the
    dictionary/ambiguity checks; recognizing a key does not authorize a write.
    """
    current_char = mapper.char(vk, current, shift)
    if len(current_char) != 1:
        return False
    if current_char.isalpha():
        return True
    if current_char not in AMBIGUOUS_LAYOUT_SYMBOLS:
        return False
    if (vk not in LETTER_PUNCTUATION_KEYS
            and not (current == "he" and
                     HEBREW_NONLETTER_VKS.get(vk) == current_char)):
        return False
    return any((symbol := mapper.char(vk, language, shift)).isalpha()
               and len(symbol) == 1 for language in LANGS if language != current)


def _script(letter: str) -> str | None:
    name = unicodedata.name(letter, "")
    return next((script for script in ("LATIN", "CYRILLIC", "HEBREW")
                 if name.startswith(script + " ")), None)


def _one_script_letters(text: str) -> bool:
    scripts = {_script(char) for char in text}
    return len(scripts) == 1 and None not in scripts


def _valid_typed(keys: list[tuple[int, bool]], typed: str, current: str) -> bool:
    letters = []
    for (vk, _), char in zip(keys, typed):
        if char.isalpha():
            letters.append(char)
        elif char in AMBIGUOUS_LAYOUT_SYMBOLS:
            if (vk not in LETTER_PUNCTUATION_KEYS
                    and not (current == "he" and
                             HEBREW_NONLETTER_VKS.get(vk) == char)):
                return False
        else:
            return False
    # Preserve ordinary word boundaries and reject mixed writing systems.
    return not letters or _one_script_letters("".join(letters))
SAMPLE_HEBREW = {
    "שלום", "תודה", "בבקשה", "כן", "לא", "אני", "אתה", "את", "אנחנו", "הוא",
    "היא", "הם", "הן", "זה", "זאת", "מה", "מי", "איפה", "מתי", "למה",
    "איך", "טוב", "טובה", "בוקר", "ערב", "לילה", "יום", "היום", "מחר",
    "אתמול", "מים", "בית", "ספר", "עבודה", "עולם", "ישראל", "עברית",
    "אנגלית", "רוסית", "שפה", "מילה", "מילים", "תוכנית", "מחשב", "אור",
    "אהבה", "חבר", "חברה", "יפה", "חדש", "גדול", "קטן", "רוצה", "צריך",
    "אפשר", "עכשיו", "אחר", "מאוד", "גם", "עם", "של", "על", "כאן",
}


def read_words(path: Path, language: str | None = None) -> set[str]:
    return {(normalize_word(line.strip(), language) if language else line.strip().casefold())
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


@dataclass(frozen=True)
class Decision:
    typed: str
    replacement: str
    language: str
    reason: str


def configured_layout_list() -> list[tuple[int, str, int]]:
    """Resolve user-preferred Windows keyboard layouts without loading any.

    On Windows 11 a KLID such as 0002040D (Hebrew Standard) is exposed by
    GetKeyboardLayoutList as an HKL such as F03D040D. The Layout Id registry
    value supplies the 003D portion. Preload/Substitutes preserve the user's
    actual choice instead of silently loading the older 0000040D Hebrew map.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Keyboard Layout\Substitutes") as key:
            substitutions = {}
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                substitutions[name.casefold()] = str(value).casefold()
                index += 1
    except OSError:
        substitutions = {}

    preferred: list[tuple[int, str, int]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Keyboard Layout\Preload") as key:
            entries = []
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                if name.isdecimal():
                    entries.append((int(name), str(value).casefold()))
                index += 1
    except OSError:
        return preferred

    for _, initial in sorted(entries):
        klid = substitutions.get(initial, initial)
        if len(klid) != 8:
            continue
        try:
            langid = int(klid[-4:], 16)
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                "SYSTEM\\CurrentControlSet\\Control\\Keyboard Layouts\\"
                                + klid) as key:
                try:
                    layout_id, _ = winreg.QueryValueEx(key, "Layout Id")
                except OSError:
                    layout_id = None
            if layout_id:
                high = 0xF000 | int(layout_id, 16)
            elif klid[:4] == "0000":
                high = langid
            else:
                continue  # No reliable HKL mapping for this custom layout.
        except (OSError, ValueError):
            continue
        preferred.append((langid, klid, ((high << 16) | langid) & 0xFFFFFFFF))
    return preferred


def configured_layout_entries() -> dict[int, tuple[str, int]]:
    preferred = {}
    for language, klid, hkl in configured_layout_list():
        preferred.setdefault(language, (klid, hkl))
    return preferred


def configured_layout_hkls() -> dict[int, int]:
    return {language: hkl for language, (_, hkl) in configured_layout_entries().items()}


def select_loaded_layouts(loaded: list[int], preferred: dict[int, int]) -> dict[str, int]:
    """Pure selection over already-loaded HKLs; never add an input method."""
    selected: dict[str, int] = {}
    for lang, langid in LANGS.items():
        candidates = [value for value in loaded if value & 0xFFFF == langid]
        if not candidates:
            raise OSError(f"Windows has no loaded {lang.upper()} keyboard layout")
        selected[lang] = next((value for value in candidates
                               if (value & 0xFFFFFFFF) == preferred.get(langid)),
                              candidates[0])
    return selected


class LayoutMapper:
    """Map physical keys through user's existing layouts, without loading any."""

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int,
                                                     ctypes.POINTER(wintypes.HANDLE)]
        self.user32.GetKeyboardLayoutList.restype = ctypes.c_int
        self.user32.ToUnicodeEx.argtypes = [wintypes.UINT, wintypes.UINT,
                                              ctypes.POINTER(ctypes.c_ubyte),
                                              wintypes.LPWSTR, ctypes.c_int,
                                              wintypes.UINT, wintypes.HANDLE]
        self.user32.ToUnicodeEx.restype = ctypes.c_int
        count = self.user32.GetKeyboardLayoutList(0, None)
        if count <= 0:
            raise OSError("Windows did not report any loaded keyboard layouts")
        handles = (wintypes.HANDLE * count)()
        found = self.user32.GetKeyboardLayoutList(count, handles)
        if found <= 0:
            raise OSError("Could not enumerate loaded Windows keyboard layouts")
        # Keep the native 64-bit HKL including sign extension. Windows' custom
        # Fxxx HKLs fail ToUnicodeEx when zero-extended from a 32-bit value.
        loaded = [int(hkl) for hkl in handles[:found] if hkl]
        configured = configured_layout_entries()
        self.layouts = select_loaded_layouts(
            loaded, {language: entry[1] for language, entry in configured.items()})

        # ToUnicodeEx cannot always translate through a globally enumerated
        # HKL until that same keyboard layout is bound to this process.
        # Bind ONLY the user's exact, already-loaded KLIDs (KLF_NOTELLSHELL).
        # Loading 0000040D instead of 0002040D would add a second Hebrew.
        self.user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
        self.user32.LoadKeyboardLayoutW.restype = wintypes.HANDLE
        for language, langid in LANGS.items():
            entry = configured.get(langid)
            if entry is None or (self.layouts[language] & 0xFFFFFFFF) != entry[1]:
                # On Windows 11 the legacy Preload/Substitutes entries may be
                # absent even though an input profile's HKL is already loaded.
                # Do NOT invent an old KLID or load another Hebrew layout.
                # Use the enumerated HKL only if ToUnicodeEx confirms its
                # expected writing system. Otherwise refuse the entire startup.
                sample = self.char(0x41, language)
                expected = {'ru': 'CYRILLIC', 'en': 'LATIN', 'he': 'HEBREW'}[language]
                if len(sample) != 1 or _script(sample) != expected:
                    raise OSError(f"Loaded {language.upper()} layout cannot be verified "
                                  'without matching Windows layout preferences')
                continue
            bound = int(self.user32.LoadKeyboardLayoutW(entry[0], 0x80) or 0)
            if (bound & 0xFFFFFFFF) != entry[1]:
                raise OSError(f"Windows did not bind the configured {language.upper()} layout")
            self.layouts[language] = bound

    @lru_cache(maxsize=512)
    def char(self, vk: int, language: str, shift: bool = False) -> str:
        state = (ctypes.c_ubyte * 256)()
        if shift:
            state[0x10] = 0x80
        buf = ctypes.create_unicode_buffer(8)
        # Flag 4 asks ToUnicodeEx not to change keyboard dead-key state.
        count = self.user32.ToUnicodeEx(vk, 0, state, buf, len(buf), 4,
                                         self.layouts[language])
        return buf.value[:count] if count == 1 else ""

    def word(self, keys: list[tuple[int, bool]], language: str) -> str:
        return "".join(self.char(vk, language, shift) for vk, shift in keys)


class LanguageEngine:
    def __init__(self, data_dir: Path, *, conservative: bool = True,
                 user_lexicon=None, user_lexicon_dir: Path | None = None):
        if user_lexicon is not None and user_lexicon_dir is not None:
            raise ValueError("choose lexicon instance or directory")
        if user_lexicon_dir is not None:
            from user_lexicon import UserLexicon
            user_lexicon = UserLexicon.load(user_lexicon_dir)
        if user_lexicon is not None:
            from user_lexicon import UserLexicon
            if not isinstance(user_lexicon, UserLexicon):
                raise ValueError("invalid user lexicon")
        self.user_lexicon = user_lexicon
        self.mapper = LayoutMapper()
        self.words = {
            "ru": read_words(data_dir / "ru.txt", "ru"),
            "en": read_words(data_dir / "en.txt", "en"),
            "he": read_words(data_dir / "he.txt", "he") | read_words(data_dir / "he_custom.txt", "he") | SAMPLE_HEBREW,
        }
        self.conservative = conservative
        # An incomplete portable data/ folder silently disabled most
        # conservative corrections. Refuse startup before installing hooks.
        missing_confidence = [f'confident_{lang}.txt' for lang in LANGS
                              if not (data_dir / f'confident_{lang}.txt').is_file()]
        if missing_confidence:
            raise ValueError('Missing required confidence dictionaries: '
                             + ', '.join(missing_confidence))
        self.confident = {
            lang: read_words(data_dir / f"confident_{lang}.txt", lang)
            for lang in LANGS
        }
        empty_confidence = [f'confident_{lang}.txt' for lang in LANGS
                            if not self.confident[lang]]
        if empty_confidence:
            raise ValueError('Empty required confidence dictionaries: '
                             + ', '.join(empty_confidence))
        # Explicit user additions are always accepted as intentional words.
        self.confident["he"] |= read_words(data_dir / "he_custom.txt") | SAMPLE_HEBREW
        self.exclusions = read_words(data_dir / "exclude.txt")
        # Russian spelling commonly omits the dots; preserve output spelling.
        for bucket in (self.words["ru"], self.confident["ru"]):
            bucket.update({word.replace("ё", "е") for word in bucket})
        # Small, explicit spelling supplement; not a relaxed ambiguity threshold.
        self.words["ru"].update(("а", "и", "поясни"))
        self.confident["ru"].update(("а", "и", "поясни"))
        self.words["en"].update(("a", "i"))

    def _excluded(self, language: str, word: str) -> bool:
        return word.casefold() in self.exclusions or self._user_contains("exclusions", language, word)

    def _user_contains(self, kind: str, language: str, word: str) -> bool:
        lexicon = getattr(self, "user_lexicon", None)
        if lexicon is None:
            return False
        try:
            return lexicon.contains(kind, language, word)
        except ValueError:
            # Engine candidates can contain OEM punctuation; user lexicon
            # intentionally accepts strictly alphabetic words only.
            return False

    def _known(self, language: str, word: str) -> bool:
        folded = normalize_word(word, language)
        return folded in self.words[language] or self._user_contains("words", language, word)

    def _confident(self, language: str, word: str) -> bool:
        folded = normalize_word(word, language)
        return folded in self.confident[language] or self._user_contains("words", language, word)

    def decide_token(self, keys: list[tuple[int, bool]], current: str,
                     *, allow_rare: bool = False) -> Decision | None:
        """Correct a complete word, preserving literal surrounding punctuation.

        Whole-token matches take priority: EN ',tksq' is RU 'белый', not a
        comma plus a truncated word. Affix interpretations must agree; a known
        source word or an ambiguous/rare whole-token match blocks fallback.
        """
        direct = self.decide(keys, current, allow_rare=allow_rare)
        if direct is not None or current not in LANGS or not 2 <= len(keys) <= 40:
            return direct
        typed = self.mapper.word(keys, current)
        if len(typed) != len(keys):
            return None
        if (self._excluded(current, typed)
                or any(self._known(lang, self.mapper.word(keys, lang)) for lang in LANGS)):
            return None
        leading = 0
        while leading < min(4, len(typed)) and typed[leading] in WORD_AFFIXES:
            leading += 1
        trailing = 0
        while trailing < min(4, len(typed)) and typed[-trailing - 1] in WORD_AFFIXES:
            trailing += 1
        matches = {}
        for left in range(leading + 1):
            for right in range(trailing + 1):
                end = len(keys) - right
                if (left == right == 0) or end - left < 1:
                    continue
                core = keys[left:end]
                core_typed = typed[left:end]
                if self._known(current, core_typed) or self._excluded(current, core_typed):
                    return None
                decision = self.decide(core, current, allow_rare=allow_rare)
                if decision:
                    replacement = typed[:left] + decision.replacement + typed[end:]
                    matches[(decision.language, replacement)] = Decision(
                        typed, replacement, decision.language, "word with literal punctuation")
        return next(iter(matches.values())) if len(matches) == 1 else None

    def decide(self, keys: list[tuple[int, bool]], current: str,
               *, allow_rare: bool = False) -> Decision | None:
        if not keys or len(keys) > 40 or current not in self.words:
            return None
        typed = self.mapper.word(keys, current)
        if (len(typed) != len(keys) or not _valid_typed(keys, typed, current)
                or self._excluded(current, typed)):
            return None
        if self._known(current, typed) and not allow_rare:
            return None
        # A correctly spelled current-language word followed by punctuation
        # must never be converted just because its physical OEM key maps to a
        # letter in another layout (e.g. "hello," or "слово.").
        if (len(typed) > 1 and not typed[-1].isalpha()
                and self._known(current, typed[:-1])):
            return None
        matches = []
        for lang in LANGS:
            if lang == current:
                continue
            candidate = self.mapper.word(keys, lang)
            if (len(candidate) == len(keys) and _one_script_letters(candidate)
                    and self._known(lang, candidate)):
                matches.append((lang, candidate))
        if len(matches) == 1:
            lang, replacement = matches[0]
        else:
            selected = self.resolve_ambiguity(matches, current, allow_rare=allow_rare)
            if selected is None:
                return None
            lang, replacement = selected
        # Priority selects only eligible destinations; the existing confidence
        # requirement is also enforced here for the single-candidate path.
        # For a single letter, a unique dictionary word is enough; ambiguity
        # and source-word/exclusion checks above still apply in every language.
        if self.conservative and len(keys) > 1 and not allow_rare and not self._confident(lang, replacement):
            return None
        if self._excluded(lang, replacement):
            return None
        return Decision(typed, replacement, lang,
                        "unique dictionary match" if len(matches) == 1
                        else "configured language priority")

    def resolve_ambiguity(self, matches: list[tuple[str, str]], current: str,
                         *, allow_rare: bool = False) -> tuple[str, str] | None:
        """Only ambiguity handling; known source and confidence checks stay outside."""
        if any(self._excluded(lang, word) for lang, word in matches):
            return None
        matches = [(lang, word) for lang, word in matches
                   if allow_rare or not self.conservative or len(word) == 1 or self._confident(lang, word)]
        for language in ALTERNATIVE_PRIORITY.get(current, ()):
            candidates = [(lang, word) for lang, word in matches if lang == language]
            if len(candidates) == 1:
                return candidates[0]
            if candidates:
                return None
        return None


def source_data_dir() -> Path:
    """Dictionaries stay external to frozen executables for independent updates."""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "data"
