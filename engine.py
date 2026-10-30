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
from language_registry import (DEFAULT_ACTIVE_LANGUAGES, LANGUAGE_BY_CODE,
                               LANGUAGE_REGISTRY, SHARED_DICTIONARY_FILES,
                               validate_active_languages)


# Preserve the historical public imports and their dict / tuple value types.
LANGS = {language.code: language.langid for language in LANGUAGE_REGISTRY}
ALTERNATIVE_PRIORITY = {language.code: language.alternatives
                        for language in LANGUAGE_REGISTRY}
# Physical OEM keys used for letters in another supported layout.
LETTER_PUNCTUATION_KEYS = {0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xC0,
                           0xDB, 0xDC, 0xDD, 0xDE}
AMBIGUOUS_LAYOUT_SYMBOLS = frozenset("[];,.'`/\\-=<>:{}\"~")
WORD_AFFIXES = frozenset(".,;:'\"`()[]{}<>!?…")
HEBREW_NONLETTER_VKS = {0x51: "/", 0x57: "'"}  # Q/W are letters in RU/EN.


def candidate_token_key(mapper, vk: int, shift: bool, current: str,
                        has_prefix: bool, *, active_languages=None,
                        policies=None) -> bool:
    """Keep keys that are letters in an active layout, even at word start.

    For example EN comma is RU б. The complete token still has to pass the
    dictionary/ambiguity checks; recognizing a key does not authorize a write.
    """
    active = LANGS if active_languages is None else active_languages
    if current not in active:
        return False
    current_char = mapper.char(vk, current, shift)
    if len(current_char) != 1:
        return False
    if current_char.isalpha():
        return True
    if current_char not in AMBIGUOUS_LAYOUT_SYMBOLS:
        return False
    extra = (dict(policies[current].behavior.source_nonletter_keys)
             if policies is not None and current in policies else
             HEBREW_NONLETTER_VKS if current == 'he' else {})
    if (vk not in LETTER_PUNCTUATION_KEYS
            and extra.get(vk) != current_char):
        return False
    return any((symbol := mapper.char(vk, language, shift)).isalpha()
               and len(symbol) == 1 for language in active if language != current)


def _script(letter: str, catalog=None) -> str | None:
    name = unicodedata.name(letter, "")
    descriptions = LANGUAGE_REGISTRY if catalog is None else catalog.values()
    return next((language.unicode_script for language in descriptions
                 if name.startswith(language.unicode_script + " ")), None)


def _one_script_letters(text: str, catalog=None) -> bool:
    scripts = {_script(char, catalog) for char in text}
    return len(scripts) == 1 and None not in scripts


def _valid_typed(keys: list[tuple[int, bool]], typed: str, current: str,
                 catalog=None) -> bool:
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
    return not letters or _one_script_letters("".join(letters), catalog)
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


def select_loaded_layouts(loaded: list[int], preferred: dict[int, int],
                          active_languages=None, catalog=None) -> dict[str, int]:
    """Pure selection over already-loaded HKLs; never add an input method."""
    selected: dict[str, int] = {}
    active = (DEFAULT_ACTIVE_LANGUAGES if active_languages is None
              else validate_active_languages(active_languages, catalog))
    languages = LANGS if catalog is None else {code: item.langid for code, item in catalog.items()}
    for lang in active:
        langid = languages[lang]
        candidates = [value for value in loaded if value & 0xFFFF == langid]
        if not candidates:
            raise OSError(f"Windows has no loaded {lang.upper()} keyboard layout")
        selected[lang] = next((value for value in candidates
                               if (value & 0xFFFFFFFF) == preferred.get(langid)),
                              candidates[0])
    return selected


def check_active_layouts_loaded(active_languages, catalog=None) -> None:
    """GUI preflight, read-only: reject missing HKLs without altering Windows."""
    active = validate_active_languages(active_languages, catalog)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int,
                                              ctypes.POINTER(wintypes.HANDLE)]
    user32.GetKeyboardLayoutList.restype = ctypes.c_int
    count = user32.GetKeyboardLayoutList(0, None)
    if count <= 0:
        raise OSError('Windows не вернула список раскладок')
    handles = (wintypes.HANDLE * count)()
    found = user32.GetKeyboardLayoutList(count, handles)
    if found <= 0:
        raise OSError('Не удалось прочитать список раскладок')
    select_loaded_layouts([int(x) for x in handles[:found] if x], {}, active, catalog)


class LayoutMapper:
    """Map physical keys through user's existing layouts, without loading any."""

    def __init__(self, active_languages=None, catalog=None):
        active = (DEFAULT_ACTIVE_LANGUAGES if active_languages is None
                  else validate_active_languages(active_languages, catalog))
        descriptions = LANGUAGE_BY_CODE if catalog is None else catalog
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
            loaded, {language: entry[1] for language, entry in configured.items()},
            active, catalog)

        # ToUnicodeEx cannot always translate through a globally enumerated
        # HKL until that same keyboard layout is bound to this process.
        # Bind ONLY the user's exact, already-loaded KLIDs (KLF_NOTELLSHELL).
        # Loading 0000040D instead of 0002040D would add a second Hebrew.
        self.user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
        self.user32.LoadKeyboardLayoutW.restype = wintypes.HANDLE
        for language in active:
            langid = descriptions[language].langid
            entry = configured.get(langid)
            if entry is None or (self.layouts[language] & 0xFFFFFFFF) != entry[1]:
                # On Windows 11 the legacy Preload/Substitutes entries may be
                # absent even though an input profile's HKL is already loaded.
                # Do NOT invent an old KLID or load another Hebrew layout.
                # Use the enumerated HKL only if ToUnicodeEx confirms its
                # expected writing system. Otherwise refuse the entire startup.
                sample = self.char(0x41, language)
                expected = descriptions[language].unicode_script
                if len(sample) != 1 or _script(sample, descriptions) != expected:
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


DICTIONARY_FILES = (
    *(language.dictionary_file for language in LANGUAGE_REGISTRY),
    *(language.confident_file for language in LANGUAGE_REGISTRY),
    *(addition.filename for language in LANGUAGE_REGISTRY
      for addition in language.additions),
    *SHARED_DICTIONARY_FILES,
)


def require_dictionary_files(data_dir, descriptions=None):
    data_dir = Path(data_dir)
    # With no explicit selection, retain the original full three-language
    # requirements and error behavior. R16/3b can validate selected built-ins
    # before reading them without weakening the legacy default startup.
    files = DICTIONARY_FILES if descriptions is None else (
        *(item.dictionary_file for item in descriptions),
        *(item.confident_file for item in descriptions),
        *(extra.filename for item in descriptions for extra in item.additions),
        *SHARED_DICTIONARY_FILES,
    )
    missing = [name for name in files if not (data_dir / name).is_file()]
    if missing:
        raise ValueError(f'Неполная папка словарей: {data_dir.resolve()}\n'
                         f'Отсутствуют файлы: {", ".join(missing)}\n'
                         'Распакуй архив TypeBridge целиком, сохранив папку data рядом с EXE.')
    for name in files:
        try:
            with (data_dir / name).open('rb') as stream:
                stream.read(1)
        except OSError as error:
            raise OSError(f'Не удалось прочитать словарь: {data_dir / name}\n'
                          'Проверь доступ к файлу и распакуй архив TypeBridge целиком.') from error


def _read_lexical_data(data_dir, descriptions=None):
    descriptions = LANGUAGE_REGISTRY if descriptions is None else tuple(descriptions)
    words = {
        language.code: read_words(data_dir / language.dictionary_file, language.code)
        for language in descriptions
    }
    for language in descriptions:
        for addition in language.additions:
            if addition.for_words:
                words[language.code].update(read_words(data_dir / addition.filename,
                                                       language.code))
    if 'he' in words:
        words["he"] |= SAMPLE_HEBREW
    # An incomplete portable data/ folder silently disabled most
    # conservative corrections. Refuse startup before installing hooks.
    missing_confidence = [language.confident_file for language in descriptions
                          if not (data_dir / language.confident_file).is_file()]
    if missing_confidence:
        raise ValueError('Missing required confidence dictionaries: '
                         + ', '.join(missing_confidence))
    confident = {
        language.code: read_words(data_dir / language.confident_file, language.code)
        for language in descriptions
    }
    empty_confidence = [language.confident_file for language in descriptions
                        if not confident[language.code]]
    if empty_confidence:
        raise ValueError('Empty required confidence dictionaries: '
                         + ', '.join(empty_confidence))
    # Explicit user additions are always accepted as intentional words.
    for language in descriptions:
        for addition in language.additions:
            if addition.for_confident:
                # Preserve the original casefold-only reading of additions to
                # confidence (the main dictionaries use normalize_word).
                confident[language.code].update(read_words(data_dir / addition.filename))
    if 'he' in confident:
        confident["he"] |= SAMPLE_HEBREW
    exclusions = read_words(data_dir / SHARED_DICTIONARY_FILES[0])
    # Russian spelling commonly omits the dots; preserve output spelling.
    if 'ru' in words:
        for bucket in (words["ru"], confident["ru"]):
            bucket.update({word.replace("ё", "е") for word in bucket})
    # Small, explicit spelling supplement; not a relaxed ambiguity threshold.
    if 'ru' in words:
        words["ru"].update(("а", "и", "поясни"))
        confident["ru"].update(("а", "и", "поясни"))
    if 'en' in words:
        words["en"].update(("a", "i"))
    return {'words': words, 'confident': confident, 'exclusions': exclusions}


class LanguageEngine:
    def diagnose_token(self, keys, current, *, allow_rare=False,
                       exact_policy=False):
        """Optional read-only reason code; never invoked in the normal hook."""
        from decision_diagnostics import explain_token
        return explain_token(self, keys, current, allow_rare=allow_rare,
                             exact_policy=exact_policy)

    def diagnose_early(self, keys, current):
        """Optional prefix reason code, without changing the early policy."""
        from decision_diagnostics import explain_early
        return explain_early(self, keys, current)

    def __init__(self, data_dir: Path, *, conservative: bool = True,
                 user_lexicon=None, user_lexicon_dir: Path | None = None,
                 cache_dir: Path | None = None, cache_progress=None,
                 active_languages=None, pack_root: Path | None = None,
                 engine_backend: str = 'unified'):
        if engine_backend not in ('unified', 'legacy'):
            raise ValueError('Unknown TypeBridge language-engine backend')
        self.engine_backend = engine_backend
        self._policy_engine = None
        if engine_backend == 'unified':
            from language_unified_runtime import prepare_selected
            from language_decision_v2 import PolicyDecisionEngine
            if pack_root is not None and active_languages is None:
                raise ValueError('External language activation requires explicit active_languages')
            if user_lexicon is not None and user_lexicon_dir is not None:
                raise ValueError('choose lexicon instance or directory')
            if user_lexicon_dir is not None:
                from user_lexicon import UserLexicon
                user_lexicon = UserLexicon.load(user_lexicon_dir)
            if user_lexicon is not None:
                from user_lexicon import UserLexicon
                if not isinstance(user_lexicon, UserLexicon):
                    raise ValueError('invalid user lexicon')
            selected = (DEFAULT_ACTIVE_LANGUAGES if active_languages is None
                        else tuple(active_languages))
            data, self.cache_status, catalog, languages = prepare_selected(
                data_dir, pack_root, selected, cache_dir=cache_dir,
                cache_progress=cache_progress)
            self.language_catalog = catalog
            self.language_policies = languages
            self.active_languages = validate_active_languages(selected, catalog)
            self.language_priority = {code: lang.description.alternatives
                                      for code, lang in languages.items()}
            self.user_lexicon = user_lexicon
            self.conservative = conservative
            self.mapper = LayoutMapper(active_languages=self.active_languages,
                                       catalog=self.language_catalog)
            self.words = {lang: set(data['words'][lang]) for lang in self.active_languages}
            self.confident = {lang: set(data['confident'][lang])
                              for lang in self.active_languages}
            self.exclusions = set(data['exclusions'])
            if cache_dir is not None:
                self._startup_base_index = {lang: tuple(sorted(self.words[lang]))
                                            for lang in self.active_languages}
            self._policy_engine = PolicyDecisionEngine(
                packs=languages, active=self.active_languages, mapper=self.mapper,
                words=self.words, confident=self.confident, exclusions=self.exclusions,
                conservative=conservative, personal=user_lexicon)
            return
        # Retain the original full set for old configurations.
        self.language_catalog = None
        self.language_priority = ALTERNATIVE_PRIORITY
        if pack_root is not None:
            if active_languages is None:
                raise ValueError('External language activation requires explicit active_languages')
            from language_pack_loader import discover_language_packs
            from language_registry import LANGUAGE_BY_CODE
            external_codes = tuple(code for code in active_languages
                                   if code not in LANGUAGE_BY_CODE)
            verified_packages = discover_language_packs(
                pack_root, selected_codes=external_codes)
            self.language_catalog = dict(LANGUAGE_BY_CODE)
            used_langids = {description.langid for description in LANGUAGE_BY_CODE.values()}
            for code, package in verified_packages.items():
                if package.description.langid in used_langids:
                    raise ValueError(f'Language pack LANGID conflict: {code}')
                self.language_catalog[code] = package.description
                used_langids.add(package.description.langid)
            self.active_languages = validate_active_languages(
                active_languages, self.language_catalog)
            self.language_priority = {code: description.alternatives
                                      for code, description in self.language_catalog.items()}
        else:
            self.active_languages = (DEFAULT_ACTIVE_LANGUAGES if active_languages is None
                                     else validate_active_languages(active_languages))
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
        if pack_root is None:
            require_dictionary_files(data_dir)
            self.mapper = (LayoutMapper() if self.active_languages == DEFAULT_ACTIVE_LANGUAGES
                           else LayoutMapper(active_languages=self.active_languages))
        else:
            from language_lexicon import load_selected_lexicon
            data, self.cache_status = load_selected_lexicon(
                data_dir, pack_root, self.active_languages, cache_dir=cache_dir,
                verified_packages=verified_packages)
            self.mapper = LayoutMapper(active_languages=self.active_languages,
                                       catalog=self.language_catalog)
        self.conservative = conservative
        if pack_root is not None:
            if cache_dir is not None:
                self._startup_base_index = {lang: tuple(data['words'][lang])
                                            for lang in self.active_languages}
        elif cache_dir is None:
            data = _read_lexical_data(data_dir)
            self.cache_status = 'disabled'
        else:
            from dictionary_cache import load_or_build, policy_digest
            from frequency_rank import normalize_word
            # A registry-only change to an addition's role must invalidate the
            # prepared lexicon even when source file bytes stay the same.
            policy = policy_digest((_read_lexical_data, normalize_word, read_words),
                                   ['lexicon-v1', sorted(SAMPLE_HEBREW),
                                    [(language.code, language.dictionary_file,
                                      language.confident_file,
                                      [(addition.filename, addition.for_words,
                                        addition.for_confident)
                                       for addition in language.additions])
                                     for language in LANGUAGE_REGISTRY]])
            data, self.cache_status = load_or_build(
                data_dir, DICTIONARY_FILES, policy, lambda: _read_lexical_data(data_dir),
                cache_dir, progress=cache_progress)
            self._startup_base_index = {lang: tuple(data['words'][lang]) for lang in LANGS}
        self.words = data['words'] if cache_dir is None else {lang: set(data['words'][lang]) for lang in (self.active_languages if pack_root is not None else LANGS)}
        self.confident = data['confident'] if cache_dir is None else {lang: set(data['confident'][lang]) for lang in (self.active_languages if pack_root is not None else LANGS)}
        self.exclusions = set(data['exclusions'])

    def _normalize(self, word: str, language: str) -> str:
        if getattr(self, '_policy_engine', None) is not None:
            return self._policy_engine.normalize(language, word)
        # Pack v1 explicitly declares casefold-only normalization. Do not
        # silently apply the legacy NFKC/combining-mark policy to new scripts.
        if getattr(self, 'language_catalog', None) is not None and language not in LANGUAGE_BY_CODE:
            return word.casefold()
        return normalize_word(word, language)

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
        folded = self._normalize(word, language)
        return folded in self.words[language] or self._user_contains("words", language, word)

    def _confident(self, language: str, word: str) -> bool:
        folded = self._normalize(word, language)
        return folded in self.confident[language] or self._user_contains("words", language, word)

    def _sync_policy(self):
        """Live settings/lexicon replacements must not leave a stale policy."""
        policy = getattr(self, '_policy_engine', None)
        if policy is not None:
            policy.conservative = self.conservative
            policy.personal = self.user_lexicon
            policy.exclusions = self.exclusions
        return policy

    def decide_token(self, keys: list[tuple[int, bool]], current: str,
                     *, allow_rare: bool = False) -> Decision | None:
        """Correct a complete word, preserving literal surrounding punctuation.

        Whole-token matches take priority: EN ',tksq' is RU 'белый', not a
        comma plus a truncated word. Affix interpretations must agree; a known
        source word or an ambiguous/rare whole-token match blocks fallback.
        """
        policy = self._sync_policy()
        if policy is not None:
            result = policy.decide_token(keys, current, allow_rare=allow_rare)
            return Decision(result.typed, result.replacement, result.language,
                            result.reason) if result is not None else None
        direct = self.decide(keys, current, allow_rare=allow_rare)
        if (direct is not None or current not in getattr(self, 'active_languages', LANGS)
                or not 2 <= len(keys) <= 40):
            return direct
        typed = self.mapper.word(keys, current)
        if len(typed) != len(keys):
            return None
        if (self._excluded(current, typed)
                or any(self._known(lang, self.mapper.word(keys, lang))
                       for lang in getattr(self, 'active_languages', LANGS))):
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
        policy = self._sync_policy()
        if policy is not None:
            result = policy.decide(keys, current, allow_rare=allow_rare)
            return Decision(result.typed, result.replacement, result.language,
                            result.reason) if result is not None else None
        active = getattr(self, 'active_languages', LANGS)
        if not keys or len(keys) > 40 or current not in active or current not in self.words:
            return None
        typed = self.mapper.word(keys, current)
        if (len(typed) != len(keys) or not _valid_typed(keys, typed, current,
                                                        getattr(self, 'language_catalog', None))
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
        for lang in active:
            if lang == current:
                continue
            candidate = self.mapper.word(keys, lang)
            if (len(candidate) == len(keys) and _one_script_letters(candidate,
                                                                    getattr(self, 'language_catalog', None))
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
        policy = self._sync_policy()
        if policy is not None:
            return policy.resolve_ambiguity(matches, current, allow_rare=allow_rare)
        active = getattr(self, 'active_languages', LANGS)
        if current not in active:
            return None
        matches = [(lang, word) for lang, word in matches if lang in active]
        # Keep the historical RU/EN/HE priority untouched. A configuration
        # with external packs must never guess between multiple plausible
        # destinations, especially EN/ES sharing the Latin writing system.
        if (getattr(self, 'language_catalog', None) is not None
                and any(lang not in LANGUAGE_BY_CODE for lang in active)
                and len(matches) > 1):
            return None
        if any(self._excluded(lang, word) for lang, word in matches):
            return None
        matches = [(lang, word) for lang, word in matches
                   if allow_rare or not self.conservative or len(word) == 1 or self._confident(lang, word)]
        for language in getattr(self, 'language_priority', ALTERNATIVE_PRIORITY).get(current, ()):
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
