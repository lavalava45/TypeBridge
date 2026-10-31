"""Usable RU/EN/HE keyboard-layout autocorrector for Windows 11.

This is the canonical user-facing prototype.  The large experimental
``prototype.py`` remains available as research evidence, but is not imported
here.  The hook keeps only physical key identities for the unfinished word,
never writes typed words to disk, and ignores injected input.
"""

from __future__ import annotations

# PyInstaller multiprocessing children must divert before importing the GUI.
if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, asdict, replace
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import zipfile
import threading
from time import perf_counter, monotonic
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog
from ui_i18n import (messagebox, tr, localize_window, LocalizedStringVar, ChoiceVar,
                     LANGUAGES, get_language, set_language)

from engine import (LANGS, WORD_AFFIXES, LanguageEngine, candidate_token_key,
                    check_active_layouts_loaded, source_data_dir)
from language_registry import (DEFAULT_ACTIVE_LANGUAGES, LANGUAGE_REGISTRY,
                               validate_active_languages)
from language_pack_loader import available_language_catalog
from language_package_transactions import recover_package_transactions
from settings_language_profile import (legacy_selection, read_language_selection,
                                      read_language_profile,
                                      write_language_selection, sidecar_path,
                                      recover_pending_pair, save_settings_pair,
                                      TRANSACTION_NAME)
from runtime_safety import require_no_original_switcher
from single_instance import exclusive_live_hook
from user_lexicon import UserLexicon, LexiconLoadError, recover_lexicon
from tray_layouts import layout_owner, WindowsLayouts
from direct_hotkeys import DEFAULTS, SUGGESTIONS, MANUAL_KEYS, Chord, compile_bindings
from word_cycle import CYCLE_KEYS, RecentWord, remember_word
from incremental_recognition import prepare_early, cached_early_index, suggest_live_early
from early_scintilla import ScintillaEarlyBridge, replace_prefix
from dictionary_diagnostics import dictionary_report
from diagnostic_writer import DiagnosticWriter
from pipeline_probe import emit_pipeline, decision_failure
try:
    from build_variant import PROFILE as BUILD_PROFILE
except ModuleNotFoundError:
    # Running from checked-out source keeps the original diagnostic workflow.
    # Frozen release builders always embed an explicit, audited profile.
    BUILD_PROFILE = 'diagnostics'

BROWSER_APPS = frozenset({'chrome.exe', 'msedge.exe', 'firefox.exe',
                          'brave.exe', 'opera.exe', 'vivaldi.exe'})
TRACE_APPS = BROWSER_APPS | frozenset({'notepad.exe', 'notepad++.exe'})
from early_native_edit import NativeEditBridge, supports_native_edit
from word_recovery import recover_prefix
from input_transport import send_with_remainder, unmatched_keydowns
from native_transaction_queue import NativeTransactionQueue, PhysicalKey, KEYUP_MESSAGES
from windows_shortcuts import read_windows_shortcuts, open_windows_keyboard_settings
from settings_widgets import ScrollablePage, Tooltip, fit_settings_window
from caps_correction import caps_decision
from startup_registration import read_registration, write_registration, startup_command
from snippets import SnippetConfig, SnippetMatcher
from app_paths import USER_DIR, migrate_user_data
from input_journal import DEFAULT_APPS, JournalStore, JournalService, validate_journal
from quick_search import DEFAULT_ENGINES, QuickSearch, validate_engines
from app_exclusions import PASSWORD_PROCESSES, DEFAULT_CATEGORIES, CATEGORIES, effective_exclusions, validate_categories


BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = (Path(sys.executable).resolve().parent
           if getattr(sys, "frozen", False)
           else Path(__file__).resolve().parent)
LANGUAGE_PACK_DIR = APP_DIR / 'language_packs'
SETTINGS_PATH = USER_DIR / "settings.json"
DEFAULT_SETTINGS_PATH = APP_DIR / "typebridge-settings.json"
DEBUG_ENABLED = (APP_DIR / "TypeBridge-debug.flag").is_file()
DEBUG_WORDS_ENABLED = (BUILD_PROFILE == 'diagnostics' and DEBUG_ENABLED
                       and (APP_DIR / "TypeBridge-debug-words.flag").is_file())

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WM_TOGGLE_PAUSE = 0x8001
WM_RELOAD_CONFIG = 0x8002
WM_RELOAD_SNIPPETS = 0x8003
WM_PASSWORD_STATE = 0x8004
WM_DEBUG_PROBE_PING = 0x8005
WM_RELEASE_KEYBOARD_JOB = 0x8006
WM_INPUTLANGCHANGEREQUEST = 0x0050
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SPACE = 0x20
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_CAPITAL = 0x14
VK_F8 = 0x77
VK_PAUSE = 0x13
MODIFIER_VKS = frozenset((VK_SHIFT, VK_CONTROL, VK_MENU, 0xA0, 0xA1,
                          0xA2, 0xA3, 0xA4, 0xA5))

LLKHF_INJECTED = 0x10
LLKHF_EXTENDED = 0x01
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_UNICODE = 0x0004
SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
GWL_STYLE = -16
ES_PASSWORD = 0x0020
from win32_input import Input, InputUnion, KeyInput, OWN_INPUT_MARKER
BOUNDARY_VKS = frozenset((VK_SPACE, VK_RETURN, VK_TAB))
PUNCTUATION_BOUNDARIES = frozenset(".!?;:…")
MOUSE_BOUNDARIES = frozenset((WM_LBUTTONDOWN, WM_RBUTTONDOWN,
                              WM_MBUTTONDOWN, WM_MOUSEWHEEL,
                              WM_XBUTTONDOWN))


class KbdHook(ctypes.Structure):
    _fields_ = (("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t))


class GuiThreadInfo(ctypes.Structure):
    _fields_ = (("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT))


@dataclass(frozen=True)
class Settings:
    ui_language: str = "ru"
    # Persisted in R16/2b but not enabled in runtime until GUI/HKL/hotkey gates.
    active_languages: tuple[str, ...] = DEFAULT_ACTIVE_LANGUAGES
    corrections_enabled: bool = False
    # R04 staging: a per-profile, explicit opt-in. Applied only at hook startup.
    # False preserves existing alpha behavior for every existing profile.
    experimental_password_guard: bool = False
    conservative: bool = True
    only_window_title: str = ""
    manual_f8: bool = True
    manual_key: str = "Scroll Lock"
    journal_enabled: bool = False
    journal_days: int = 7
    journal_apps: tuple[str, ...] = DEFAULT_APPS
    clipboard_history: bool = False
    clipboard_history_key: str = "Ctrl+Alt+H"
    plain_paste: bool = False
    plain_paste_key: str = "Ctrl+Shift+V"
    quick_search: bool = False
    quick_search_key: str = "Ctrl+Alt+G"
    quick_search_engine: str = "Google"
    quick_search_engines: tuple[tuple[str, str], ...] = DEFAULT_ENGINES
    cycle_key: str = "Pause"
    early_correction: bool = False
    early_native: bool = False
    show_caret_flag: bool = False
    fix_caps_lock: bool = False
    autostart: bool = False
    direct_ru: str = DEFAULTS["ru"]
    direct_en: str = DEFAULTS["en"]
    direct_he: str = DEFAULTS["he"]
    direct_extra: tuple[tuple[str, str], ...] = ()
    # R16/4d source-of-truth when present. Old fields remain projection for
    # legacy settings.json and older TypeBridge EXE compatibility.
    language_shortcuts: tuple[tuple[str, str], ...] = ()
    excluded_categories: tuple[str, ...] = DEFAULT_CATEGORIES
    excluded_processes: tuple[str, ...] = PASSWORD_PROCESSES


@dataclass(frozen=True)
class FocusContext:
    top: int
    focus: int
    thread_id: int
    language: str
    process_name: str
    title: str
    layout_window: int = 0
    password_revision: int = 0


def load_settings(path: Path | None = None, *, catalog=None) -> Settings:
    if path is None:
        # A profile with a surviving language sidecar but a missing legacy
        # settings file is corrupt, not a reason to silently load defaults.
        path = (SETTINGS_PATH if SETTINGS_PATH.exists()
                or (catalog is not None and
                    (sidecar_path(SETTINGS_PATH).exists()
                     or (SETTINGS_PATH.parent / TRANSACTION_NAME).exists()))
                else DEFAULT_SETTINGS_PATH)
    # A crash during the two-file commit must not expose a hybrid legacy/
    # language profile. Successful two-file commits survive the recovery;
    # incomplete writes restore the previous verified pair.
    recover_pending_pair(path)
    if not path.exists():
        if catalog is not None and sidecar_path(path).exists():
            raise ValueError('Legacy settings missing while language sidecar exists')
        return Settings()
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("settings must be a JSON object")
    excluded = raw.get("excluded_processes", list(Settings.excluded_processes))
    if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
        raise ValueError("excluded_processes must be a JSON array of names")
    for name in ("conservative", "manual_f8", "early_correction", "early_native", "show_caret_flag", "experimental_password_guard",
                 "fix_caps_lock", "autostart", "corrections_enabled", "clipboard_history", "plain_paste", "journal_enabled", "quick_search"):
        if name in raw and type(raw[name]) is not bool:
            raise ValueError(f"{name} must be true or false")
    title = raw.get("only_window_title", "")
    cycle_key = raw.get("cycle_key", "Pause")
    if not isinstance(cycle_key, str) or cycle_key not in CYCLE_KEYS:
        raise ValueError("Выбери поддерживаемую клавишу переключения слова")
    if not isinstance(title, str):
        raise ValueError("only_window_title must be text")
    ui_language = raw.get("ui_language", "ru")
    if not isinstance(ui_language, str) or ui_language not in LANGUAGES:
        raise ValueError("Unsupported interface language")
    journal_apps = validate_journal(raw.get("journal_days", 7), raw.get("journal_apps", list(DEFAULT_APPS)))
    categories = validate_categories(raw.get("excluded_categories", list(DEFAULT_CATEGORIES)))
    extra_raw = raw.get('direct_extra', ())
    if (not isinstance(extra_raw, (list, tuple))
            or any(not isinstance(row, (list, tuple)) or len(row) != 2
                   or not all(isinstance(value, str) for value in row) for row in extra_raw)):
        raise ValueError('Invalid additional language shortcuts')
    shortcuts_raw = raw.get('language_shortcuts', ())
    if (not isinstance(shortcuts_raw, (tuple, list))
            or any(not isinstance(pair, (tuple, list)) or len(pair) != 2
                   or not all(isinstance(value, str) for value in pair)
                   for pair in shortcuts_raw)
            or len({pair[0] for pair in shortcuts_raw}) != len(shortcuts_raw)):
        raise ValueError('Invalid universal language shortcuts')
    settings = Settings(ui_language=ui_language,
                    active_languages=validate_active_languages(
                        raw.get("active_languages", list(DEFAULT_ACTIVE_LANGUAGES)), catalog),
                    journal_enabled=raw.get("journal_enabled", False),
                    quick_search=raw.get("quick_search", False),
                    quick_search_key=raw.get("quick_search_key", "Ctrl+Alt+G"),
                    quick_search_engine=raw.get("quick_search_engine", "Google"),
                    quick_search_engines=validate_engines(raw.get("quick_search_engines", DEFAULT_ENGINES), raw.get("quick_search_engine", "Google")),
                    journal_days=raw.get("journal_days", 7), journal_apps=journal_apps,
                    excluded_categories=categories, corrections_enabled=raw.get("corrections_enabled", False),
                    experimental_password_guard=raw.get("experimental_password_guard", False),
                    conservative=raw.get("conservative", True),
                    manual_f8=raw.get("manual_f8", True),
                    manual_key=raw.get("manual_key", "Scroll Lock"),
                    clipboard_history=raw.get("clipboard_history", False),
                    clipboard_history_key=raw.get("clipboard_history_key", "Ctrl+Alt+H"),
                    plain_paste=raw.get("plain_paste", False),
                    plain_paste_key=raw.get("plain_paste_key", "Ctrl+Shift+V"),
                    cycle_key=cycle_key,
                    early_correction=raw.get("early_correction", False),
                    early_native=raw.get("early_native", False),
                    show_caret_flag=raw.get("show_caret_flag", False),
                    fix_caps_lock=raw.get("fix_caps_lock", False),
                    autostart=raw.get("autostart", False),
                    direct_ru=raw.get("direct_ru", DEFAULTS["ru"]),
                    direct_en=raw.get("direct_en", DEFAULTS["en"]),
                    direct_he=raw.get("direct_he", DEFAULTS["he"]),
                    direct_extra=tuple(tuple(row) for row in extra_raw),
                    language_shortcuts=tuple(tuple(row) for row in shortcuts_raw),
                    only_window_title=title.strip(),
                    excluded_processes=tuple(dict.fromkeys(
                        (*Settings.excluded_processes,
                         *(x.strip().casefold() for x in excluded if x.strip())))))
    if catalog is not None:
        selected = read_language_profile(path, catalog)
        if selected is not None:
            generic = selected[2]
            if generic and not settings.language_shortcuts and all(
                    code not in DEFAULTS for code, _ in generic):
                generic = (tuple((code, getattr(settings, f'direct_{code}'))
                                 for code in DEFAULTS) + generic)
            settings = replace(settings, active_languages=selected[0],
                               direct_extra=selected[1],
                               language_shortcuts=(generic
                                   if generic else settings.language_shortcuts))
        if settings.language_shortcuts:
            # The legacy v1 sidecar records only currently active external
            # bindings. Preserve disabled/missing codes in the new main JSON
            # mapping without making older EXEs reject an absent language.
            settings = replace(settings, direct_extra=tuple(
                (code, chord) for code, chord in settings.language_shortcuts
                if code not in DEFAULTS and code in catalog
                and chord.strip().casefold() != 'отключено'))
    compile_bindings(settings, catalog)
    return settings


def staged_password_guard(settings: Settings):
    """Construct opt-in R04 metadata only at startup; no GUI/UIA work here.

    A running hook never hot-swaps guards on a settings save: changing the
    setting requires a complete stop/restart of TypeBridge. The default None
    path preserves old behavior, including all public alpha compatibility.
    """
    if not settings.experimental_password_guard:
        return None
    from password_fields import PasswordFieldGuard
    return PasswordFieldGuard(require_element_id=True)


def save_settings(settings: Settings, path: Path = SETTINGS_PATH, *, catalog=None) -> None:
    # Fail before creating any temporary file or replacing existing settings.
    validate_active_languages(settings.active_languages, catalog)
    if settings.direct_extra and catalog is None:
        raise ValueError('Saving external shortcuts requires a verified language catalog')
    compile_bindings(settings, catalog)
    # Commit the legacy profile first and the full-language sidecar LAST.
    # An unsuccessful legacy save must never activate a new language choice
    # in the next session. The old EXE always sees built-in codes only.
    sidecar = sidecar_path(path)
    update_sidecar = (catalog is not None and
                      (sidecar.exists() or
                       any(code not in DEFAULTS for code in settings.active_languages)
                       or settings.direct_extra or settings.language_shortcuts))
    legacy = replace(settings, active_languages=legacy_selection(settings.active_languages),
                     direct_extra=(), language_shortcuts=())
    path.parent.mkdir(parents=True, exist_ok=True)
    if update_sidecar:
        raw = (json.dumps(asdict(legacy), ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        legacy_document = json.loads(raw)
        if settings.language_shortcuts:
            # Keep language sidecar v1 for older R16 builds. Older EXEs parse
            # the main JSON field-by-field and safely ignore this extra key.
            # The two-file journal still commits generic bindings atomically.
            legacy_document['language_shortcuts'] = [list(row) for row in
                                                     settings.language_shortcuts]
        else:
            legacy_document.pop('language_shortcuts', None)
        raw = (json.dumps(legacy_document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        compatible_extra = (tuple((code, chord) for code, chord in settings.direct_extra
                                  if code in catalog and code in settings.active_languages)
                            if settings.language_shortcuts
                            else settings.direct_extra)
        # Keep the v1 sidecar for already-installed R16 readers. The complete
        # universal mapping is stored in the main JSON under its own key.
        save_settings_pair(path, raw, settings.active_languages,
                           compatible_extra, catalog)
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            document = asdict(legacy)
            if settings.language_shortcuts:
                document['language_shortcuts'] = [list(row) for row in settings.language_shortcuts]
            else:
                document.pop('language_shortcuts', None)
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def load_user_lexicon(folder: Path = USER_DIR, *, catalog=None) -> UserLexicon:
    folder.mkdir(parents=True, exist_ok=True)
    return UserLexicon.load(folder.resolve(), catalog=catalog)


class StartupCancelled(Exception):
    """The user declined recovery; close without a second error dialog."""


def lexicon_error_text(error):
    return '\n'.join((tr('Личный словарь: {v0}').format(v0=error.path),
                      tr('Место ошибки: {v0}').format(v0=error.location),
                      tr('Причина: {v0}').format(v0=tr(error.reason))))


def load_startup_lexicon(root, *, catalog=None):
    """Never silently drop personal words or exclusions after a read failure."""
    try:
        return load_user_lexicon(catalog=catalog) if catalog is not None else load_user_lexicon()
    except LexiconLoadError as error:
        if error.digest is None:
            raise  # Never reset an invalid sidecar or oversized personal file.
        accepted = messagebox.askyesno('Личный словарь',
            lexicon_error_text(error) + '\n\n' + tr('Сохранить полную резервную копию .bad и начать с пустым личным словарём? '
                'Личные слова и исключения не будут действовать до восстановления из копии. '
                'Ответ «Нет» завершит запуск без изменения файла.'), parent=root)
        if not accepted:
            raise StartupCancelled() from error
        lexicon, backup = recover_lexicon(error)
        messagebox.showinfo('Личный словарь', tr('Резервная копия проверена: {v0}').format(v0=backup), parent=root)
        return lexicon


def foreground_language(catalog=None) -> str:
    """Read the active layout independently of correction hooks and exclusions."""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(GuiThreadInfo))
    u.GetGUIThreadInfo.restype = wintypes.BOOL
    u.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
    u.GetKeyboardLayout.restype = wintypes.HANDLE
    u.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    u.GetClassNameW.restype = ctypes.c_int
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return "idle"
    thread = u.GetWindowThreadProcessId(hwnd, None)
    if not thread:
        return "idle"
    info = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
    if u.GetGUIThreadInfo(thread, ctypes.byref(info)) and info.hwndFocus:
        focus_thread = u.GetWindowThreadProcessId(info.hwndFocus, None) or thread
        _, thread = layout_owner(u, hwnd, info.hwndFocus, thread, focus_thread)
    language_id = int(u.GetKeyboardLayout(thread) or 0) & 0xFFFF
    return language_for_langid(language_id, catalog) or 'idle'


def language_for_langid(langid, catalog=None):
    """Pure HKL-to-language lookup; external catalog is strictly opt-in."""
    descriptions = LANGS if catalog is None else {
        code: description.langid for code, description in catalog.items()}
    return next((code for code, value in descriptions.items() if value == langid), '')


def input_key(vk: int, flags: int = 0, scan: int = 0) -> Input:
    return Input(1, InputUnion(ki=KeyInput(
        vk, scan, flags, 0, OWN_INPUT_MARKER)))


def unicode_inputs(text: str) -> list[Input]:
    events: list[Input] = []
    encoded = text.encode("utf-16-le")
    for index in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[index:index + 2], "little")
        events.extend((input_key(0, KEYEVENTF_UNICODE, unit),
                       input_key(0, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                                 unit)))
    return events


def correction_inputs(original_length: int, replacement: str,
                      boundary_vk: int | None = VK_SPACE,
                      boundary_text: str = "") -> list[Input]:
    events: list[Input] = []
    for _ in range(original_length):
        events.extend((input_key(VK_BACK),
                       input_key(VK_BACK, KEYEVENTF_KEYUP)))
    events.extend(unicode_inputs(replacement))
    if boundary_text:
        events.extend(unicode_inputs(boundary_text))
    elif boundary_vk is not None:
        events.extend((input_key(boundary_vk),
                       input_key(boundary_vk, KEYEVENTF_KEYUP)))
    return events


def running_engine_settings(settings: Settings, engine) -> Settings:
    """Validate shortcuts against the STILL-RUNNING language set.

    A saved language selection takes effect after restart, but other settings
    can be reloaded immediately. Never let a reassigned key collide with a
    language which this engine continues to recognize until that restart.
    """
    catalog = getattr(engine, 'language_catalog', None)
    effective = replace(settings,
        active_languages=getattr(engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES),
        direct_extra=settings.direct_extra if catalog is not None else ())
    compile_bindings(effective, catalog)
    return effective


def pending_engine_settings(settings: Settings, active, catalog) -> Settings:
    """Validate shortcuts against the mapper being started or already running.

    In particular, removing HE from the desired set does not release HE's
    shortcut until a new engine is constructed at the next full restart.
    """
    effective = replace(settings, active_languages=tuple(active))
    compile_bindings(effective, catalog)
    return effective


class LiveHook:
    """Small global hook with a single, inspectable correction path."""

    def __init__(self, engine: LanguageEngine, settings: Settings,
                 emit=lambda _kind, _message: None,
                 *, accept_injected: bool = False, snippets: SnippetConfig | None = None,
                 password_guard=None):
        self.engine = engine
        self.settings = settings
        self.emit = emit
        self.accept_injected = accept_injected
        # R04 staged integration. A guard is injected only by an explicitly
        # controlled test harness; the existing GUI/runtime never supplies it.
        # Unknown and stale snapshots are rejected, never queried via UIA in
        # the low-level keyboard callback. Default behavior stays unchanged
        # until independent physical acceptance has proved this mode safe.
        self.password_guard = password_guard
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare_api()
        self.keys: list[tuple[int, bool]] = []
        self.token_blocked = False
        self._backspace_remaining = 0
        self.last_word: RecentWord | None = None
        self.early_done = False
        self.early_disabled = False
        self.native_early_disabled = False
        self.native_edit_disabled = False
        self.native_blocked_scopes = set()
        self.native_edit_attempts = 0
        self.native_queue = NativeTransactionQueue()
        self.native_worker = None
        self.native_pending_recovery = None
        self._native_worker_tls = threading.local()
        self._in_keyboard_callback = False
        self._last_physical_event = None
        self.native_early_keyup_pending = None
        # Prepare before installing the hook. Enabling early mode later must
        # not sort millions of words while keyboard callbacks are live.
        prepare_early(engine)
        self.last_focus = 0
        self.last_language = ""
        self.suppressed_keyups: set[int] = set()
        self.process_cache: dict[int, str] = {}
        self.keyboard_hook = None
        self.mouse_hook = None
        self.keyboard_callback = None
        self.mouse_callback = None
        self.thread_id = 0
        self.stopping = False
        self.paused = False
        self.pending_config = None
        self.snippets = SnippetMatcher(snippets or SnippetConfig())
        self.pending_snippets = None
        self.pipeline_debug = DEBUG_ENABLED
        # Opt-in R04 must not persist plaintext diagnostics while a helper
        # can be stale or miss a same-HWND DOM password transition. Existing
        # files are not deleted; no new word/readback trace is generated here.
        self.debug_words = DEBUG_WORDS_ENABLED and password_guard is None
        self._browser_field_revision = 0
        # The HWND can stay unchanged while the focused browser DOM element
        # changes. A fresh metadata revision must start a new word, even if
        # the process, native focus HWND and keyboard layout all match.
        self._tracked_password_revision = None
        self._guard_skipped_token = False
        self.debug_keyboard_callbacks = 0
        self.debug_keydowns = 0
        self.debug_mouse_callbacks = 0
        self.debug_mouse_max_us = 0
        self.debug_mouse_slow = 0
        self.debug_probe_pongs = 0
        self.debug_callback_errors = 0
        self.debug_keyboard_slow = 0
        self.debug_keyboard_max_ms = 0

    def _language_from_id(self, langid):
        engine = getattr(self, 'engine', None)
        return language_for_langid(
            langid, getattr(engine, 'language_catalog', None))

    def _declare_api(self) -> None:
        u, k = self.user32, self.kernel32
        self.callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u.SetWindowsHookExW.argtypes = (ctypes.c_int, self.callback_type,
                                        wintypes.HINSTANCE, wintypes.DWORD)
        u.SetWindowsHookExW.restype = wintypes.HANDLE
        u.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                     wintypes.WPARAM, wintypes.LPARAM)
        u.CallNextHookEx.restype = ctypes.c_ssize_t
        u.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
        u.UnhookWindowsHookEx.restype = wintypes.BOOL
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = (wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD))
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.IsChild.argtypes = (wintypes.HWND, wintypes.HWND)
        u.IsChild.restype = wintypes.BOOL
        u.GetGUIThreadInfo.argtypes = (wintypes.DWORD,
                                       ctypes.POINTER(GuiThreadInfo))
        u.GetGUIThreadInfo.restype = wintypes.BOOL
        u.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
        u.GetKeyboardLayout.restype = wintypes.HANDLE
        u.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR,
                                     ctypes.c_int)
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR,
                                    ctypes.c_int)
        u.GetClassNameW.restype = ctypes.c_int
        u.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.GetKeyState.argtypes = (ctypes.c_int,)
        u.GetKeyState.restype = ctypes.c_short
        u.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input),
                                ctypes.c_int)
        u.SendInput.restype = wintypes.UINT
        u.SendMessageTimeoutW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_ssize_t,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
        u.SendMessageTimeoutW.restype = ctypes.c_size_t
        u.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                  wintypes.UINT, wintypes.UINT)
        u.GetMessageW.restype = ctypes.c_int
        u.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
        u.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
        u.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM)
        u.PostThreadMessageW.restype = wintypes.BOOL
        u.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT, wintypes.UINT)
        u.PeekMessageW.restype = wintypes.BOOL
        k.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        k.GetModuleHandleW.restype = wintypes.HMODULE
        k.GetCurrentThreadId.restype = wintypes.DWORD
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                  wintypes.DWORD)
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD))
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.GetProcessTimes.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME))
        k.GetProcessTimes.restype = wintypes.BOOL
        k.CloseHandle.argtypes = (wintypes.HANDLE,)

    def pressed(self, vk: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)

    def process_name(self, pid: int) -> str:
        # Never key safety-critical exclusions by PID alone: Windows can reuse
        # a PID for a completely different process (including a password app).
        handle = self.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            self.process_cache.pop(pid, None)
            return ""
        try:
            creation, exit_time = wintypes.FILETIME(), wintypes.FILETIME()
            kernel_time, user_time = wintypes.FILETIME(), wintypes.FILETIME()
            if not self.kernel32.GetProcessTimes(handle, ctypes.byref(creation),
                                                 ctypes.byref(exit_time),
                                                 ctypes.byref(kernel_time),
                                                 ctypes.byref(user_time)):
                self.process_cache.pop(pid, None)
                return ""
            identity = (int(creation.dwHighDateTime), int(creation.dwLowDateTime))
            cached = self.process_cache.get(pid)
            if isinstance(cached, tuple) and cached[0] == identity:
                return cached[1]
            path = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(path))
            if not self.kernel32.QueryFullProcessImageNameW(
                    handle, 0, path, ctypes.byref(size)):
                self.process_cache.pop(pid, None)
                return ""
            name = path.value.rsplit("\\", 1)[-1].casefold()
            self.process_cache[pid] = (identity, name)
            return name
        finally:
            self.kernel32.CloseHandle(handle)

    def context(self) -> FocusContext | None:
        top = int(self.user32.GetForegroundWindow() or 0)
        if not top:
            emit_pipeline(self, 'context-rejected', reason='no-foreground', interval=4)
            return None
        pid = wintypes.DWORD()
        top_thread = int(self.user32.GetWindowThreadProcessId(
            top, ctypes.byref(pid)) or 0)
        if not top_thread or not pid.value or pid.value == os.getpid():
            emit_pipeline(self, 'context-rejected', reason='own-or-invalid-top', interval=4)
            return None
        info = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        focus = top
        if self.user32.GetGUIThreadInfo(top_thread, ctypes.byref(info)):
            focus = int(info.hwndFocus or top)
        focus_pid = wintypes.DWORD()
        focus_thread = int(self.user32.GetWindowThreadProcessId(
            focus, ctypes.byref(focus_pid)) or 0)
        if (not focus_thread or not focus_pid.value or focus_pid.value == os.getpid()
                or (focus != top and not self.user32.IsChild(top, focus))):
            emit_pipeline(self, 'context-rejected', reason='invalid-focus', interval=4)
            return None
        process = self.process_name(focus_pid.value)
        host_process = self.process_name(pid.value)
        excluded = effective_exclusions(self.settings)
        if (not process or not host_process
                or process in excluded or host_process in excluded):
            emit_pipeline(self, 'context-rejected', reason='process-unavailable-or-excluded', interval=4)
            return None
        title_buffer = ctypes.create_unicode_buffer(512)
        self.user32.GetWindowTextW(top, title_buffer, len(title_buffer))
        title = title_buffer.value
        only_title = self.settings.only_window_title.casefold()
        if only_title and only_title not in title.casefold():
            emit_pipeline(self, 'context-rejected', reason='title-filter', interval=4)
            return None
        class_buffer = ctypes.create_unicode_buffer(128)
        self.user32.GetClassNameW(focus, class_buffer, len(class_buffer))
        focus_class = class_buffer.value.casefold()
        # These are navigation/list surfaces, not text editors. Their
        # type-ahead may look like a word to the hook, but injecting Backspace
        # can navigate folders, move selection or trigger other commands.
        # Keep actual EDIT/RichEdit/Scintilla and browser content widgets
        # eligible; this is a targeted exclusion, NOT a universal text-field
        # detector for arbitrary custom controls.
        if (focus_class in ('syslistview32', 'systreeview32', 'shelldll_defview',
                            'directuihwnd', 'toolbarwindow32')
                or (process == 'explorer.exe' and focus_class in
                    ('cabinetwclass', 'explorewclass', 'shell_traywnd',
                     'progman', 'workerw'))):
            emit_pipeline(self, 'context-rejected', reason='non-text-navigation-control', interval=4)
            return None
        if (focus_class in ("edit", "richedit20w", "richedit50w", "richeditd2dpt")
                and self.user32.GetWindowLongPtrW(focus, GWL_STYLE)
                & ES_PASSWORD):
            emit_pipeline(self, 'context-rejected', reason='protected-edit', interval=4)
            return None
        password_revision = 0
        password_guard = getattr(self, 'password_guard', None)
        if password_guard is not None:
            # Metadata comes exclusively from an independent worker. Never
            # block a callback on the UIA provider, and never interpret
            # 'unknown' as permission. The revision is part of every scope
            # so a field change invalidates in-flight replacement jobs.
            password_revision = password_guard.permit_revision(top, focus)
            if password_revision is None:
                emit_pipeline(self, 'context-rejected', reason='password-metadata-unverified', interval=4)
                return None
        owner, owner_thread = layout_owner(self.user32, top, focus, top_thread, focus_thread)
        raw_hkl = int(self.user32.GetKeyboardLayout(owner_thread) or 0) & 0xFFFFFFFF
        language_id = raw_hkl & 0xFFFF
        language = self._language_from_id(language_id)
        if not language:
            emit_pipeline(self, 'context-rejected', reason='unrecognized-hkl', interval=4,
                          hkl=f'{raw_hkl:08X}')
            return None
        emit_pipeline(self, 'context-ready', interval=4, app=process,
                      language=language, hkl=f'{raw_hkl:08X}')
        return FocusContext(top, focus, owner_thread, language, process, title,
                            owner, password_revision=password_revision)

    def clear_token(self, *, preserve_snippets=False) -> None:
        self.keys.clear()
        self.token_blocked = False
        self._backspace_remaining = 0
        self.last_word = None
        self.early_done = False
        self.native_early_deferred = False
        self.native_early_keyup_pending = None
        if not preserve_snippets and getattr(self, "snippets", None):
            self.snippets.reset()

    def _native_blocked(self, context) -> bool:
        if getattr(self, 'native_edit_disabled', False):
            return True
        scopes = getattr(self, 'native_blocked_scopes', set())
        return context is not None and self._word_scope(context) in scopes

    @staticmethod
    def _native_recovery_key_safe(event, drop_trigger):
        if event.trigger and drop_trigger:
            return True
        vk = event.vk
        return (0x30 <= vk <= 0x5A or 0xBA <= vk <= 0xE2 or
                vk in (VK_SPACE, VK_SHIFT, 0xA0, 0xA1)) and not event.flags & 0x20

    def _quarantine_native(self, reason='uncertain-write', context=None) -> None:
        """Do not retry unsafe native mutations; expose the actual reason."""
        scoped = reason == 'input-barrier-lost' and context is not None
        if scoped:
            scopes = getattr(self, 'native_blocked_scopes', None)
            if scopes is None:
                scopes = self.native_blocked_scopes = set()
            target = self._word_scope(context)
            first = target not in scopes and not getattr(self, 'native_edit_disabled', False)
            scopes.add(target)
        else:
            first = not getattr(self, 'native_edit_disabled', False)
            self.native_edit_disabled = True
            self.native_early_disabled = True
        if first:
            cause = {
                'uncertain-write': 'после неопределённого результата записи',
                'input-barrier-lost': 'из-за смены поля или неполной доставки задержанных клавиш',
                'worker-error': 'из-за ошибки рабочего потока',
            }.get(reason, 'из-за ошибки нативной коррекции')
            if scoped:
                message = ('Нативные исправления ограничены для исходного поля '
                           'из-за потери задержанных клавиш. Другие поля доступны. '
                           'Вернись в исходное поле и проверь текст; восстановление '
                           'задержанного ввода автоматически не выполняется.')
            else:
                message = ('Нативные исправления заблокированы ' + cause + '. '
                           'Остальные функции могут работать. Для восстановления закройте '
                           'TypeBridge через «Выход» и запустите заново.')
            self.emit('transport-quarantine', message)

    def _note_native_mutation(self, bridge) -> None:
        # Do not count expensive read-only preflight or declined admissions as
        # mutations: quarantining them would unnecessarily disable correction
        # on the slower PC without any text ever having been changed.
        if getattr(bridge, 'write_started', False) and not self._native_is_off_hook():
            self.native_edit_attempts = getattr(self, 'native_edit_attempts', 0) + 1

    def _trace_word(self, event, context, **details):
        """Queue test-only word content, never write from the keyboard hook."""
        if (not getattr(self, 'debug_words', False) or context is None
                or context.process_name.lower() not in TRACE_APPS):
            return
        self.emit('word-trace', {'event': event, 'app': context.process_name,
                                 'language': context.language, **details})

    def _trace_browser_key(self, context, vk, shift):
        """Debug-only physical input; not a claim about the browser document.

        Called after shortcut filtering. Includes blocked tails so a duplicate
        following an early conversion can be compared with actual key delivery.
        No clipboard, DOM read, UI Automation, file I/O or waiting in the hook.
        """
        if (not getattr(self, 'debug_words', False) or context is None
                or context.process_name.lower() not in BROWSER_APPS):
            return
        try:
            reason = 'token-blocked' if getattr(self, 'token_blocked', False) else 'tracking'
            if vk == VK_BACK:
                self._trace_word('edit-action', context, state='backspace', reason=reason,
                                 tracked=str(len(self.keys)))
                return
            state = {VK_SPACE: 'space', VK_RETURN: 'enter', VK_TAB: 'tab'}.get(vk)
            char = self.engine.mapper.char(vk, context.language, shift)
            if state or (char and char.isprintable()):
                self._trace_word('typed-character', context, source=char or '',
                                 state=state or 'physical-key', reason=reason,
                                 tracked=str(len(self.keys)))
        except Exception:
            pass  # Diagnostic failures must not alter physical input delivery.

    def _queue_browser_readback(self, context, source, replacement, phase, *, revision=None):
        if (not getattr(self, 'debug_words', False) or context is None
                or context.process_name.lower() not in BROWSER_APPS):
            return
        self.emit('browser-readback-request',
                  (self, context, {'source': source, 'replacement': replacement, 'phase': phase},
                   getattr(self, '_browser_field_revision', 0) if revision is None else revision,
                   monotonic()))

    @staticmethod
    def _native_word_excerpt(bridge):
        value = getattr(bridge, 'last_snapshot', None)
        if not value:
            return ''
        document, caret = value
        try:
            return document[:caret].decode('utf-8', 'strict')[-60:]
        except (UnicodeError, TypeError, ValueError):
            return ''

    def _trace_native_before_boundary(self, context):
        """Queue a post-early read; never read the document inside the hook."""
        if (not getattr(self, 'debug_words', False)
                or not getattr(self, 'early_done', False)
                or context.process_name != 'notepad.exe'
                or getattr(self, '_native_observation_pending', False)):
            return
        self._native_observation_pending = True
        self.emit('deferred-native-read', (self, context))

    def _trace_native_after_boundary(self, context):
        """Read in the GUI thread, after physical delimiter handling.

        This observation may contain subsequent typed characters; metadata
        names it as a deferred sample, not an atomic boundary-time snapshot.
        """
        self._native_observation_pending = False
        if (not getattr(self, 'debug_words', False)
                or context.process_name != 'notepad.exe'):
            return
        bridge = None
        try:
            def valid():
                current = self.context()
                return (current is not None and
                        self._word_scope(current) == self._word_scope(context))
            if not valid():
                self._trace_word('post-early-deferred', context, state='read-skipped',
                                 reason='focus-changed-before-read')
                return
            bridge = NativeEditBridge(context, valid, pending_char='')
            with bridge:
                bridge.snapshot()
            self._trace_word('post-early-deferred', context, state='readback-after-boundary',
                             observed=self._native_word_excerpt(bridge))
        except Exception as error:
            self._trace_word('post-early-deferred', context, state='read-failed',
                             reason=type(error).__name__)

    def _send_guarded_inputs(self, events, valid, *, before_send=None):
        """One serial SendInput attempt, one bounded remainder, balanced releases.

        Generic keyboard correction, snippets, and manual cycling all use the
        same protection. A successful SendInput event count does not prove the
        editor's text changed; never report it as verified document content.
        """
        if not valid():
            return 0, 'context-changed-before-send'

        def send(part):
            if before_send is not None:
                # Runs after the context preflight and immediately before
                # the irreversible Windows API. An exception in SendInput or
                # later layout verification must not replay a held trigger.
                before_send()
            batch = (Input * len(part))(*part)
            ctypes.set_last_error(0)
            return int(self.user32.SendInput(len(part), batch, ctypes.sizeof(Input)))

        sent = send_with_remainder(events, send, valid)
        if sent < len(events):
            dangling = unmatched_keydowns(events, sent)
            if dangling:
                releases = [input_key(vk, flags | KEYEVENTF_KEYUP, scan)
                            for vk, scan, flags in reversed(dangling)]
                if send(releases) != len(releases):
                    self.emit('error', 'SendInput: не подтверждено отпускание клавиш')
            return sent, 'partial'
        return sent, 'complete'

    def _physical_event(self, vk, message, event, *, trigger=False):
        return PhysicalKey(int(vk), int(event.scanCode), int(event.flags),
                           int(message), trigger)

    def _schedule_native(self, context, job, *, trigger=None, drop_always=False):
        """Start a native editor transaction off WH_KEYBOARD_LL.

        The hook then only captures physical keys to a bounded queue. On
        completion the worker replays them under the verified target focus.
        `job` returns True iff an original punctuation hotkey was already
        inserted by the native replacement and must not be replayed.
        """
        gate = getattr(self, 'native_queue', None)
        if gate is None:
            gate = self.native_queue = NativeTransactionQueue()
        if getattr(self, '_native_worker_tls', None) is None:
            self._native_worker_tls = threading.local()
        if self.stopping or not gate.begin(
                trigger, target_scope=self._native_scope(context),
                command_trigger=drop_always):
            return False

        def work():
            drop_trigger = bool(drop_always)
            try:
                self._native_worker_tls.active = True
                current = self.context()
                if (not self.stopping and current is not None and
                        self._native_scope(current) == gate.target_scope):
                    drop_trigger = bool(job()) or drop_trigger
                else:
                    gate.abort('focus-changed-before-commit')
                    self.emit('diagnostic', 'native-job-refused:focus-changed')
            except Exception as error:
                self._quarantine_native('worker-error')
                self.emit('diagnostic', 'native-worker-error:' + type(error).__name__)
            finally:
                drop_trigger = drop_trigger or gate.trigger_irreversible
                # Replay in small batches without releasing the barrier in
                # between. Physical input that arrived during replay is next.
                try:
                    self._drain_native_input(context, gate, trigger, drop_trigger)
                except Exception as error:
                    # A worker exception must never leave the low-level hook
                    # swallowing every later key behind a stuck barrier.
                    gate.close()
                    self._quarantine_native('worker-error')
                    self.emit('error', 'Ошибка возврата клавиш: ' + type(error).__name__)
                finally:
                    self._native_worker_tls.active = False

        thread = threading.Thread(target=work, name='TypeBridgeNativeEdit', daemon=True)
        self.native_worker = thread
        try:
            thread.start()
            return True
        except RuntimeError:
            gate.close()
            self.emit('error', 'Не удалось запустить нативную транзакцию')
            return False

    def _schedule_keyboard(self, context, job, *, trigger, drop_always=False,
                           allow_paused=False):
        """Hold the physical event, then send only after the hook has returned.

        A posted message, not a sleep or a reentrant callback flag, acknowledges
        return to the hook thread's message pump. The existing bounded barrier
        holds following input and replays the trigger only if nothing was edited.
        """
        if getattr(self, '_keyboard_job_release', None) is not None:
            return False
        serial = getattr(self, '_keyboard_job_serial', 0) + 1
        self._keyboard_job_serial = serial
        release = threading.Event()
        revision = getattr(self, '_browser_field_revision', 0)
        settings = self.settings
        self._keyboard_job_release = (serial, release)
        if not self.user32.PostThreadMessageW(self.thread_id, WM_RELEASE_KEYBOARD_JOB, serial, 0):
            self._keyboard_job_release = None
            return False

        def released_job():
            try:
                if not release.wait(.5):
                    self.native_queue.abort('keyboard-callback-release-timeout')
                    self.emit('diagnostic', 'keyboard-job-refused:callback-not-released')
                    return False
                current = self.context()
                if (self.stopping or current is None
                        or (self.paused and not allow_paused) or self.settings is not settings
                        or self._word_scope(current) != self._word_scope(context)
                        or current.language != context.language
                        or getattr(self, '_browser_field_revision', 0) != revision):
                    self.native_queue.abort('keyboard-context-changed')
                    self.emit('diagnostic', 'keyboard-job-refused:context-changed')
                    return False
                return job()
            finally:
                pending = getattr(self, '_keyboard_job_release', None)
                if pending is not None and pending[0] == serial:
                    self._keyboard_job_release = None

        started = self._schedule_native(context, released_job, trigger=trigger,
                                        drop_always=drop_always)
        if not started:
            self._keyboard_job_release = None
        else:
            emit_pipeline(self, 'transport', method='keyboard-worker', result='scheduled')
        return started

    def _release_keyboard_job(self, serial):
        pending = getattr(self, '_keyboard_job_release', None)
        if pending is not None and pending[0] == serial:
            self._keyboard_job_release = None
            pending[1].set()

    def _native_is_off_hook(self):
        tls = getattr(self, '_native_worker_tls', None)
        return bool(tls is not None and getattr(tls, 'active', False))

    def _mark_keyboard_trigger_mutation(self):
        """Mark the first possible SendInput mutation before entering Win32."""
        gate = getattr(self, 'native_queue', None)
        if self._native_is_off_hook() and gate is not None:
            gate.mark_trigger_irreversible()

    def _confirm_keyboard_trigger_acceptance(self, sent):
        """Only a definite zero-acceptance response permits trigger replay."""
        gate = getattr(self, 'native_queue', None)
        if not sent and self._native_is_off_hook() and gate is not None:
            gate.reset_unaccepted_trigger()

    def _browser_field_changed_after_submission(self, context, revision):
        """Never switch a browser thread's HKL for a different DOM input."""
        if (context.process_name.lower() not in BROWSER_APPS or
                getattr(self, '_browser_field_revision', 0) == revision):
            return False
        gate = getattr(self, 'native_queue', None)
        if gate is not None and gate.busy:
            gate.abort('field-revision-changed-after-submit')
        self.native_early_disabled = True
        self.clear_token()
        self.token_blocked = True
        self.emit('error', 'Поле браузера изменилось после отправки текста; '
                  'переключение раскладки и досылка клавиш отменены')
        return True

    def _drain_native_input(self, context, gate, trigger, drop_trigger,
                            *, recovery=False):
        lost = False
        original_scope = gate.target_scope or self._native_scope(context)
        trigger_vk = trigger.vk if trigger is not None else None
        had_following_input = False
        cleared_token = False
        attempted_replay = False
        undelivered = []
        # A hotkey is already suppressed by its hook branch; for punctuation
        # prevent a later physical KEYUP from reaching the editor when its
        # KEYDOWN was incorporated in the native replacement.
        if (drop_trigger and trigger_vk is not None and not gate.aborted_reason
                and not recovery):
            self.suppressed_keyups.add(trigger_vk)
        try:
            for _ in range(16):
                if gate.aborted_reason:
                    lost = True
                    break
                held = gate.take()
                if (drop_trigger and trigger_vk is not None and
                        not gate.command_trigger and gate.trigger_repeated):
                    # Physical printable auto-repeat needs its final KEYUP.
                    self.suppressed_keyups.discard(trigger_vk)
                undelivered.extend(held)
                if gate.aborted_reason:
                    lost = True
                    break
                playable = []
                for event in held:
                    if event.trigger and drop_trigger:
                        if event.message in KEYUP_MESSAGES:
                            self.suppressed_keyups.discard(trigger_vk)
                        continue
                    if not event.trigger and event.message not in KEYUP_MESSAGES:
                        had_following_input = True
                    if event.message in KEYUP_MESSAGES and event.vk in self.suppressed_keyups:
                        self.suppressed_keyups.discard(event.vk)
                        continue
                    flags = KEYEVENTF_SCANCODE if event.scan else 0
                    if event.flags & LLKHF_EXTENDED:
                        flags |= KEYEVENTF_EXTENDEDKEY
                    if event.message in KEYUP_MESSAGES:
                        flags |= KEYEVENTF_KEYUP
                    playable.append(input_key(0 if event.scan else event.vk,
                                              flags, event.scan))
                if playable:
                    def valid():
                        current = self.context()
                        return (not gate.aborted_reason and not self.stopping and current is not None and
                                self._native_scope(current) == original_scope)
                    sent, reason = self._send_guarded_inputs(playable, valid)
                    # A pre-send focus refusal accepted NO events. Preserve
                    # the still-intact physical queue for explicit recovery
                    # in its original field, rather than treating the mere
                    # attempt to call SendInput as an irreversible replay.
                    if sent:
                        attempted_replay = True
                    if sent != len(playable):
                        lost = True
                        gate.abort('unsafe-replay-' + reason)
                        self.emit('error', 'Задержанные клавиши не полностью доставлены: ' + reason)
                    else:
                        undelivered.clear()
                        self._observe_snippet_replay(held)
                else:
                    undelivered.clear()
                if had_following_input and not cleared_token:
                    # Clear the old source token BEFORE releasing the barrier.
                    # A new physical KEYDOWN is allowed to enter the hook as
                    # soon as close_if_drained() returns True.
                    self.clear_token(preserve_snippets=True)
                    self.token_blocked = True
                    cleared_token = True
                if gate.aborted_reason:
                    lost = True
                    break
                if gate.close_if_drained():
                    break
            else:
                lost = True
        finally:
            if gate.busy:
                waiting = gate.close()
                undelivered.extend(waiting)
                lost = bool(waiting) or lost
            if gate.overflow:
                lost = True
            if gate.aborted_reason:
                lost = True
            if lost:
                reason = gate.aborted_reason or 'replay-error'
                # Preserve an untouched backlog in volatile memory ONLY if
                # no replay was attempted and focus switched. Never insert
                # it into an unrelated target or silently discard it.
                if (reason in ('focus-changed', 'focus-changed-before-commit',
                               'unsafe-replay-context-changed-before-send')
                        and not attempted_replay and undelivered
                        and getattr(self, 'native_pending_recovery', None) is None
                        and (context.process_name.lower() not in BROWSER_APPS or
                             self._native_scope(context) == original_scope)
                        and all(self._native_recovery_key_safe(e, drop_trigger)
                                for e in undelivered)):
                    self.native_pending_recovery = (context, tuple(undelivered),
                                                    bool(drop_trigger), trigger,
                                                    self.last_language if getattr(self, 'last_language', '')
                                                    in getattr(self.engine, 'active_languages', LANGS)
                                                    else context.language,
                                                    original_scope,
                                                    perf_counter())
                    self.emit('native-recovery-available', len(undelivered))
                # A refused (zero-event) replay, focus change or overflow
                # does not prove the editor's text was mutated. Limit that
                # quarantine to the source field. An uncertain partial replay
                # or unexplained failure still requires a global stop.
                local_failure = (reason in ('focus-changed', 'focus-changed-before-commit',
                                            'field-revision-changed',
                                            'buffer-overflow', 'external-injected-input',
                                            'unsafe-replay-context-changed-before-send'))
                self._quarantine_native('input-barrier-lost' if local_failure
                                        else 'uncertain-write', context if local_failure else None)
                self.emit('error', 'Задержанный ввод не может быть безопасно доставлен: '
                          + reason + ('; исходное поле заблокировано' if local_failure
                                      else '; нативный транспорт заблокирован'))
                if trigger_vk is not None:
                    self.suppressed_keyups.discard(trigger_vk)
            # Replayed injected keystrokes intentionally bypass token parsing.
            # Never resume an old token using an incomplete physical history.
            if lost:
                self.clear_token(preserve_snippets=True)
                self.token_blocked = True

    def expire_native_pending(self):
        """Drop an expired volatile buffer without accessing other windows."""
        pending = getattr(self, 'native_pending_recovery', None)
        if pending is None or perf_counter() - pending[-1] <= 180:
            return False
        self.native_pending_recovery = None
        self.emit('native-recovery-available', 0)
        self.emit('info', 'Время возврата задержанных клавиш истекло')
        return True

    def restore_native_pending(self):
        """Explicit recovery request; replay ONLY in original focused field.

        In-memory physical events are never written to disk or auto-injected
        after a focus switch. The operator must return to the source field and
        select Restore from the tray menu. Never replay during a live edit.
        """
        recovery = getattr(self, 'native_pending_recovery', None)
        if recovery is None or self.stopping or getattr(self, 'native_edit_disabled', False):
            return False
        if len(recovery) == 6:  # Legacy in-memory/test snapshot without DOM revision.
            original, held, drop_trigger, trigger, expected_language, timestamp = recovery
            expected_scope = self._native_scope(original)
        else:
            original, held, drop_trigger, trigger, expected_language, expected_scope, timestamp = recovery
        if perf_counter() - timestamp > 180:
            self.expire_native_pending()
            return False
        current = self.context()
        if (current is None or self._word_scope(current) != self._word_scope(original)
                or current.language != expected_language
                or self._native_scope(current) != expected_scope):
            self.emit('error', 'Для возврата клавиш активируй исходное поле '
                      'и нужную раскладку (' + expected_language.upper() + ')')
            return False
        # Only printable physical typing and optional Shift are recoverable.
        # Never replay Win/Alt/Ctrl shortcuts, Tab/Enter navigation, or
        # arbitrary hotkeys later at a possibly different caret position.
        if not all(self._native_recovery_key_safe(e, drop_trigger) for e in held):
            self.emit('error', 'Отложенные клавиши содержат управляющую комбинацию; '
                      'автоматический возврат небезопасен')
            return False
        if getattr(self, 'native_queue', None) is None:
            return False
        gate = self.native_queue
        # Compensate unmatched physical down events; the corresponding real
        # KEYUP may already have been delivered in another field.
        paired = list(held)
        unmatched = []
        for item in held:
            key = (item.vk, item.scan)
            if item.message in KEYUP_MESSAGES:
                for i in range(len(unmatched)-1, -1, -1):
                    if (unmatched[i].vk, unmatched[i].scan) == key:
                        unmatched.pop(i)
                        break
            else:
                unmatched.append(item)
        paired.extend(PhysicalKey(item.vk, item.scan, 0, WM_KEYUP,
                                  trigger=item.trigger)
                      for item in reversed(unmatched))
        if len(paired) > gate.limit:
            self.emit('error', 'Буфер восстановления превышает безопасный лимит')
            return False
        if not gate.begin(target_scope=expected_scope):
            self.emit('info', 'Нативная операция ещё выполняется; восстановление не начато')
            return False
        self.native_pending_recovery = None
        self.emit('native-recovery-available', 0)
        for item in paired:
            if not gate.capture(item):
                gate.close()
                self.emit('error', 'Буфер восстановления переполнен; возврат отменён')
                return False

        def restore():
            try:
                self._drain_native_input(original, gate, trigger, drop_trigger,
                                         recovery=True)
                if (not getattr(self, 'native_edit_disabled', False)
                        and not gate.aborted_reason and not gate.overflow):
                    getattr(self, 'native_blocked_scopes', set()).discard(
                        self._word_scope(original))
                    self.emit('native-recovery-complete', 'Отложенный ввод отправлен '
                              'в исходное поле; проверь результат и позицию каретки')
            except Exception as error:
                gate.close()
                self._quarantine_native('worker-error')
                self.emit('error', 'Ошибка возврата клавиш: ' + type(error).__name__)

        thread = threading.Thread(target=restore, name='TypeBridgeNativeRecovery', daemon=True)
        self.native_worker = thread
        try:
            thread.start()
            return True
        except RuntimeError:
            gate.close()
            # No physical input was replayed; restore the recoverable snapshot
            # so a failed thread start does not erase the user's last chance.
            self.native_pending_recovery = recovery
            self.emit('native-recovery-available', len(held))
            self.emit('error', 'Не удалось запустить возврат клавиш; ввод сохранён в памяти')
            return False

    def _try_snippet(self, context, vk, shift):
        matcher = getattr(self, "snippets", None)
        if matcher is None:
            return False, False
        if self.paused or not matcher.config.enabled:
            matcher.reset()
            return False, False
        char = {VK_SPACE: ' ', VK_RETURN: '\n', VK_TAB: '\t'}.get(vk)
        if char is None:
            char = self.engine.mapper.char(vk, context.language, shift)
        expansion, pending = matcher.feed(char)
        if expansion is None:
            return False, pending
        # Recheck focus/layout immediately before editing; never expand into a new field.
        current = self.context()
        if (current is None or self._word_scope(current) != self._word_scope(context)
                or current.language != context.language):
            self.clear_token()
            return False, False
        revision = getattr(self, '_browser_field_revision', 0)
        # Feed the trigger exactly once. The worker receives the immutable
        # expansion, not a second attempt to recognize the same physical key.
        self.clear_token()
        matcher.invalidate()
        self.token_blocked = True
        if (getattr(self, '_in_keyboard_callback', False)
                and not self._native_is_off_hook()):
            started = self._schedule_keyboard(
                context,
                lambda: self._expand_snippet(context, expansion, vk, matcher, revision),
                trigger=self._last_physical_event)
            if not started:
                self.emit('diagnostic', 'snippet-worker-unavailable')
            return started, False  # Failure forwards the original key; no inline fallback.
        return self._expand_snippet(context, expansion, vk, matcher, revision), False

    def _expand_snippet(self, context, expansion, vk, matcher, revision):
        """Build and submit the packet off-hook; never re-feed the trigger."""
        if self._native_is_off_hook():
            self._native_worker_tls.snippet_matcher = matcher
        events = correction_inputs(expansion.delete, '', None, '')
        # Text is injected as Unicode packets, not physical VK_RETURN/VK_TAB.
        # This is NOT a guarantee against app actions: a host may interpret
        # Unicode CR (from LF) or TAB as submit/navigation. Preserve existing
        # alpha behavior pending real editor/chat acceptance tests.
        events.extend(unicode_inputs(expansion.text.replace('\n', '\r')))
        if expansion.separator in ('\n', '\t'):
            code = VK_RETURN if expansion.separator == '\n' else VK_TAB
            events.extend((input_key(code), input_key(code, KEYEVENTF_KEYUP)))
        else:
            events.extend(unicode_inputs(expansion.separator))
        def still_valid():
            current = self.context()
            gate = getattr(self, 'native_queue', None)
            return (not self.stopping and not self.paused and current is not None
                    and self._word_scope(current) == self._word_scope(context)
                    and current.language == context.language
                    and getattr(self, '_browser_field_revision', 0) == revision
                    and self.snippets is matcher and matcher.config.enabled
                    and not (gate is not None and gate.busy and gate.aborted_reason))

        sent, reason = self._send_guarded_inputs(
            events, still_valid, before_send=self._mark_keyboard_trigger_mutation)
        self._confirm_keyboard_trigger_acceptance(sent)
        if sent == 0:
            self.emit('diagnostic' if reason == 'context-changed-before-send' else 'error',
                      'snippet-refused:' + reason)
            return False
        self.suppressed_keyups.add(vk)
        if sent != len(events):
            self.emit('error', f'Неполная автозамена: отправлено {sent}/{len(events)}; повтор не выполняется')
        else:
            self.emit('ok', 'Шаблон вставлен')
        return True

    def _observe_snippet_replay(self, held):
        """Retain known whitespace boundaries, without guessing replayed text.

        Replayed OWN_INPUT events bypass recognition. An already delivered
        space must nevertheless allow the *next* physical template to start.
        Unknown queued text stays blocked; never expand it a second time.
        """
        matcher = getattr(getattr(self, '_native_worker_tls', None), 'snippet_matcher', None)
        if matcher is None or matcher is not getattr(self, 'snippets', None):
            return
        downs = [event.vk for event in held if not event.trigger
                 and event.message in (WM_KEYDOWN, WM_SYSKEYDOWN)
                 and event.vk not in MODIFIER_VKS]
        if downs:
            if downs[-1] in (VK_SPACE, VK_RETURN):
                matcher.reset()
            else:
                matcher.invalidate()

    def _try_early(self, context: FocusContext, *, await_native_keyup=False,
                   key_delivered=False) -> bool:
        if (not self.settings.early_correction or self.paused
                or getattr(self, "early_done", False)):
            return False
        native = context.process_name != "notepad++.exe"
        disabled_attribute = "native_early_disabled" if native else "early_disabled"
        if getattr(self, disabled_attribute, False):
            return False
        if native and self._native_blocked(context):
            return False
        if native and getattr(self, 'native_early_deferred', False):
            return False
        if native:
            if not self.settings.early_native:
                return False
            name = ctypes.create_unicode_buffer(128)
            self.user32.GetClassNameW(context.focus, name, len(name))
            keyboard = not supports_native_edit(name.value)
            if not keyboard and self._native_blocked(context):
                return False
        else:
            keyboard = False
        decision = suggest_live_early(self.engine, self.keys, context.language)
        if decision is None:
            if await_native_keyup:
                self.native_early_keyup_pending = None
            return False
        if native and not keyboard and await_native_keyup:
            # Do not manufacture the currently held WM_CHAR. On some modern
            # Notepad RichEdit controls, a late physical character reaches the
            # document after our synchronous transaction and duplicates it.
            # Allow the physical KEYDOWN through, then try after KEYUP, only
            # when the full typed source is independently visible in RichEdit.
            self.native_early_keyup_pending = (
                self.keys[-1][0], tuple(self.keys), self._word_scope(context))
            return False
        if (not native and getattr(self, '_in_keyboard_callback', False)
                and not self._native_is_off_hook()):
            token = tuple(self.keys)
            def released_scintilla():
                if tuple(self.keys) != token:
                    self.emit('diagnostic', 'scintilla-token-changed')
                    return False
                # Same recognition and verified native transaction. The held
                # KEYDOWN has not entered Scintilla; keep the existing prefix
                # span, Undo group and readback protocol unchanged.
                try:
                    return self._try_early(context, key_delivered=key_delivered)
                except Exception:
                    self.early_disabled = True
                    raise
            return self._schedule_keyboard(
                context, released_scintilla, trigger=self._last_physical_event)
        self._trace_word('early-decision', context, source=decision.typed,
                         replacement=decision.replacement, target=decision.language)
        emit_pipeline(self, 'early-found', language=context.language,
                      target=decision.language, length=len(self.keys),
                      method='keyboard' if keyboard else 'native')
        source_hkl = int(self.engine.mapper.layouts[context.language]) & 0xFFFFFFFF
        source_revision = getattr(self, '_browser_field_revision', 0)
        source_settings, source_token = self.settings, tuple(self.keys)
        def valid():
            current = self.context()
            gate = getattr(self, 'native_queue', None)
            return (current is not None and self._word_scope(current) == self._word_scope(context)
                    and current.language == context.language
                    and getattr(self, '_browser_field_revision', 0) == source_revision
                    and int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFFFFFF == source_hkl
                    and (native or (not self.stopping and not self.paused
                         and self.settings is source_settings and tuple(self.keys) == source_token
                         and not (gate is not None and gate.busy and gate.aborted_reason)))
                    and not any(self.pressed(vk) for vk in
                                (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)))
        if keyboard:
            return self._try_keyboard_early(context, decision, valid)
        bridge = None
        try:
            adapter = (NativeEditBridge(context, valid,
                                        pending_char='' if key_delivered else decision.typed[-1],
                                        off_hook=self._native_is_off_hook())
                       if native else ScintillaEarlyBridge(context))
            bridge = adapter
            try:
                with adapter as bridge:
                    outcome = replace_prefix(bridge, decision.typed if key_delivered else decision.typed[:-1],
                                             decision.replacement, valid)
            finally:
                # An adapter exit, diagnostic or HKL error must not replay the
                # held letter after a possible Scintilla edit. Read-only
                # preflight failures leave the original key replayable.
                if not native and getattr(bridge, 'write_started', False):
                    self._mark_keyboard_trigger_mutation()
        except Exception as error:
            emit_pipeline(self, 'transport', method='early-native', result='exception',
                          reason=getattr(bridge, 'failure', '') or type(error).__name__)
            if native:
                self._note_native_mutation(bridge)
                self._trace_native(bridge)
                if not getattr(bridge, 'write_started', False):
                    self.native_early_deferred = True
                    self.emit('diagnostic', 'early-refused:' +
                              (getattr(bridge, 'failure', '') or type(error).__name__))
                    return False
                self._quarantine_native()
                if not key_delivered:
                    self.suppressed_keyups.add(self.keys[-1][0])
                self.clear_token()
                self.token_blocked = True
                self.emit('diagnostic', 'early-uncertain:' +
                          (getattr(bridge, 'failure', '') or type(error).__name__))
                return True  # A native edit may have consumed the held key.
            setattr(self, disabled_attribute, True)
            self.emit("error", f"Ранний режим отключён до перезапуска: {type(error).__name__}")
            if getattr(bridge, 'write_started', False):
                self.clear_token()
                self.token_blocked = True
                return True
            return False
        if native:
            self._note_native_mutation(bridge)
            self._trace_native(bridge)
        if not native and (bridge.timeout or getattr(bridge, "budget_exceeded", False)):
            setattr(self, disabled_attribute, True)
            self.emit("error", "Ранний режим отключён: редактор не ответил вовремя")
        if outcome.state == "refused":
            if native and outcome.reason != 'prefix-not-delivered':
                # An unstable caret/document must not be read again on the next
                # physical letter of the same word. Retry only in a new token.
                self.native_early_deferred = True
            emit_pipeline(self, 'transport', method='early-native', result='refused',
                          reason=outcome.reason)
            self.emit("diagnostic", "early-refused:" +
                      ((getattr(bridge, 'failure', '') if native else '') or outcome.reason))
            self._trace_word('early-result', context, source=decision.typed,
                             replacement=decision.replacement, state='refused',
                             reason=outcome.reason, observed=self._native_word_excerpt(bridge))
            return False  # No document write: forward the triggering key normally.
        if native and not key_delivered:
            self.suppressed_keyups.add(self.keys[-1][0])
        if outcome.state != "verified":
            emit_pipeline(self, 'transport', method='early-native', result=outcome.state,
                          reason=outcome.reason)
            setattr(self, disabled_attribute, True)
            if native:
                self._quarantine_native()
            self.clear_token()
            self.token_blocked = True
            if native:
                self.emit('diagnostic', 'early-uncertain:' +
                          (getattr(bridge, 'failure', '') or outcome.reason))
            self.emit("error", "Ранняя замена не подтверждена; повтор отключён")
            self._trace_word('early-result', context, source=decision.typed,
                             replacement=decision.replacement, state=outcome.state,
                             reason=outcome.reason, observed=self._native_word_excerpt(bridge))
            return True  # Mutation may already include this character; never replay.
        emit_pipeline(self, 'transport', method='early-native', result='verified')
        self._trace_word('early-result', context, source=decision.typed,
                         replacement=decision.replacement, state='verified',
                         observed=self._native_word_excerpt(bridge))
        return self._finish_early_layout(context, decision, disabled_attribute)

    def _try_keyboard_early(self, context, decision, valid):
        if (getattr(self, '_in_keyboard_callback', False)
                and not self._native_is_off_hook()):
            token = tuple(self.keys)
            def send_released_token():
                if tuple(self.keys) != token:
                    self.native_queue.abort('keyboard-token-changed')
                    return False
                return self._try_keyboard_early(context, decision, valid)
            return self._schedule_keyboard(context,
                send_released_token,
                trigger=self._last_physical_event)
        # Use the existing boundary-correction transport, with no delimiter.
        # The current key is still held: remove only the preceding physical keys.
        # No editor API or clipboard. In live callbacks this packet runs only
        # in the worker after the posted release acknowledgement.
        # Worker and mouse callbacks can clear the tracked token in parallel.
        # Do not read keys[-1] or remeasure the deletion span after Win32 I/O.
        token = tuple(self.keys)
        if not token:
            return False
        current_vk = token[-1][0]
        removed = len(token) - 1
        events = correction_inputs(removed, decision.replacement, None, "")
        readback_revision = getattr(self, '_browser_field_revision', 0)
        sent, reason = self._send_guarded_inputs(
            events, valid, before_send=self._mark_keyboard_trigger_mutation)
        self._confirm_keyboard_trigger_acceptance(sent)
        emit_pipeline(self, 'transport', method='early-sendinput',
                      result=reason)
        self._trace_word('early-result', context, source=decision.typed,
                         replacement=decision.replacement, target=decision.language,
                         state='submitted-unverified' if sent == len(events) else 'incomplete',
                         transport='sendinput', reason=reason, sent=str(sent),
                         total=str(len(events)), removed=str(removed))
        if sent != len(events):
            if reason == 'context-changed-before-send':
                return False  # No accepted input: forward the physical key.
            self.emit("error", f"SendInput: отправлено {sent}/{len(events)}")
            if sent == 0:
                self.token_blocked = True
                return False  # Nothing inserted: deliver the held physical key normally.
            self._queue_browser_readback(context, decision.typed, decision.replacement,
                                         'early-partial', revision=readback_revision)
            self.native_early_disabled = True
            self.suppressed_keyups.add(current_vk)
            self.clear_token()
            self.token_blocked = True
            return True  # Partial edit is uncertain; do not repeat or append the held key.
        self.suppressed_keyups.add(current_vk)
        if self._browser_field_changed_after_submission(context, readback_revision):
            return True
        finished = self._finish_early_layout(context, decision, "native_early_disabled",
                                             keyboard=True)
        self._queue_browser_readback(context, decision.typed, decision.replacement,
                                     'early', revision=readback_revision)
        return finished

    def _finish_early_layout(self, context, decision, disabled_attribute, *, keyboard=False):
        hkl = int(self.engine.mapper.layouts[decision.language])
        result = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(context.layout_window or context.focus,
            WM_INPUTLANGCHANGEREQUEST, 0, hkl, SMTO_ABORTIFHUNG | SMTO_BLOCK,
            30, ctypes.byref(result))
        observed = int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFFFFFF
        emit_pipeline(self, 'layout-result', target=decision.language,
                      hkl=f'{hkl & 0xFFFFFFFF:08X}', observed=f'{observed:08X}',
                      result='confirmed' if observed == hkl & 0xFFFFFFFF else 'unconfirmed')
        self.last_language = self._language_from_id(observed & 0xFFFF)
        if observed != hkl & 0xFFFFFFFF:
            setattr(self, disabled_attribute, True)
            self.clear_token()
            self.token_blocked = True
            self.emit("error", "Ранний текст исправлен, но смена раскладки не подтверждена")
        else:
            self.early_done = True
            # SendInput confirms submission, not text readback. Keep that
            # distinction in the status; the direct native paths read back text.
            self.emit("info" if keyboard else "ok", f"До пробела: {context.language.upper()} → {decision.language.upper()}"
                      f" · {len(self.keys)} символов")
        return True

    @staticmethod
    def _word_scope(context: FocusContext) -> tuple:
        return (context.top, context.focus, context.thread_id, context.process_name,
                context.layout_window, context.password_revision)

    def _native_scope(self, context: FocusContext) -> tuple:
        scope = self._word_scope(context)
        if context.process_name.lower() in BROWSER_APPS:
            return scope + (('browser-field-revision',
                             getattr(self, '_browser_field_revision', 0)),)
        return scope

    def _remember(self, context, keys, text, language, suffix, *, whole=False):
        self.last_word = remember_word(keys, text, language, suffix,
                                       self._word_scope(context), whole=whole,
                                       active_languages=getattr(self.engine, 'active_languages', None))

    def _switch_direct(self, context: FocusContext, language: str) -> None:
        if language not in getattr(self.engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES):
            self.emit('info', 'Эта раскладка исключена из активных языков')
            return
        self.clear_token()
        if (getattr(self, '_in_keyboard_callback', False)
                and not self._native_is_off_hook()):
            # Manual layout selection remains available while correction is
            # paused. Wait for the posted callback-release acknowledgement,
            # then use the same bounded focus/replay barrier as other jobs.
            if not self._schedule_keyboard(
                    context, lambda: self._switch_direct(context, language),
                    trigger=self._last_physical_event, drop_always=True,
                    allow_paused=True):
                self.emit('diagnostic', 'direct-layout-worker-unavailable')
            return  # Never fall back to waiting inside the hook.
        try:
            service = WindowsLayouts()
            target = service.capture()
            if target is None or target.top != context.top or target.focus != context.focus:
                raise OSError("Поле ввода изменилось; переключение отменено")
            hkl = self.engine.mapper.layouts.get(language)
            if hkl is None:
                raise OSError("Нужная раскладка не загружена")
            service.request(target, hkl)
            self.last_language = language
            self.emit("ok", f"Раскладка → {language.upper()}")
        except (OSError, ValueError) as error:
            self.emit("error", f"Прямое переключение: {error}")

    def _cycle_word(self, context: FocusContext) -> None:
        if context.language not in getattr(self.engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES):
            self.clear_token()
            self.emit('info', 'Текущая раскладка исключена из активных языков')
            return
        class_name = ctypes.create_unicode_buffer(128)
        self.user32.GetClassNameW(context.focus, class_name, len(class_name))
        native = supports_native_edit(class_name.value)
        scintilla = context.process_name == 'notepad++.exe' and class_name.value == 'Scintilla'
        if native and self._native_blocked(context):
            self.emit('diagnostic', 'native-edit-quarantined:manual-cycle')
            return
        if self.keys and not self.token_blocked:
            text = self.engine.mapper.word(self.keys, context.language)
            match = self.engine.decide(self.keys, context.language, allow_rare=True)
            self._remember(context, self.keys, text, context.language, "",
                           whole=bool(match and match.reason == "unique dictionary match"))
        word = getattr(self, "last_word", None)
        if word is None or word.scope != self._word_scope(context):
            self.clear_token()
            self.emit("info", "Нет последнего слова у курсора для переключения")
            return
        try:
            language, replacement = word.next_variant(
                self.engine.mapper, getattr(self.engine, 'active_languages', None))
        except ValueError as error:
            self.emit("info", str(error))
            return
        if native or scintilla:
            if getattr(self, '_in_keyboard_callback', False):
                trigger = getattr(self, '_last_physical_event', None)
                if self._schedule_native(context,
                        lambda: self._cycle_edit_word(context, word, language,
                                                      replacement, native=native),
                        trigger=trigger, drop_always=True):
                    return
                self.emit('diagnostic', 'manual-cycle-worker-unavailable')
                return
            self._cycle_edit_word(context, word, language, replacement, native=native)
            return
        if (getattr(self, '_in_keyboard_callback', False)
                and not self._native_is_off_hook()):
            # The generic path also performs SendInput and may wait up to
            # 180 ms for the target HKL. Never run either operation inside
            # WH_KEYBOARD_LL. The released-callback worker validates the
            # original focus and drops this command hotkey exactly once.
            trigger = getattr(self, '_last_physical_event', None)
            if self._schedule_keyboard(
                    context,
                    lambda: self._cycle_word(context),
                    trigger=trigger, drop_always=True):
                return
            # Scheduling failed: do NOT fall back to blocking the hook.
            self.emit('diagnostic', 'manual-cycle-keyboard-worker-unavailable')
            return
        events = correction_inputs(len(word.text + word.suffix),
                                   replacement + word.suffix, None, "")
        def still_valid():
            current = self.context()
            return (not self.stopping and current is not None
                    and self._word_scope(current) == self._word_scope(context)
                    and current.language == context.language
                    and getattr(self, 'last_word', None) is word)

        sent, reason = self._send_guarded_inputs(events, still_valid)
        if sent != len(events):
            self.clear_token()
            self.token_blocked = True
            self.emit('diagnostic' if reason == 'context-changed-before-send' else 'error',
                      f'Ручная замена: отправлено {sent}/{len(events)}; {reason}; повтор отменён')
            return
        hkl = self.engine.mapper.layouts[language]
        result = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(context.layout_window or context.focus,
            WM_INPUTLANGCHANGEREQUEST, 0, hkl, SMTO_ABORTIFHUNG | SMTO_BLOCK,
            180, ctypes.byref(result))
        observed = int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFF
        self.last_language = self._language_from_id(observed)
        word.text, word.language = replacement, language
        self.keys.clear()
        # Extending a manually changed unfinished word must not auto-correct
        # only its newly typed tail. A delimiter ends this protection.
        self.token_blocked = not bool(word.suffix)
        self.emit("ok" if self.last_language == language else "error",
                  f"Ручное переключение → {language.upper()}" if self.last_language == language
                  else "Слово переключено, но приложение не подтвердило смену раскладки")

    def _cycle_edit_word(self, context, word, language, replacement, *, native):
        """Verified native manual cycle. Never blindly backspace a RichEdit word.

        Repeated hotkey presses are particularly unsafe when a previous cycle
        left a partial word: compare the actual editor suffix (including spaces)
        before every edit, and update the remembered variant only after readback.
        """
        source_hkl = int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFFFFFF
        def valid():
            current = self.context()
            return (current is not None and self._word_scope(current) == self._word_scope(context)
                    and current.language == context.language
                    and int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFFFFFF == source_hkl
                    and getattr(self, 'last_word', None) is word
                    and not any(self.pressed(vk) for vk in
                                (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)))
        bridge = None
        try:
            bridge = (NativeEditBridge(context, valid, off_hook=self._native_is_off_hook())
                      if native else ScintillaEarlyBridge(context))
            with bridge:
                result = replace_prefix(bridge, word.text + word.suffix,
                                        replacement + word.suffix, valid)
        except Exception as error:
            # No blind fallback: timeout may have modified the document already.
            result = None
            reason = getattr(bridge, 'failure', '') or type(error).__name__
        else:
            reason = getattr(bridge, 'failure', '') or result.reason
        if native:
            self._note_native_mutation(bridge)
            self._trace_native(bridge)
        if result is None or result.state != 'verified':
            uncertain = (result is None and getattr(bridge, 'write_started', False)
                         or result is not None and result.state == 'uncertain')
            self._trace_word('manual-cycle-result', context, source=word.text,
                             replacement=replacement, state='uncertain' if uncertain else 'refused',
                             reason=reason, observed=self._native_word_excerpt(bridge))
            self.clear_token()
            if uncertain:
                self.token_blocked = True
                if native:
                    self._quarantine_native()
                else:
                    self.early_disabled = True
            self.emit('diagnostic', 'manual-cycle-' + ('uncertain:' if uncertain else 'refused:') + reason)
            return
        self._trace_word('manual-cycle-result', context, source=word.text,
                         replacement=replacement, state='verified',
                         observed=self._native_word_excerpt(bridge))
        hkl = int(self.engine.mapper.layouts[language])
        response = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(context.layout_window or context.focus,
            WM_INPUTLANGCHANGEREQUEST, 0, hkl, SMTO_ABORTIFHUNG | SMTO_BLOCK,
            180, ctypes.byref(response))
        observed = int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFF
        self.last_language = self._language_from_id(observed)
        if observed != hkl & 0xFFFF:
            # The text changed but the language did not. Without verified HKL,
            # no additional cycle is allowed on this remembered word.
            self.clear_token()
            self.token_blocked = True
            self.emit('error', 'Ручная замена подтверждена, но раскладка не переключилась')
            return
        word.text, word.language = replacement, language
        self.keys.clear()
        self.token_blocked = not bool(word.suffix)
        self.emit('ok', f'Ручное переключение → {language.upper()}')

    def _boundary(self, vk: int, language: str,
                  shift: bool) -> tuple[int | None, str] | None:
        if vk in BOUNDARY_VKS:
            return vk, ""
        if candidate_token_key(self.engine.mapper, vk, shift, language, bool(self.keys),
                               active_languages=getattr(self.engine, 'active_languages', None),
                               policies=getattr(self.engine, 'language_policies', None)):
            # OEM punctuation may be a letter anywhere, e.g. EN ';' is RU ж.
            # Resolve literal trailing punctuation once the token is complete.
            return None
        character = self.engine.mapper.char(vk, language, shift)
        if len(character) == 1 and character in PUNCTUATION_BOUNDARIES:
            return None, character
        return None

    def _correct(self, context: FocusContext, boundary_vk: int | None,
                 boundary_text: str, *, manual: bool = False) -> bool:
        if context.language not in getattr(self.engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES):
            return False
        if boundary_vk is not None:
            self._trace_native_before_boundary(context)
            if (getattr(self, 'debug_words', False) and getattr(self, 'early_done', False)
                    and context.process_name.lower() in BROWSER_APPS):
                self._queue_browser_readback(context, '',
                    self.engine.mapper.word(list(self.keys), context.language), 'boundary-after-early')
        suffix = " " if boundary_vk == VK_SPACE else boundary_text
        emit_pipeline(self, 'delimiter', length=len(self.keys), language=context.language,
                      separator=('space' if boundary_vk == VK_SPACE else
                                 'enter' if boundary_vk == VK_RETURN else
                                 'tab' if boundary_vk == VK_TAB else
                                 'manual' if manual else 'punctuation'),
                      blocked=bool(getattr(self, 'token_blocked', False)),
                      paused=bool(self.paused), early=bool(getattr(self, 'early_done', False)))
        remember = boundary_vk not in (VK_RETURN, VK_TAB)
        previous = getattr(self, "last_word", None)
        if not self.keys and previous is not None and not manual:
            self.clear_token(preserve_snippets=True)
            if (remember and suffix and len(previous.suffix + suffix) <= 40
                    and previous.scope == self._word_scope(context)):
                previous.suffix += suffix
                self.last_word = previous
            return False
        if getattr(self, "token_blocked", False):
            emit_pipeline(self, 'decision-none', reason='token-blocked')
            self.clear_token(preserve_snippets=True)
            self.token_blocked = manual
            return False
        keys = list(self.keys)
        caps = (caps_decision(self.engine, keys, context.language)
                if (self.settings.fix_caps_lock and not self.paused
                    and not manual and not getattr(self, "early_done", False)
                    and self.user32.GetKeyState(VK_CAPITAL) & 1) else None)
        decision = caps or (self.engine.decide_token(keys, context.language, allow_rare=manual)
                    if manual or (not self.paused and not getattr(self, "early_done", False)) else None)
        if getattr(self, 'debug_words', False) and keys:
            try:
                alternatives = {lang: self.engine.mapper.word(keys, lang)
                                for lang in getattr(self.engine, 'active_languages', LANGS)}
                reason = (decision.reason if decision else
                          decision_failure(self.engine, keys, context.language,
                                           allow_rare=manual))
                self._trace_word('boundary-decision', context,
                                 source=alternatives[context.language], **alternatives,
                                 replacement=decision.replacement if decision else '',
                                 target=decision.language if decision else '',
                                 state='selected' if decision else 'no-decision',
                                 reason=reason)
            except Exception:
                # Diagnostic work must never interfere with physical typing.
                pass
        original_length = len(keys)
        self.clear_token(preserve_snippets=True)
        if decision is None:
            if self.paused and not manual:
                reason = 'paused'
            elif getattr(self, 'early_done', False) and not manual:
                reason = 'already-corrected-early'
            else:
                try:
                    reason = decision_failure(self.engine, keys, context.language,
                                              allow_rare=manual)
                except Exception:
                    reason = 'diagnostic-classifier-failed'
            emit_pipeline(self, 'decision-none', reason=reason, length=len(keys),
                          language=context.language, rare=not self.settings.conservative,
                          manual=manual)
            if remember and keys:
                typed = self.engine.mapper.word(keys, context.language)
                whole = False
                if typed and (not typed[0].isalpha() or not typed[-1].isalpha()):
                    match = self.engine.decide(keys, context.language, allow_rare=True)
                    whole = bool(match and match.reason == "unique dictionary match")
                self._remember(context, keys, typed, context.language, suffix, whole=whole)
            return False
        name = ctypes.create_unicode_buffer(128)
        self.user32.GetClassNameW(context.focus, name, len(name))
        native = supports_native_edit(name.value)
        emit_pipeline(self, 'decision-found', target=decision.language,
                      language=context.language, length=len(keys),
                      method='native' if native else 'generic')
        if native:
            if self._native_blocked(context):
                emit_pipeline(self, 'decision-none', reason='native-edit-quarantined')
                return False
            if getattr(self, '_in_keyboard_callback', False):
                trigger = getattr(self, '_last_physical_event', None)
                return self._schedule_native(
                    context,
                    lambda: self._correct_native(context, decision, keys, boundary_vk,
                        boundary_text, remember, suffix, manual, bool(caps)),
                    trigger=trigger, drop_always=manual)
            return self._correct_native(context, decision, keys, boundary_vk,
                                        boundary_text, remember, suffix, manual, bool(caps))
        if (getattr(self, '_in_keyboard_callback', False)
                and not self._native_is_off_hook()):
            return self._schedule_keyboard(context,
                lambda: self._correct_keyboard(context, decision, keys, boundary_vk,
                    boundary_text, remember, suffix, manual, bool(caps)),
                trigger=self._last_physical_event, drop_always=manual)
        return self._correct_keyboard(context, decision, keys, boundary_vk,
                                      boundary_text, remember, suffix, manual, bool(caps))

    def _correct_keyboard(self, context, decision, keys, boundary_vk, boundary_text,
                          remember, suffix, manual, caps):
        """Generic boundary packet, also used by the released-hook worker."""
        original_length = len(keys)
        events = correction_inputs(original_length, decision.replacement,
                                   boundary_vk, boundary_text)
        if caps:
            events = [input_key(VK_CAPITAL), input_key(VK_CAPITAL, KEYEVENTF_KEYUP)] + events
            # Last-word cycling must preserve the corrected case with CapsLock off.
            keys = [(vk, not shift if 0x41 <= vk <= 0x5A else shift) for vk, shift in keys]
        def target_still_valid():
            current = self.context()
            return (not self.stopping and not self.paused and current is not None
                    and self._word_scope(current) == self._word_scope(context)
                    and current.language == context.language
                    and getattr(self, '_browser_field_revision', 0) == readback_revision)

        readback_revision = getattr(self, '_browser_field_revision', 0)
        sent, reason = self._send_guarded_inputs(
            events, target_still_valid,
            before_send=self._mark_keyboard_trigger_mutation)
        self._confirm_keyboard_trigger_acceptance(sent)
        emit_pipeline(self, 'transport', method='sendinput',
                  result=reason)
        self._trace_word('boundary-result', context, source=decision.typed,
                         replacement=decision.replacement, target=decision.language,
                         state='submitted-unverified' if sent == len(events) else 'incomplete',
                         transport='sendinput', reason=reason, sent=str(sent),
                         total=str(len(events)), removed=str(original_length))
        if sent != len(events):
            if reason == 'context-changed-before-send':
                return False
            self.token_blocked = True
            self.emit("error", f"SendInput: отправлено {sent}/{len(events)}")
            # If even one Backspace was submitted, the physical separator
            # must not be forwarded a second time into a partly edited word.
            # Keep its KEYUP balanced while prohibiting any corrective replay.
            if sent > 0 and boundary_vk is not None:
                self.suppressed_keyups.add(boundary_vk)
            if sent:
                self._queue_browser_readback(context, decision.typed, decision.replacement,
                                             'boundary-partial', revision=readback_revision)
            return sent > 0
        if self._browser_field_changed_after_submission(context, readback_revision):
            return True
        target_hkl = int(self.engine.mapper.layouts[decision.language])
        result = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(
            context.layout_window or context.focus, WM_INPUTLANGCHANGEREQUEST, 0, target_hkl,
            SMTO_ABORTIFHUNG | SMTO_BLOCK, 180, ctypes.byref(result))
        observed_language_id = int(
            self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFF
        changed = observed_language_id == (target_hkl & 0xFFFF)
        emit_pipeline(self, 'layout-result', target=decision.language,
                      observed=f'{observed_language_id:04X}',
                      hkl=f'{target_hkl & 0xFFFFFFFF:08X}',
                      result='confirmed' if changed else 'unconfirmed')
        self.last_language = self._language_from_id(observed_language_id)
        if remember:
            self._remember(context, keys, decision.replacement, decision.language,
                           suffix, whole=decision.reason == "unique dictionary match")
            if manual:
                self.token_blocked = True
        if boundary_vk is not None:
            self.suppressed_keyups.add(boundary_vk)
        if changed:
            self.emit("ok", "Исправлен случайный CapsLock" if caps else
                      f"{context.language.upper()} → {decision.language.upper()}"
                      f" · {original_length} символов")
        else:
            self.emit("error", "Текст исправлен, но приложение не подтвердило смену раскладки")
        self._queue_browser_readback(context, decision.typed, decision.replacement,
                                     'boundary', revision=readback_revision)
        return True

    def _trace_native(self, bridge):
        # Queue metadata only; persistence belongs to the GUI, never the hook.
        if getattr(self, 'pipeline_debug', False):
            trace = getattr(bridge, 'trace', ())
            if trace:
                self.emit('native-trace', tuple(trace))

    def _correct_native(self, context, decision, keys, boundary_vk, boundary_text,
                        remember, suffix, manual, caps):
        if self._native_blocked(context):
            self.emit('diagnostic', 'native-edit-quarantined:boundary')
            return False
        # The word is already delivered; the delimiter/manual hotkey is held.
        # A refusal must never fall back to deleting an unverified character count.
        def valid():
            current = self.context()
            return (not self.stopping and current is not None
                    and self._word_scope(current) == self._word_scope(context)
                    and current.language == context.language)
        target = decision.replacement + (boundary_text if boundary_vk is None else "")
        bridge = None
        try:
            bridge = NativeEditBridge(context, valid, pending_char="",
                                      off_hook=self._native_is_off_hook())
            with bridge:
                outcome = replace_prefix(bridge, decision.typed, target, valid)
        except Exception as error:
            self._note_native_mutation(bridge)
            self._trace_native(bridge)
            emit_pipeline(self, 'transport', method='native', result='exception',
                          reason=type(error).__name__)
            self.emit("diagnostic", "boundary-refused:" +
                      (getattr(bridge, 'failure', '') or type(error).__name__))
            if getattr(bridge, 'write_started', False):
                self._quarantine_native()
            return False
        self._note_native_mutation(bridge)
        self._trace_native(bridge)
        emit_pipeline(self, 'transport', method='native', result=outcome.state,
                      reason=outcome.reason)
        if outcome.state != "verified":
            self._trace_word('boundary-result', context, source=decision.typed,
                             replacement=decision.replacement, state=outcome.state,
                             reason=outcome.reason, observed=self._native_word_excerpt(bridge))
            self.emit("diagnostic", "boundary-" + outcome.state + ":" +
                      (getattr(bridge, 'failure', '') or outcome.reason))
            if outcome.state == "uncertain":
                self._quarantine_native()
                self.token_blocked = True
            return False  # Forward the delimiter; never retry a possibly partial word edit.
        # A verified editor write does not authorize subsequent HKL/CapsLock
        # changes if focus or protected-field metadata changed meanwhile.
        # Do not attempt a speculative Undo on the original document.
        if not valid():
            self.clear_token()
            self.token_blocked = True
            self.emit('diagnostic', 'native-postwrite-layout-refused:context-changed')
            return False
        if caps:
            events = (Input * 2)(input_key(VK_CAPITAL), input_key(VK_CAPITAL, KEYEVENTF_KEYUP))
            self.user32.SendInput(2, events, ctypes.sizeof(Input))
            keys = [(vk, not shift if 0x41 <= vk <= 0x5A else shift) for vk, shift in keys]
        hkl = int(self.engine.mapper.layouts[decision.language])
        result = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(context.layout_window or context.focus,
            WM_INPUTLANGCHANGEREQUEST, 0, hkl, SMTO_ABORTIFHUNG | SMTO_BLOCK,
            30, ctypes.byref(result))
        observed = int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFF
        emit_pipeline(self, 'layout-result', target=decision.language,
                      observed=f'{observed:04X}', hkl=f'{hkl & 0xFFFFFFFF:08X}',
                      result='confirmed' if observed == hkl & 0xFFFF else 'unconfirmed')
        self.last_language = self._language_from_id(observed)
        if remember:
            self._remember(context, keys, decision.replacement, decision.language,
                           suffix, whole=decision.reason == "unique dictionary match")
        self.token_blocked = manual
        self.emit("diagnostic", "boundary-verified:layout-" +
                  ("confirmed" if observed == hkl & 0xFFFF else "unconfirmed"))
        self._trace_word('boundary-result', context, source=decision.typed,
                         replacement=decision.replacement, state='verified',
                         observed=self._native_word_excerpt(bridge))
        # Space/Enter/Tab pass physically, preserving the application's own handling.
        # Literal punctuation was included in the verified range replacement.
        return boundary_vk is None

    def handle_keyboard(self, code, message, param):
        if code < 0:
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        event = ctypes.cast(param, ctypes.POINTER(KbdHook)).contents
        vk = int(event.vkCode)
        if event.dwExtraInfo == OWN_INPUT_MARKER:
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        password_guard = getattr(self, 'password_guard', None)
        if (password_guard is not None and message in (WM_KEYDOWN, WM_SYSKEYDOWN)
                and (vk in (VK_TAB, VK_RETURN, 0x1B, 0x21, 0x22, 0x23, 0x24,
                            0x25, 0x26, 0x27, 0x28)
                     or (vk not in MODIFIER_VKS and any(
                         self.pressed(mod) for mod in
                         (VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))))):
            # Keyboard-only focus navigation can change the DOM text field
            # while its native HWND remains unchanged. Invalidate before a
            # callback may act on the previous element's 'safe' snapshot.
            # Fail-open input DELIVERY (CallNextHookEx), fail-closed edits.
            password_guard.invalidate()
        gate = getattr(self, 'native_queue', None)
        if gate is not None and gate.busy:
            if event.flags & LLKHF_INJECTED:
                # External synthetic input must not be captured and replayed
                # as our physical key. Another input tool may be injecting
                # during the transaction; abandon the barrier instead.
                gate.abort('external-injected-input')
                return self.user32.CallNextHookEx(self.keyboard_hook, code, message, param)
            # The barrier belongs to one specific focused control. If the
            # user clicks another field, do not swallow that field's typing.
            # The worker must not replay already held events into the new
            # field; instead it reports the undeliverable old-field prefix.
            if gate.active:
                if (message in (WM_KEYDOWN, WM_SYSKEYDOWN) and
                        (vk in (VK_TAB, 0x1B, 0x21, 0x22, 0x23, 0x24,
                                0x25, 0x26, 0x27, 0x28,
                                VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN,
                                0xA2, 0xA3, 0xA4, 0xA5))):
                    self._browser_field_revision = getattr(self, '_browser_field_revision', 0) + 1
                    gate.abort('field-revision-changed')
                try:
                    current = self.context()
                    same = (current is not None and
                            (gate.target_scope is None or
                             self._native_scope(current) == gate.target_scope))
                except (OSError, ValueError, AttributeError):
                    same = False
                if not same:
                    gate.abort('focus-changed')
                elif gate.capture(self._physical_event(vk, message, event)):
                    return 1
            # The worker still owns cleanup. Until it finishes, let all
            # subsequent physical input pass through unmodified and do not
            # start any new correction on a different target.
            return self.user32.CallNextHookEx(self.keyboard_hook, code, message, param)
        if (event.flags & LLKHF_INJECTED) and not self.accept_injected:
            self.clear_token()
            emit_pipeline(self, 'input-filtered', reason='injected', interval=5)
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if message in (WM_KEYUP, WM_SYSKEYUP):
            if vk in self.suppressed_keyups:
                self.suppressed_keyups.discard(vk)
                return 1
            candidate = getattr(self, 'native_early_keyup_pending', None)
            if candidate is not None and vk == candidate[0]:
                self.native_early_keyup_pending = None
                if (tuple(self.keys) == candidate[1] and not self.token_blocked
                        and not self.stopping):
                    current = self.context()
                    if (current is not None
                            and self._word_scope(current) == candidate[2]):
                        if (getattr(self, '_in_keyboard_callback', False)
                                and self._schedule_native(current,
                                    lambda: self._try_early(current, key_delivered=True))):
                            pass
                        elif not getattr(self, '_in_keyboard_callback', False):
                            self._try_early(current, key_delivered=True)
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if message not in (WM_KEYDOWN, WM_SYSKEYDOWN):
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if self.stopping:
            self.clear_token()
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        self._last_physical_event = self._physical_event(vk, message, event, trigger=True)
        emit_pipeline(self, 'hook-keydown', interval=5)
        context = self.context()
        if context is None:
            self.clear_token()
            if password_guard is not None:
                self._tracked_password_revision = None
                # A portion of a word was delivered without tracking while
                # metadata was unknown/password. If it later turns safe, the
                # remaining suffix is NOT an independently verified word.
                if vk in (VK_SPACE, VK_RETURN, VK_TAB):
                    self._guard_skipped_token = False
                elif 0x30 <= vk <= 0x5A or 0xBA <= vk <= 0xE2:
                    self._guard_skipped_token = True
            self.last_focus = 0
            self.last_language = ""
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if password_guard is not None:
            revision = context.password_revision
            if revision != getattr(self, '_tracked_password_revision', None):
                # Clear all token/snippet state from the previous UIA element;
                # never append the new field's keystrokes to an old token.
                self.clear_token()
                self.last_focus = 0
                self.last_language = ""
                self._tracked_password_revision = revision
            if getattr(self, '_guard_skipped_token', False):
                # Do not convert a tail as a complete word, or consume manual
                # commands. The first delimiter establishes a new safe token.
                if vk in (VK_SPACE, VK_RETURN, VK_TAB):
                    self._guard_skipped_token = False
                self.clear_token()
                return self.user32.CallNextHookEx(
                    self.keyboard_hook, code, message, param)
        if vk != VK_BACK:
            # Retyping, shortcuts or navigation interrupt a consecutive run
            # of deletes. Never reuse its count for a different document span.
            self._backspace_remaining = 0
        if (vk in (VK_TAB, 0x1B, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
                            VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, 0xA2, 0xA3, 0xA4, 0xA5)
                or any(self.pressed(k) for k in (VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))):
            self._browser_field_revision = getattr(self, '_browser_field_revision', 0) + 1
        if (vk == VK_PAUSE and self.pressed(VK_CONTROL)
                and self.pressed(VK_MENU)):
            # Key-repeat must not toggle the pause state more than once.
            if vk not in self.suppressed_keyups:
                self.toggle_pause()
                self.suppressed_keyups.add(vk)
            return 1
        if (context.focus != self.last_focus
                or context.language != self.last_language):
            if self.keys:
                emit_pipeline(self, 'token-reset', reason=(
                    'focus-changed' if context.focus != self.last_focus else 'language-changed'))
            self.clear_token()
        self.last_focus = context.focus
        self.last_language = context.language
        if vk in MODIFIER_VKS:
            if vk in (VK_CONTROL, VK_MENU, 0xA2, 0xA3, 0xA4, 0xA5):
                self.clear_token()
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if not (self.pressed(VK_LWIN) or self.pressed(VK_RWIN)):
            chord = Chord(vk, frozenset(key for key in (VK_CONTROL, VK_MENU, VK_SHIFT)
                                       if self.pressed(key)))
            if getattr(self, "_binding_settings", None) is not self.settings:
                self._direct_bindings = compile_bindings(
                    self.settings, getattr(self.engine, 'language_catalog', None))
                self._binding_settings = self.settings
            language = self._direct_bindings.get(chord)
            if language:
                if vk not in self.suppressed_keyups:
                    self.suppressed_keyups.add(vk)
                    self._switch_direct(context, language)
                return 1
        if (vk == CYCLE_KEYS[self.settings.cycle_key] and vk != 0
                and not any(self.pressed(key) for key in
                            (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN))):
            if vk not in self.suppressed_keyups:
                if getattr(self, 'snippets', None): self.snippets.reset()
                self.suppressed_keyups.add(vk)
                self._cycle_word(context)
            return 1
        manual_vk = MANUAL_KEYS[self.settings.manual_key]
        if self.paused and vk == manual_vk and manual_vk != 0:
            self.clear_token()
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if (vk == manual_vk and manual_vk != 0 and self.settings.manual_f8
                and not any(self.pressed(key) for key in
                            (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN))):
            if vk in self.suppressed_keyups:
                return 1
            if getattr(self, 'snippets', None): self.snippets.reset()
            if self._correct(context, None, "", manual=True):
                self.suppressed_keyups.add(vk)
                return 1
            self.emit("info", "Однозначное исправление не найдено")
            self.suppressed_keyups.add(vk)
            return 1
        if (self.pressed(VK_CONTROL) or self.pressed(VK_MENU)
                or self.pressed(VK_LWIN) or self.pressed(VK_RWIN)):
            if self.keys:
                emit_pipeline(self, 'token-reset', reason='modifier')
            self.clear_token()
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if context.language not in getattr(self.engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES):
            # We deliberately do not map a disabled or absent Windows layout.
            # Pass its input through unmodified; never consume a physical key.
            self.clear_token(preserve_snippets=True)
            if getattr(self, 'snippets', None):
                self.snippets.invalidate()
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if vk == VK_BACK:
            self._trace_browser_key(context, vk, False)
            if getattr(self, 'snippets', None): self.snippets.invalidate()
            previous = self.last_word
            self.last_word = None
            if self.keys:
                self.keys.pop()
                if not self.keys:
                    # The entire tracked token is gone; its early conversion
                    # must not suppress recognition of the next typed word.
                    self.clear_token(preserve_snippets=True)
            else:
                remaining = getattr(self, '_backspace_remaining', 0)
                if (not remaining and previous is not None
                        and previous.scope == self._word_scope(context)
                        and previous.language == context.language
                        and previous.text.isalpha()
                        and len(previous.text) == len(previous.keys)
                        and all(ord(c) <= 0xFFFF for c in previous.text)
                        and not previous.suffix.strip(' ')
                        and not self.pressed(VK_SHIFT)):
                    remaining = len(previous.text) + len(previous.suffix)
                if remaining:
                    self._backspace_remaining = remaining - 1
                    if self._backspace_remaining:
                        self.token_blocked = True
                    else:
                        self.clear_token(preserve_snippets=True)
                        emit_pipeline(self, 'token-reset', reason='known-word-deleted')
                else:
                    # Unknown text, a moved caret or extra Backspace still
                    # requires a delimiter before automatic correction resumes.
                    self.token_blocked = True
                    emit_pipeline(self, 'token-blocked', reason='backspace-unknown-span', interval=2)
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        shift = self.pressed(VK_SHIFT)
        if 0x41 <= vk <= 0x5A:
            shift ^= bool(self.user32.GetKeyState(VK_CAPITAL) & 1)
        self._trace_browser_key(context, vk, shift)
        expanded, pending_snippet = self._try_snippet(context, vk, shift)
        if expanded:
            return 1
        if pending_snippet:
            emit_pipeline(self, 'token-blocked', reason='snippet-prefix', interval=2)
            # Reserve a recognized shortcut prefix from layout and CapsLock correction.
            self.keys.clear(); self.last_word = None; self.early_done = False
            self.token_blocked = True
            return self.user32.CallNextHookEx(self.keyboard_hook, code, message, param)
        boundary = self._boundary(vk, context.language, shift)
        if boundary is not None:
            if self._correct(context, *boundary):
                return 1
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        self.last_word = None
        if getattr(self, "token_blocked", False):
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if (candidate_token_key(self.engine.mapper, vk, shift,
                                context.language, bool(self.keys),
                                active_languages=getattr(self.engine, 'active_languages', None),
                                policies=getattr(self.engine, 'language_policies', None))
                or self.engine.mapper.char(vk, context.language, shift) in WORD_AFFIXES):
            self.keys.append((vk, shift))
            if len(self.keys) == 1:
                emit_pipeline(self, 'token-start', language=context.language, interval=1)
            if len(self.keys) > 40:
                self.clear_token(preserve_snippets=True)
                self.token_blocked = True
            elif self._try_early(context, await_native_keyup=True):
                return 1
        else:
            if self.keys:
                emit_pipeline(self, 'token-reset', reason='nonword-key')
            self.clear_token(preserve_snippets=True)
            character = self.engine.mapper.char(vk, context.language, shift)
            self.token_blocked = bool(character and character.isprintable())
            if self.token_blocked:
                emit_pipeline(self, 'token-blocked', reason='nonword-key', interval=2)
        return self.user32.CallNextHookEx(
            self.keyboard_hook, code, message, param)

    def handle_mouse(self, code, message, param):
        if code >= 0 and int(message) in MOUSE_BOUNDARIES:
            password_guard = getattr(self, 'password_guard', None)
            if password_guard is not None:
                password_guard.invalidate()
                self._guard_skipped_token = False
            self._browser_field_revision = getattr(self, '_browser_field_revision', 0) + 1
            gate = getattr(self, 'native_queue', None)
            if gate is not None and gate.busy:
                gate.abort('field-revision-changed')
            self.clear_token()
            self.last_focus = 0
            self.last_language = ""
        return self.user32.CallNextHookEx(
            self.mouse_hook, code, message, param)

    def request_stop(self) -> bool:
        self.stopping = True
        if not self.thread_id:
            return False
        return bool(self.user32.PostThreadMessageW(
            self.thread_id, WM_QUIT, 0, 0))

    def request_probe_ping(self) -> bool:
        """Debug-only liveness ping; does not change keyboard state or focus."""
        if not self.pipeline_debug or not self.thread_id:
            return False
        return bool(self.user32.PostThreadMessageW(
            self.thread_id, WM_DEBUG_PROBE_PING, 0, 0))

    def _keyboard_probe_callback(self, code, message, param):
        started = perf_counter()
        self._keyboard_callback_depth = getattr(self, '_keyboard_callback_depth', 0) + 1
        self._in_keyboard_callback = True
        self.debug_keyboard_callbacks += 1
        if code >= 0 and message in (WM_KEYDOWN, WM_SYSKEYDOWN):
            self.debug_keydowns += 1
        try:
            return self.handle_keyboard(code, message, param)
        except Exception as error:
            self.debug_callback_errors += 1
            emit_pipeline(self, 'callback-error', method='keyboard',
                          reason=type(error).__name__)
            return self.user32.CallNextHookEx(self.keyboard_hook, code, message, param)
        finally:
            self._keyboard_callback_depth -= 1
            self._in_keyboard_callback = self._keyboard_callback_depth > 0
            elapsed = int((perf_counter() - started) * 1000)
            self.debug_keyboard_max_ms = max(self.debug_keyboard_max_ms, elapsed)
            if elapsed >= 200:
                self.debug_keyboard_slow += 1
                # Elapsed time is NOT a mutation outcome. It can include
                # CallNextHookEx delays in another hook and worker counters
                # may change independently. Only actual uncertain writes
                # cause native quarantine in their transport handlers.
                emit_pipeline(self, 'slow-keyboard-callback', elapsed_ms=elapsed)

    def _mouse_probe_callback(self, code, message, param):
        started = perf_counter()
        self.debug_mouse_callbacks += 1
        try:
            return self.handle_mouse(code, message, param)
        except Exception as error:
            self.debug_callback_errors += 1
            emit_pipeline(self, 'callback-error', method='mouse',
                          reason=type(error).__name__)
            return self.user32.CallNextHookEx(self.mouse_hook, code, message, param)
        finally:
            # Diagnostic wrapper only. No per-movement I/O or coordinates.
            # This excludes time waiting for the GIL before Python entry.
            elapsed_us = max(0, int((perf_counter() - started) * 1_000_000))
            self.debug_mouse_max_us = max(getattr(self, 'debug_mouse_max_us', 0), elapsed_us)
            if elapsed_us >= 5_000:
                self.debug_mouse_slow = getattr(self, 'debug_mouse_slow', 0) + 1

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.clear_token()
        self.last_focus = 0
        self.emit("paused" if self.paused else "ready",
                  "Автоисправление выключено" if self.paused else "Автоисправление включено")

    def request_pause(self) -> None:
        if self.thread_id:
            self.user32.PostThreadMessageW(self.thread_id, WM_TOGGLE_PAUSE, 0, 0)

    def request_config(self, settings: Settings, lexicon: UserLexicon) -> None:
        # Called by GUI, outside the hook: never sort dictionaries in a callback.
        # A new subset requires a fresh LayoutMapper and hook. Defer only that
        # field until restart; other settings retain their existing reload path.
        runtime_settings = running_engine_settings(settings, self.engine)
        # Guard lifecycle is immutable for a running hook: an opt-in/opt-out
        # saved during a session takes effect only after a full restart.
        runtime_settings = replace(runtime_settings,
            experimental_password_guard=self.password_guard is not None)
        index = cached_early_index(self.engine, lexicon) if settings.early_correction else None
        self.pending_config = (runtime_settings, lexicon, index)
        if self.thread_id:
            self.user32.PostThreadMessageW(self.thread_id, WM_RELOAD_CONFIG, 0, 0)

    def request_snippets(self, config):
        self.pending_snippets = SnippetMatcher(config)
        if self.thread_id:
            self.user32.PostThreadMessageW(self.thread_id, WM_RELOAD_SNIPPETS, 0, 0)

    def run(self) -> None:
        if ctypes.sizeof(Input) != 40:
            raise RuntimeError(f"Win64 INPUT has unexpected size {ctypes.sizeof(Input)}")
        self.thread_id = int(self.kernel32.GetCurrentThreadId())
        message = wintypes.MSG()
        self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        if self.pending_config is not None:
            # Saves made after the GUI published this hook but before its
            # message queue existed must take effect before hook install.
            self.settings, self.engine.user_lexicon, index = self.pending_config
            self.engine._early_prefix_index = index
            self.engine.conservative = self.settings.conservative
            self.pending_config = None
        if self.stopping:
            self.thread_id = 0
            return
        # Measure slow native transactions even without debug flags: Windows
        # can silently remove a low-level keyboard hook after long callbacks.
        self.keyboard_callback = self.callback_type(self._keyboard_probe_callback)
        self.mouse_callback = self.callback_type(
            self._mouse_probe_callback if self.pipeline_debug else self.handle_mouse)
        module = self.kernel32.GetModuleHandleW(None)
        self.keyboard_hook = self.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self.keyboard_callback, module, 0)
        if not self.keyboard_hook:
            raise ctypes.WinError(ctypes.get_last_error())
        self.mouse_hook = self.user32.SetWindowsHookExW(
            WH_MOUSE_LL, self.mouse_callback, module, 0)
        if not self.mouse_hook:
            self.user32.UnhookWindowsHookEx(self.keyboard_hook)
            raise ctypes.WinError(ctypes.get_last_error())
        emit_pipeline(self, 'hook-installed')
        emit_pipeline(self, 'settings', enabled=True, paused=self.paused,
                      rare=not self.settings.conservative,
                      early=self.settings.early_correction,
                      native=self.settings.early_native, words=self.debug_words)
        self.emit("ready", "Автоисправление включено")
        message = wintypes.MSG()
        try:
            password_guard = getattr(self, 'password_guard', None)
            if password_guard is not None:
                password_guard.start()
            while self.user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_RELEASE_KEYBOARD_JOB:
                    self._release_keyboard_job(int(message.wParam))
                    continue
                if message.message == WM_DEBUG_PROBE_PING and self.pipeline_debug:
                    self.debug_probe_pongs += 1
                    continue
                if message.message == WM_TOGGLE_PAUSE:
                    self.toggle_pause()
                    continue
                if message.message == WM_RELOAD_CONFIG and self.pending_config:
                    self.settings, self.engine.user_lexicon, index = self.pending_config
                    self.engine._early_prefix_index = index
                    self.engine.conservative = self.settings.conservative
                    self.pending_config = None
                    self.clear_token()
                    continue
                if message.message == WM_RELOAD_SNIPPETS and self.pending_snippets is not None:
                    self.snippets = self.pending_snippets
                    self.pending_snippets = None
                    self.clear_token()
                    continue
                self.user32.TranslateMessage(ctypes.byref(message))
                self.user32.DispatchMessageW(ctypes.byref(message))
        finally:
            try:
                # Release global callbacks FIRST, even if an experimental
                # metadata worker is hung or its shutdown raises.
                if self.mouse_hook:
                    self.user32.UnhookWindowsHookEx(self.mouse_hook)
                if self.keyboard_hook:
                    self.user32.UnhookWindowsHookEx(self.keyboard_hook)
            finally:
                self.mouse_hook = self.keyboard_hook = None
                self.thread_id = 0
                if getattr(self, 'password_guard', None) is not None:
                    try:
                        self.password_guard.close()
                    except Exception:
                        self.emit('diagnostic', 'password-metadata-helper-close-failed')
                self.emit("stopped", "Автопереключение остановлено")


class TypeBridgeGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        from ui_diagnostics import install_callback_reporting
        install_callback_reporting(root, USER_DIR)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._diagnostic_writer = DiagnosticWriter(
            USER_DIR, debug=DEBUG_ENABLED, words=DEBUG_WORDS_ENABLED,
            diagnostics=BUILD_PROFILE == 'diagnostics')
        self.hook: LiveHook | None = None
        self.worker: threading.Thread | None = None
        self.closing = False
        self.cancel_start = threading.Event()
        # Serialize publication of a new hook with GUI configuration saves.
        self._runtime_config_lock = threading.RLock()
        self._desired_runtime_config = None
        self.tray = None
        self.caret_indicator = None
        self.caret_indicator_error = ""
        self.last_tray_language = None
        self._window_icon_language = None
        self.run_state = "stopped"
        self.last_start_error = ""
        self.native_quarantine_message = ""
        self._hook_watchdog = None
        self._raw_probe = None
        self._control_probe = None
        self._watchdog_idle_logged = False
        self._comparison_captured = False
        self._browser_readback = None
        if DEBUG_WORDS_ENABLED:
            from browser_readback import BrowserReadback
            self._browser_readback = BrowserReadback(
                lambda result: self.events.put(('browser-readback-result', result)))
            self._browser_readback.start()
        if DEBUG_ENABLED and BUILD_PROFILE == 'diagnostics':
            from hook_watchdog import HookWatchdog
            from raw_input_probe import RawKeyboardProbe
            from keyboard_control_probe import ControlKeyboardProbe
            from dictionary_diagnostics import record_debug_event
            self._raw_probe = RawKeyboardProbe()
            self._control_probe = ControlKeyboardProbe()
            self._hook_watchdog = HookWatchdog(
                lambda kind, event: record_debug_event(USER_DIR, kind, event),
                raw_probe=self._raw_probe, control_probe=self._control_probe)
        # Repair an interrupted offline package swap before the catalog (or
        # any selected package bytes) can be consumed by this new instance.
        if LANGUAGE_PACK_DIR.exists():
            recover_package_transactions(LANGUAGE_PACK_DIR, SETTINGS_PATH)
        invalid_packs = {}
        self.language_catalog = available_language_catalog(
            LANGUAGE_PACK_DIR, tolerant=True, errors=invalid_packs)
        settings = load_settings(catalog=self.language_catalog)
        for pack, reason in invalid_packs.items():
            self.emit('error', f'Пакет {pack} отключён: {reason}')
        # Indicator configuration is immutable until a *full restart*;
        # saving a different language set cannot change the running hook.
        self.display_languages = tuple(settings.active_languages)
        self.launch_languages = self.display_languages
        set_language(settings.ui_language)
        self.ui_language = tk.StringVar(value=LANGUAGES[settings.ui_language])
        self.lexicon = load_startup_lexicon(root, catalog=self.language_catalog)
        self.snippet_config = SnippetConfig.load(USER_DIR / 'snippets.json')
        self.snippets_editor = None
        self.dictionary_import_window = None
        self.journal_window = None
        self.search_window = None
        self.search_settings = settings
        self.active_language_vars = {
            description.code: tk.BooleanVar(value=description.code in settings.active_languages)
            for description in self.language_catalog.values()
        }
        self.quick_search = QuickSearch(lambda result: self.events.put(("quick-search-result", result)),
                                       copy_guard=lambda: self.clipboard_tools.selection_copy())
        self.journal_enabled = tk.BooleanVar(value=settings.journal_enabled)
        self.journal_days = tk.StringVar(value=str(settings.journal_days))
        self.journal_apps = tk.StringVar(value=", ".join(settings.journal_apps))
        self.journal_status = LocalizedStringVar(value="Дневник включён" if settings.journal_enabled else "Дневник выключен")
        self.journal_service = JournalService(JournalStore(USER_DIR / 'journal' / 'journal.sqlite3'),
                                            lambda message: self.events.put(("journal", message)))
        from journal_io import JournalIo
        self.journal_io = JournalIo(self.journal_service,
                                    lambda message: self.events.put(('journal-io', message)))
        self._configure_journal(settings)
        self.clipboard_window = None
        from clipboard_tools import ClipboardTools
        self.clipboard_tools = ClipboardTools(root, lambda message: self.events.put(("clipboard", message)))
        self.clipboard_tools.configure(settings)
        root.title("TypeBridge — " + " / ".join(code.upper() for code in settings.active_languages))
        # The window close button minimizes to a recoverable taskbar entry;
        # only the explicit Exit command terminates the background switcher.
        root.protocol("WM_DELETE_WINDOW", self.hide)
        self.status = LocalizedStringVar(value="Остановлен")
        self.conservative = tk.BooleanVar(value=settings.conservative)
        self.manual_f8 = tk.BooleanVar(value=settings.manual_f8)
        self.manual_key = ChoiceVar(value=settings.manual_key)
        self.clipboard_history = tk.BooleanVar(value=settings.clipboard_history)
        self.clipboard_history_key = ChoiceVar(value=settings.clipboard_history_key)
        self.plain_paste = tk.BooleanVar(value=settings.plain_paste)
        self.plain_paste_key = ChoiceVar(value=settings.plain_paste_key)
        self.cycle_key = ChoiceVar(value=settings.cycle_key)
        self._init_early_options(settings)
        self.show_caret_flag = tk.BooleanVar(value=settings.show_caret_flag)
        self.fix_caps_lock = tk.BooleanVar(value=settings.fix_caps_lock)
        self.autostart = tk.BooleanVar(value=read_registration() is not None)
        self.corrections_enabled = tk.BooleanVar(value=settings.corrections_enabled)
        self.experimental_password_guard = tk.BooleanVar(
            value=settings.experimental_password_guard)
        additional_keys = dict(settings.direct_extra)
        generic_keys = dict(settings.language_shortcuts)
        self.direct_keys = {
            lang: ChoiceVar(value=generic_keys.get(lang,
                getattr(settings, f'direct_{lang}') if lang in DEFAULTS
                else additional_keys.get(lang, 'Отключено')))
            for lang in self.language_catalog}
        self.windows_language_shortcut = LocalizedStringVar()
        self.windows_layout_shortcut = LocalizedStringVar()
        self.only_title = tk.StringVar(value=settings.only_window_title)
        self.excluded = tk.StringVar(value=", ".join(
            name for name in settings.excluded_processes if name not in PASSWORD_PROCESSES))
        self.excluded_categories = {name: tk.BooleanVar(value=name in settings.excluded_categories)
                                    for name in CATEGORIES}
        self.exclusions_window = None
        self._build()
        root._i18n_refresh = self._translate_word_rows
        localize_window(root)
        fit_settings_window(root, self.settings_pages)
        self.refresh_windows_shortcuts()
        root.bind("<FocusIn>", self.refresh_windows_shortcuts, add="+")
        root.after(100, self._drain)
        self._schedule_debug_probes(root)
        root.after(150, self._start_tray)
        root.after(500, self._update_tray)
        root.after(200, lambda: self._apply_caret_indicator(self.show_caret_flag.get()))
        if settings.corrections_enabled:
            root.after(300, self._restore_corrections)

    def _schedule_debug_probes(self, root):
        """Never schedule unavailable Diagnostics-only probes in Public.

        A debug.flag file may be present beside both profile executables.
        Scheduling callbacks in Public must not dereference absent probes.
        """
        if DEBUG_ENABLED and BUILD_PROFILE == 'diagnostics':
            root.after(800, self._raw_probe.start)
            root.after(1100, self._control_probe.start)
            root.after(4000, self._poll_hook_watchdog)

    def _make_rare_words_option(self, parent):
        # Keep the stored conservative flag and existing profiles unchanged.
        return ttk.Checkbutton(parent, text="Исправлять редкие слова автоматически",
                               variable=self.conservative, onvalue=False, offvalue=True)

    def _init_early_options(self, settings):
        self.early_correction = tk.BooleanVar(self.root, value=settings.early_correction)
        # One UI choice drives both legacy runtime fields; master was authoritative.
        self.early_native = self.early_correction

    def _make_early_option(self, parent):
        return ttk.Checkbutton(parent, text="Исправлять до пробела", variable=self.early_correction)

    def refresh_windows_shortcuts(self, _event=None) -> None:
        language, layout = read_windows_shortcuts()
        self.windows_language_shortcut.set(language)
        self.windows_layout_shortcut.set(layout)
        values = (language, layout)
        if values != getattr(self, '_last_windows_shortcuts', None):
            self._last_windows_shortcuts = values
            if DEBUG_ENABLED:
                self.emit('probe', f'windows-shortcuts:language={language};layout={layout}')

    def open_windows_shortcuts(self) -> None:
        try:
            open_windows_keyboard_settings()
        except OSError as exc:
            messagebox.showerror("TypeBridge", f"{tr('Не удалось открыть настройки Windows')}: {exc}")

    def _build(self) -> None:
        footer = ttk.Frame(self.root, padding=(12, 8))
        footer.pack(side='bottom', fill='x')
        self.save_button = ttk.Button(footer, text="Сохранить настройки", command=self.save)
        self.save_button.pack(side='left')
        ttk.Button(footer, text="Свернуть на панель задач", command=self.hide).pack(side="left", padx=8)
        ttk.Button(footer, text="Автозамена и шаблоны…", command=self.open_snippets).pack(side="left")
        ttk.Button(footer, text="Выход", command=self.close).pack(side="right")
        language_bar = ttk.Frame(self.root)
        language_bar.pack(fill="x", padx=14, pady=(10, 0))
        ttk.Label(language_bar, text="Язык интерфейса:").pack(side="left")
        language_picker = ttk.Combobox(language_bar, textvariable=self.ui_language,
                                      values=tuple(LANGUAGES.values()), state="readonly", width=16)
        language_picker.pack(side="left", padx=8)
        language_picker.bind("<<ComboboxSelected>>", self.change_ui_language)
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        self.settings_pages = []
        def page(title):
            tab = ScrollablePage(notebook)
            self.settings_pages.append(tab)
            notebook.add(tab, text=title)
            return tab.body
        frame = page("Переключатель")
        ttk.Label(frame, text="Автопереключатель TypeBridge",
                  font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.status,
                  font=("Segoe UI", 11)).pack(anchor="w", pady=(4, 14))
        languages_frame = ttk.LabelFrame(frame, text="Языки автопереключения", padding=8)
        languages_frame.pack(fill="x", pady=(0, 10))
        # Vertical scrolling belongs to the existing ScrollablePage; unlike a
        # single horizontal row this can show an unbounded installed catalog.
        for index, language in enumerate(self.language_catalog.values()):
            ttk.Checkbutton(languages_frame, text=language.name,
                            variable=self.active_language_vars[language.code]).grid(
                                row=index // 3, column=index % 3,
                                sticky='w', padx=(0, 16), pady=3)
        ttk.Button(languages_frame, text='Установить языковой пакет ZIP…',
                   command=self.install_language_package).grid(
                       row=(len(self.language_catalog) + 2) // 3,
                       column=0, columnspan=3, sticky='w', pady=(8, 0))
        actions = ttk.Frame(languages_frame)
        actions.grid(row=(len(self.language_catalog) + 2) // 3 + 1,
                     column=0, columnspan=3, sticky='w', pady=(5, 0))
        ttk.Button(actions, text='Обновить пакет ZIP…',
                   command=self.update_language_package).pack(side='left')
        ttk.Button(actions, text='Удалить отключённый пакет…',
                   command=self.remove_language_package).pack(side='left', padx=6)
        ttk.Button(actions, text='Восстановить пакет…',
                   command=self.restore_language_package).pack(side='left')
        ttk.Label(frame, text="Для применения нового набора языков полностью выйди из TypeBridge и запусти его снова.",
                  wraplength=620).pack(anchor="w", pady=(0, 8))
        self.corrections_check = ttk.Checkbutton(
            frame, text="Автоматически переключать раскладку", variable=self.corrections_enabled,
            command=self._on_corrections_check)
        self.corrections_check.pack(anchor="w", pady=(0, 10))
        Tooltip(self.corrections_check, "Исправлять слова, набранные в неправильной раскладке. Определяется после пробела или другого разделителя")
        rare_option = self._make_rare_words_option(frame)
        rare_option.pack(anchor="w")
        Tooltip(rare_option, "Включено: весь словарь по пробелу; до пробела дополнительно исправляются "
                "однозначные полные редкие слова от 5 букв, но не их незаконченные префиксы. "
                "Выключено: только уверенные совпадения. Редкие слова увеличивают риск неверного выбора языка.")
        early_option = self._make_early_option(frame)
        early_option.pack(anchor="w", pady=(6, 0))
        Tooltip(early_option, "Экспериментальный режим: Не ждать пробела или другого разделителя. Исправление происходит, когда набранных букв достаточно для выбора слова.")
        guard_option = ttk.Checkbutton(
            frame, text="Экспериментальная проверка парольных полей (R04, после перезапуска)",
            variable=self.experimental_password_guard)
        guard_option.pack(anchor="w", pady=(6, 0))
        Tooltip(guard_option,
                "По умолчанию отключено. При включении неизвестные поля не исправляются; "
                "проверка UI Automation выполняется в отдельном процессе. "
                "Изменение действует только после полного выхода из TypeBridge и нового запуска. "
                "Защита браузерных паролей ещё НЕ прошла физическую приёмку.")
        ttk.Checkbutton(frame, text="Исправлять случайный CapsLock: пРИВЕТ → Привет",
                        variable=self.fix_caps_lock).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(frame, text="Автозапуск в трее",
                        variable=self.autostart).pack(anchor="w", pady=(6, 0))
        cycle_row = ttk.Frame(frame)
        cycle_row.pack(fill="x", pady=(6, 0))
        ttk.Label(cycle_row, text="Перебрать раскладки последнего слова:").pack(side="left")
        ttk.Combobox(cycle_row, textvariable=self.cycle_key, state="readonly",
                     values=tuple(CYCLE_KEYS), width=15).pack(side="left", padx=10)
        title_row = ttk.Frame(frame)
        title_row.pack(fill="x", pady=(12, 4))
        ttk.Label(title_row, text="Переключать только в окнах с заголовками:").pack(side="left")
        title_entry = ttk.Entry(title_row, textvariable=self.only_title)
        title_entry.pack(side="left", fill="x", expand=True, padx=(10, 0))
        Tooltip(title_entry, "Укажи один заголовок или его часть — правило действует во всех совпавших окнах. "
                "Пустое поле — все приложения. Исключения настраиваются отдельно.")
        tools = ttk.Frame(frame)
        tools.pack(fill="x", pady=(10, 2))
        ttk.Button(tools, text="Исключения приложений…", command=self.open_exclusions).pack(side="left")
        ttk.Button(tools, text="Дневник ввода…", command=self.open_journal).pack(side="left", padx=8)
        ttk.Button(tools, text="Быстрый поиск…", command=self.open_search).pack(side="left")
        ttk.Label(frame, textvariable=self.journal_status, foreground="#a12b20").pack(anchor="w")
        ttk.Label(frame, text="События (слова не записываются):").pack(anchor="w")
        self.log = tk.Text(frame, height=6, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(5, 0))
        dictionary = page("Мои слова и исключения")
        self._build_dictionary(dictionary)
        shortcuts = page("Горячие клавиши")
        manual_row = ttk.Frame(shortcuts)
        manual_row.pack(fill="x", pady=(6, 0))
        manual_option = ttk.Checkbutton(manual_row, text="Ручное исправление слова до пробела",
                                        variable=self.manual_f8)
        manual_option.pack(side="left")
        Tooltip(manual_option, "Исправить набираемое слово по выбранной горячей клавише, не дожидаясь пробела.")
        ttk.Combobox(manual_row, textvariable=self.manual_key, state="readonly",
                     values=tuple(MANUAL_KEYS), width=15).pack(side="left", padx=10)
        ttk.Label(shortcuts, text="Установить раскладку без изменения текста",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 14))
        for lang, description in self.language_catalog.items():
            row = ttk.Frame(shortcuts)
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=description.name, width=16).pack(side="left")
            ttk.Combobox(row, textvariable=self.direct_keys[lang],
                         values=SUGGESTIONS, width=25).pack(side="left")
        ttk.Label(shortcuts, text="Выбери сочетание или введи своё, например Ctrl+Alt+1.\n"
                  "Для отключения выбери «Отключено». Команды работают при запущенном\n"
                  "переключателе, в том числе когда автоисправление отключено.",
                  wraplength=620).pack(anchor="w", pady=14)
        system_keys = ttk.LabelFrame(shortcuts, text="Системные сочетания Windows", padding=12)
        system_keys.pack(fill="x", pady=(20, 0))
        ttk.Label(system_keys, text="Win+Space — стандартное переключение раскладки",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        for title, variable in (
            ("Между языками:", self.windows_language_shortcut),
            ("Между раскладками одного языка:", self.windows_layout_shortcut),
        ):
            row = ttk.Frame(system_keys)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=title).pack(side="left")
            ttk.Label(row, textvariable=variable, wraplength=330, justify="right").pack(side="right")
        ttk.Button(system_keys, text="Изменить в Windows…",
                   command=self.open_windows_shortcuts).pack(anchor="w", pady=(8, 4))
        ttk.Label(system_keys, text="В Windows: дополнительные параметры клавиатуры → сочетания клавиш для языков ввода.",
                  wraplength=580).pack(anchor="w")
        clipboard = page("Буфер обмена")
        for text, enabled, hotkey in (
            ("Сохранять историю буфера обмена", self.clipboard_history, self.clipboard_history_key),
            ("Вставлять без форматирования", self.plain_paste, self.plain_paste_key),
        ):
            ttk.Checkbutton(clipboard, text=text, variable=enabled).pack(anchor="w", pady=(12, 4))
            row = ttk.Frame(clipboard)
            row.pack(anchor="w")
            ttk.Label(row, text="Горячая клавиша:").pack(side="left")
            ttk.Combobox(row, textvariable=hotkey, width=25,
                         values=("Отключено", "Ctrl+Alt+H", "Ctrl+Shift+V",
                                 *(key for key in SUGGESTIONS[1:] if not key.endswith("F12")))).pack(side="left", padx=10)
        ttk.Label(clipboard, text="Функции буфера работают независимо от автоисправления.\n"
                  "История: 30 текстовых фрагментов, до 100 000 символов каждый, только в памяти. "
                  "Выключение истории и выход из приложения очищают список.\n"
                  "Вставка без форматирования заменяет содержимое буфера чистым текстом. "
                  "Исходные стили в буфере не сохраняются. Обычный Ctrl+V не изменяется.\n"
                  "Не копируй секреты при включённой истории: не все приложения помечают их как приватные.",
                  wraplength=620).pack(anchor="w", pady=16)
        ttk.Button(clipboard, text="Открыть историю…", command=self.open_clipboard_history).pack(anchor="w", pady=6)
        from privacy_ui import build_privacy_tab
        build_privacy_tab(notebook, frame=page("Приватность"))
        ttk.Button(clipboard, text="Очистить историю", command=self.clipboard_tools.clear_history).pack(anchor="w", pady=6)
        indication = page("Индикация")
        ttk.Checkbutton(indication, text="Показывать флажок раскладки рядом с текстовым курсором",
                        variable=self.show_caret_flag).pack(anchor="w", pady=(0, 12))
        ttk.Label(indication, text="Флажок показывает RU / EN / HE в активном поле ввода.\n"
                  "Не забирает фокус и пропускает клики. Работает независимо\n"
                  "от автоисправления. Если поле не сообщает положение каретки,\n"
                  "индикатор скрывается.", wraplength=620).pack(anchor="w", pady=(0, 14))

    def change_ui_language(self, event=None):
        selected = next((code for code, name in LANGUAGES.items() if name == self.ui_language.get()), None)
        if selected is None:
            return
        try:
            catalog = getattr(self, 'language_catalog', None)
            updated = replace(load_settings(catalog=catalog), ui_language=selected)
            if catalog is None:
                save_settings(updated)
            else:
                save_settings(updated, SETTINGS_PATH, catalog=catalog)
        except (ValueError, OSError) as error:
            self.ui_language.set(LANGUAGES[get_language()])
            messagebox.showerror("Настройки", f"Не удалось сохранить язык интерфейса: {error}", parent=self.root)
            return
        set_language(selected)
        if self.tray:
            self.tray.refresh_ui()

    def _apply_caret_indicator(self, enabled: bool) -> None:
        if self.closing:
            return
        if enabled and (self.caret_indicator is None or self.caret_indicator.closed.is_set()):
            from caret_indicator import CaretFlagIndicator
            catalog = getattr(self, 'language_catalog', None)
            if catalog is None:
                self.caret_indicator = CaretFlagIndicator()
            else:
                self.caret_indicator = CaretFlagIndicator({
                    code: catalog[code].langid
                    for code in getattr(self, 'display_languages', DEFAULT_ACTIVE_LANGUAGES)})
            self.caret_indicator_error = ""
            self.caret_indicator.start()
        if self.caret_indicator is not None:
            self.caret_indicator.set_enabled(enabled)
        if getattr(self, "tray", None):
            self.tray.set_caret_flag(enabled)

    def toggle_caret_flag(self) -> None:
        current = self.tray.caret_flag if self.tray else self.show_caret_flag.get()
        enabled = not current
        try:
            # Change only this saved setting; leave unsaved GUI edits alone.
            catalog = getattr(self, 'language_catalog', None)
            updated = replace(load_settings(catalog=catalog), show_caret_flag=enabled)
            if catalog is None:
                save_settings(updated)
            else:
                save_settings(updated, SETTINGS_PATH, catalog=catalog)
        except (ValueError, OSError) as error:
            self.emit("error", f"Не удалось сохранить настройку флажка: {error}")
            return
        self.show_caret_flag.set(enabled)
        self._apply_caret_indicator(enabled)

    def open_journal(self):
        from journal_gui import JournalWindow
        if self.journal_window and self.journal_window.window.winfo_exists():
            self.journal_window.window.deiconify()
            self.journal_window.window.lift()
        else:
            self.journal_window = JournalWindow(self.root, self)

    def apply_journal_settings(self, enabled, days, apps):
        apps = validate_journal(days, apps)
        catalog = getattr(self, 'language_catalog', None)
        settings = replace(load_settings(catalog=catalog), journal_enabled=enabled,
                           journal_days=days, journal_apps=apps)
        if catalog is None:
            save_settings(settings)
        else:
            save_settings(settings, SETTINGS_PATH, catalog=catalog)
        self.journal_enabled.set(enabled)
        self.journal_days.set(str(days))
        self.journal_apps.set(", ".join(apps))
        self._configure_journal(settings)

    def _configure_journal(self, settings):
        self.journal_io.submit('configure', settings, self._journal_config_applied)
        self.journal_status.set('Дневник: применяются настройки…')

    def _journal_config_applied(self, result, error):
        if error:
            self.journal_status.set('Не удалось применить настройки дневника')
            return
        enabled = self.journal_service.enabled
        self.journal_status.set((self.journal_service.state or 'Дневник включён')
                                if enabled else 'Дневник выключен')
        if self.tray:
            self.tray.set_journal_enabled(enabled)

    def open_exclusions(self):
        from exclusions_gui import ExclusionsWindow
        if self.exclusions_window and self.exclusions_window.window.winfo_exists():
            self.exclusions_window.window.deiconify()
            self.exclusions_window.window.lift()
            return
        self.exclusions_window = ExclusionsWindow(self.root, self)

    def open_snippets(self):
        from snippets_gui import SnippetsWindow
        editor = self.snippets_editor
        if editor and editor.window.winfo_exists():
            editor.window.deiconify(); editor.window.lift()
            return
        self.snippets_editor = SnippetsWindow(self.root, self.snippet_config, self.apply_snippets)

    def apply_snippets(self, config):
        config.save(USER_DIR / 'snippets.json')
        self.snippet_config = config
        if self.hook:
            self.hook.request_snippets(config)
        self.emit('info', 'Автозамены сохранены и применены')

    def _start_tray(self) -> None:
        try:
            from tray_win32 import TrayController
            self.tray = TrayController(lambda action: self.events.put(("tray", action)),
                                       active_languages=self.display_languages)
            self.tray.set_state(*self._tray_state())
            self.tray.set_caret_flag(self.show_caret_flag.get())
            self.tray.set_journal_enabled(self.journal_service.enabled)
            self.tray.configure_clipboard(self.clipboard_tools.settings)
            self.tray.start()
        except Exception as error:
            self.emit("error", f"Трей недоступен: {error}")
            self.root.deiconify()

    def _update_taskbar_language_icon(self, language: str) -> None:
        """Keep a language flag on the recoverable taskbar window as well.

        Explorer may accept a tray icon yet place it in an inaccessible
        overflow area. This separate icon belongs to our regular Tk window;
        unlike the notification icon, it remains reachable when iconified.
        """
        language = language if language in getattr(self, 'display_languages', LANGS) else 'idle'
        if getattr(self, '_window_icon_language', None) == language:
            return
        self._window_icon_language = language
        try:
            from tray_win32 import _icon_files
            self.root.iconbitmap(str(_icon_files(getattr(self, 'display_languages', ()))
                                     [language]))
        except (OSError, RuntimeError, tk.TclError) as error:
            if DEBUG_ENABLED:
                self.emit('diagnostic', 'taskbar-language-icon:' + type(error).__name__)

    def _update_tray(self) -> None:
        if self.closing:
            return
        if getattr(self, 'hook', None) is not None:
            self.hook.expire_native_pending()
        try:
            selected = getattr(self, 'display_languages', DEFAULT_ACTIVE_LANGUAGES)
            catalog = getattr(self, 'language_catalog', None)
            if catalog is not None:
                active_catalog = {code: catalog[code] for code in selected}
                language = foreground_language(active_catalog)
            else:
                language = foreground_language()
        except OSError:
            language = "idle"
        self._update_taskbar_language_icon(language)
        if self.tray:
            try:
                self.tray.maintain()
                if language != self.last_tray_language:
                    self.tray.set_language(language)
                    self.last_tray_language = language
                self.tray.observe_foreground()
            except OSError:
                pass  # A stale Explorer HWND must not cancel the recovery timer.
            if self.root.state() == 'withdrawn' and not self.tray.available():
                self.root.deiconify()
                self.emit('info', 'Значок трея недоступен; окно восстановлено')
        indicator = self.caret_indicator
        if indicator and indicator.error and indicator.error != self.caret_indicator_error:
            self.caret_indicator_error = indicator.error
            self.emit("error", f"Индикатор каретки недоступен: {indicator.error}")
        self.root.after(500, self._update_tray)

    def hide(self) -> None:
        available = bool(self.tray and self.tray.available())
        if DEBUG_ENABLED:
            self.emit('diagnostic', f'tray-hide:ready={bool(self.tray and self.tray.ready.is_set())}:available={available}:window={self.root.state()}')
        # A successful Shell_NotifyIconGetRect means the icon is registered,
        # not that Windows has made it reachable in the visible notification
        # area. In particular, Explorer may put it in an inaccessible overflow
        # on another PC. Never withdraw the last user-accessible GUI window.
        # The minimized taskbar button is the unconditional recovery path.
        self.root.iconify()
        if DEBUG_ENABLED:
            self.emit('diagnostic', f'tray-minimized:available={available}:window={self.root.state()}')
        self.emit('info', 'Окно свёрнуто на панель задач; открой его кнопкой TypeBridge. Значок трея может находиться среди скрытых значков Windows')

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()

    def pause(self) -> None:
        hook = self.hook
        if hook:
            hook.request_pause()

    def toggle_corrections(self) -> None:
        if self.run_state == "running":
            self.pause()
        elif self.run_state in ("stopped", "paused", "error"):
            self.start()

    def _on_corrections_check(self):
        if self.corrections_enabled.get():
            self.start()
        elif self.run_state == 'running':
            self.pause()
        else:
            self._sync_tray_state()

    def _restore_corrections(self):
        if not self.closing and self.corrections_enabled.get():
            self.start()

    def _sync_tray_state(self) -> None:
        self.corrections_enabled.set(self.run_state in ("starting", "running"))
        self.corrections_check.configure(
            state="disabled" if self.run_state in ("starting", "stopping") else "normal")
        if self.tray:
            self.tray.set_state(*self._tray_state())

    def _tray_state(self):
        if self.run_state == 'running' and getattr(self, 'native_quarantine_message', ''):
            return 'degraded', self.native_quarantine_message
        return self.run_state, self.last_start_error

    def current_settings(self) -> Settings:
        excluded = [x.strip().casefold() for x in self.excluded.get().split(",") if x.strip()]
        if any(not x.endswith(".exe") or any(c in x for c in "\\/:\n") for x in excluded):
            raise ValueError("Укажи имена приложений вроде notepad.exe через запятую")
        days = int(self.journal_days.get())
        journal_apps = validate_journal(days, tuple(x.strip().casefold() for x in self.journal_apps.get().split(",") if x.strip()))
        selected_language = next((code for code, name in LANGUAGES.items() if name == self.ui_language.get()), get_language())
        original_order = getattr(self.search_settings, 'active_languages', DEFAULT_ACTIVE_LANGUAGES)
        choices = getattr(self, 'active_language_vars', None)
        selected = (tuple(lang for lang in original_order if choices[lang].get())
                    + tuple(lang for lang in (getattr(self, 'language_catalog', None)
                                              or DEFAULT_ACTIVE_LANGUAGES)
                            if lang not in original_order and choices[lang].get())
                    if choices is not None else original_order)
        catalog = getattr(self, 'language_catalog', None)
        settings = Settings(ui_language=selected_language,
                        active_languages=validate_active_languages(selected, catalog),
                        journal_enabled=self.journal_enabled.get(), journal_days=days, journal_apps=journal_apps,
                        quick_search=self.search_settings.quick_search,
                        quick_search_key=self.search_settings.quick_search_key,
                        quick_search_engine=self.search_settings.quick_search_engine,
                        quick_search_engines=self.search_settings.quick_search_engines,
                        corrections_enabled=self.corrections_enabled.get(),
                        experimental_password_guard=(
                            self.experimental_password_guard.get()
                            if getattr(self, 'experimental_password_guard', None) is not None
                            else getattr(self.search_settings, 'experimental_password_guard', False)),
                        conservative=self.conservative.get(),
                        manual_f8=self.manual_f8.get(),
                        manual_key=self.manual_key.get(),
                        clipboard_history=self.clipboard_history.get(),
                        clipboard_history_key=self.clipboard_history_key.get().strip(),
                        plain_paste=self.plain_paste.get(),
                        plain_paste_key=self.plain_paste_key.get().strip(),
                        cycle_key=self.cycle_key.get(),
                        early_correction=self.early_correction.get(),
                        early_native=self.early_native.get(),
                        show_caret_flag=self.show_caret_flag.get(),
                        fix_caps_lock=self.fix_caps_lock.get(),
                        autostart=self.autostart.get(),
                        direct_ru=(self.direct_keys['ru'].get().strip() if 'ru' in self.direct_keys
                                   else self.search_settings.direct_ru),
                        direct_en=(self.direct_keys['en'].get().strip() if 'en' in self.direct_keys
                                   else self.search_settings.direct_en),
                        direct_he=(self.direct_keys['he'].get().strip() if 'he' in self.direct_keys
                                   else self.search_settings.direct_he),
                        direct_extra=tuple((code, self.direct_keys[code].get().strip())
                                           for code in (catalog or ()) if code not in DEFAULTS
                                           and self.direct_keys[code].get().strip().casefold()
                                           != 'отключено'),
                        language_shortcuts=(tuple((code, self.direct_keys[code].get().strip())
                            for code in (catalog or ())
                            if code in self.direct_keys)
                            + tuple((code, chord) for code, chord in
                                    getattr(self.search_settings, 'language_shortcuts', ())
                                    if code not in (catalog or ()))),
                        only_window_title=self.only_title.get().strip(),
                        excluded_categories=tuple(name for name, var in self.excluded_categories.items() if var.get()),
                        excluded_processes=tuple(dict.fromkeys(
                            (*Settings.excluded_processes, *excluded))))
        compile_bindings(settings, catalog)
        return settings

    def save(self) -> Settings | None:
        try:
            settings = self.current_settings()
            # The settings window can outlive a package install/update/remove.
            # Never persist a selection from stale checkboxes: the next launch
            # would otherwise fail before a verified language catalog is built.
            # Do this BEFORE HKL checks, Run registration, or either settings
            # file. An unrelated broken/inactive package remains isolated.
            displayed_catalog = getattr(self, 'language_catalog', None)
            if displayed_catalog is not None:
                current_catalog = available_language_catalog(
                    LANGUAGE_PACK_DIR, tolerant=True,
                    selected_codes=settings.active_languages)
                for code in settings.active_languages:
                    original, current = displayed_catalog[code], current_catalog[code]
                    if (original != current or
                            getattr(original, 'personal_alphabet_policy', None) !=
                            getattr(current, 'personal_alphabet_policy', None)):
                        raise ValueError(
                            f'Языковой пакет {code.upper()} изменён после открытия '
                            'настроек. Перезапусти TypeBridge перед сохранением.')
            # The live hook must keep its original active languages until
            # restart. Validate shortcuts against BOTH language sets before
            # modifying settings, Run registration or personal dictionaries.
            running_hook = getattr(self, 'hook', None)
            if running_hook is not None:
                running_engine_settings(settings, running_hook.engine)
            elif getattr(self, 'run_state', 'stopped') in ('starting', 'running'):
                # A hook can be absent while its mapper is loading. Validate
                # that in-flight engine's immutable language set as well.
                pending_engine_settings(settings, self.launch_languages,
                                        getattr(self, 'language_catalog', None))
            previous_active = getattr(getattr(self, 'search_settings', None),
                                      'active_languages', settings.active_languages)
            if settings.active_languages != previous_active:
                # Only enumerate loaded HKLs; do not switch, install or bind one.
                catalog = getattr(self, 'language_catalog', None)
                if catalog is not None:
                    check_active_layouts_loaded(settings.active_languages, catalog)
                else:
                    check_active_layouts_loaded(settings.active_languages)
            previous_registration = read_registration()
            registration = startup_command() if settings.autostart else None
            write_registration(registration)
            try:
                if getattr(self, 'language_catalog', None) is not None:
                    save_settings(settings, catalog=getattr(self, 'language_catalog', None))
                else:
                    save_settings(settings)
            except (ValueError, OSError):
                write_registration(previous_registration)
                raise
            lock = getattr(self, '_runtime_config_lock', None)
            if lock is None:
                lock = self._runtime_config_lock = threading.RLock()
            with lock:
                self._desired_runtime_config = (settings, self.lexicon)
                hook = self.hook
                if hook:
                    hook.request_config(settings, self.lexicon)
            if getattr(self, 'search_settings', None) is not None:
                self.search_settings = replace(self.search_settings,
                                               active_languages=settings.active_languages)
            self.clipboard_tools.configure(settings)
            self.quick_search.configure()
            self._configure_journal(settings)
            if self.tray:
                self.tray.configure_clipboard(settings)
            self._apply_caret_indicator(settings.show_caret_flag)
            subset_pending = (settings.active_languages != getattr(
                self, 'launch_languages', settings.active_languages))
            guard_pending = (hook is not None and settings.experimental_password_guard !=
                             bool(getattr(hook, 'password_guard', None)))
            self.emit("info", "Языки сохранены: полностью выйди из TypeBridge и запусти его снова" if
                      subset_pending else
                      "Проверка парольных полей изменена: полностью выйди из TypeBridge и запусти снова" if
                      guard_pending else
                      "Настройки сохранены; будут применены после загрузки" if
                      self.run_state == 'starting' and hook is None else
                      "Настройки сохранены и переданы переключателю")
            return settings
        except (ValueError, OSError) as error:
            messagebox.showerror("Настройки", str(error), parent=self.root)
            return None

    def install_language_package(self) -> None:
        """Install a data-only language ZIP; activate only after full restart.

        The installed catalog is deliberately NOT updated in the running GUI:
        its hotkeys, active selection and LiveHook remain immutable until the
        user closes TypeBridge via the tray and starts the new instance.
        """
        filename = filedialog.askopenfilename(
            parent=self.root, title='Установить языковой пакет TypeBridge',
            filetypes=[('TypeBridge package ZIP', '*.zip')])
        if not filename:
            return
        try:
            from language_package_manager import install_zip
            destination = install_zip(Path(filename), LANGUAGE_PACK_DIR)
        except (ValueError, OSError, zipfile.BadZipFile) as error:
            messagebox.showerror('Языковые пакеты', str(error), parent=self.root)
            return
        messagebox.showinfo('Языковые пакеты',
            f'Пакет {destination.name.upper()} установлен. Полностью выйди из '
            'TypeBridge и запусти его снова, затем включи язык в настройках.',
            parent=self.root)

    def update_language_package(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root, title='Обновить отключённый язык',
            filetypes=[('TypeBridge package ZIP', '*.zip')])
        if not filename:
            return
        try:
            from language_package_manager import update_zip
            destination, backup = update_zip(
                Path(filename), LANGUAGE_PACK_DIR, SETTINGS_PATH,
                running_languages=self.launch_languages)
        except (ValueError, OSError, RuntimeError, zipfile.BadZipFile) as error:
            messagebox.showerror('Языковые пакеты', str(error), parent=self.root)
            return
        messagebox.showinfo('Языковые пакеты',
            f'Пакет {destination.name.upper()} обновлён. Предыдущая версия '
            f'сохранена: {backup}. Перезапусти TypeBridge.', parent=self.root)

    def remove_language_package(self) -> None:
        options = tuple(code for code in self.language_catalog if code not in DEFAULTS)
        if not options:
            messagebox.showinfo('Языковые пакеты', 'Нет внешних пакетов для удаления.',
                                parent=self.root)
            return
        code = simpledialog.askstring(
            'Удаление языкового пакета',
            'Код отключённого языка (' + ', '.join(options) + '):', parent=self.root)
        if not code:
            return
        if code not in options:
            messagebox.showerror('Языковые пакеты', 'Неизвестный код языка.',
                                 parent=self.root)
            return
        if not messagebox.askyesno('Языковые пакеты',
                f'Убрать пакет {code.upper()}? Личные слова сохранятся; '
                'файлы пакета будут перенесены в архив для восстановления.',
                parent=self.root):
            return
        try:
            from language_package_manager import remove_package
            backup = remove_package(LANGUAGE_PACK_DIR, code, SETTINGS_PATH,
                                    running_languages=self.launch_languages)
        except (ValueError, OSError, RuntimeError) as error:
            messagebox.showerror('Языковые пакеты', str(error), parent=self.root)
            return
        messagebox.showinfo('Языковые пакеты',
            f'Пакет отключён и архивирован в {backup}. Для обновления списка '
            'полностью перезапусти TypeBridge.', parent=self.root)

    def restore_language_package(self) -> None:
        folder = filedialog.askdirectory(
            parent=self.root, title='Выбрать архивированную папку языка')
        if not folder:
            return
        try:
            from language_package_manager import restore_package
            restored = restore_package(LANGUAGE_PACK_DIR, Path(folder),
                                       SETTINGS_PATH,
                                       running_languages=self.launch_languages)
        except (ValueError, OSError, RuntimeError) as error:
            messagebox.showerror('Языковые пакеты', str(error), parent=self.root)
            return
        messagebox.showinfo('Языковые пакеты',
            f'Пакет {restored.name.upper()} восстановлен. Перезапусти TypeBridge.',
            parent=self.root)

    def _build_dictionary(self, frame) -> None:
        ttk.Label(frame, text="Добавь имя, термин или слово, которое нельзя переключать.",
                  wraplength=650).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=12)
        self.word_language = tk.StringVar(value=self.display_languages[0])
        self.word_kind = tk.StringVar(value="words")
        self.word_text = tk.StringVar()
        self.word_sensitive = tk.BooleanVar(value=False)
        ttk.Combobox(row, textvariable=self.word_language,
                     values=tuple(getattr(self, 'language_catalog', LANGS)),
                     state="readonly", width=5).pack(side="left")
        ttk.Entry(row, textvariable=self.word_text, width=25).pack(side="left", padx=8)
        ttk.Radiobutton(frame, text="Моё слово: считать правильным", variable=self.word_kind,
                        value="words").pack(anchor="w")
        ttk.Radiobutton(frame, text="Исключение: не исправлять", variable=self.word_kind,
                        value="exclusions").pack(anchor="w")
        ttk.Checkbutton(frame, text="Учитывать регистр: только указанное написание",
                        variable=self.word_sensitive).pack(anchor="w", pady=(6, 0))
        ttk.Button(frame, text="Добавить", command=self.add_word).pack(anchor="w", pady=10)
        self.word_list = ttk.Treeview(frame, columns=("lang", "kind", "word", "mode"),
                                     show="headings", height=12, selectmode="browse")
        for column, title, width in (("lang", "Язык", 50), ("kind", "Тип", 120),
                                      ("word", "Слово", 240), ("mode", "Регистр", 80)):
            self.word_list.heading(column, text=title)
            self.word_list.column(column, width=width)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.word_list.yview)
        self.word_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.word_list.pack(fill="both", expand=True)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=10)
        ttk.Button(actions, text="Удалить выбранное", command=self.remove_word).pack(side="left")
        ttk.Button(actions, text="Импорт словарей…", command=self.open_dictionary_import).pack(side="left", padx=8)
        ttk.Label(frame, text="Слова и настройки хранятся в TypeBridge-data.",
                  foreground="#555").pack(anchor="w")
        self._refresh_words()

    def _refresh_words(self) -> None:
        self.word_rows = {}
        for item in self.word_list.get_children():
            self.word_list.delete(item)
        for kind in ("words", "exclusions"):
            for lang in getattr(self, 'language_catalog', LANGS):
                for mode in ("insensitive", "sensitive"):
                    for word in sorted(self.lexicon.data[kind].get(lang, {}).get(mode, ())):
                        item = self.word_list.insert("", "end", values=(
                            lang.upper(), tr("Слово" if kind == "words" else "Исключение"), word,
                            tr("Точный" if mode == "sensitive" else "Любой")))
                        self.word_rows[item] = (kind, lang, word, mode == "sensitive")

    def _translate_word_rows(self):
        for item, (kind, lang, word, sensitive) in self.word_rows.items():
            self.word_list.item(item, values=(lang.upper(), tr("Слово" if kind == "words" else "Исключение"),
                                             word, tr("Точный" if sensitive else "Любой")))

    def open_dictionary_import(self):
        from dictionary_import_gui import DictionaryImportWindow
        editor = self.dictionary_import_window
        if editor and editor.window.winfo_exists():
            editor.window.deiconify()
            editor.window.lift()
        else:
            self.dictionary_import_window = DictionaryImportWindow(self.root, self)

    def import_exclusion_preview(self, preview):
        from dictionary_import import merge_exclusions
        catalog = getattr(self, 'language_catalog', None)
        previous = (load_user_lexicon(catalog=catalog)
                    if catalog is not None
                    else load_user_lexicon())
        lexicon, added, existing = merge_exclusions(previous, preview)
        if added:
            lexicon.save(USER_DIR.resolve())
            self.lexicon = lexicon
            if self.hook:
                self.hook.request_config(self.hook.settings, lexicon)
            self._refresh_words()
        self.emit("info", f"Импорт исключений: добавлено {added}, уже были {existing}")
        return added, existing

    def _edit_word(self, remove=False) -> None:
        try:
            catalog = getattr(self, 'language_catalog', None)
            lexicon = (load_user_lexicon(catalog=catalog)
                       if catalog is not None
                       else load_user_lexicon())
            if remove:
                selection = self.word_list.selection()
                if not selection:
                    return
                kind, lang, word, sensitive = self.word_rows[selection[0]]
                lexicon.remove(kind, lang, word, sensitive=sensitive)
            else:
                lexicon.add(self.word_kind.get(), self.word_language.get(),
                            self.word_text.get().strip(), sensitive=self.word_sensitive.get())
            lexicon.save(USER_DIR.resolve())
            self.lexicon = lexicon
            hook = self.hook
            if hook:
                hook.request_config(hook.settings, lexicon)
            self.word_text.set("")
            self._refresh_words()
            self.emit("info", "Личный словарь сохранён")
        except (ValueError, OSError) as error:
            messagebox.showerror("Мой словарь", str(error), parent=self.root)

    def add_word(self) -> None:
        self._edit_word()

    def remove_word(self) -> None:
        self._edit_word(remove=True)

    def emit(self, kind: str, message: str) -> None:
        self.events.put((kind, message))

    def open_clipboard_history(self, target=None):
        from clipboard_gui import ClipboardWindow
        if target is None and self.tray:
            target = self.tray.last_target
        if target and target.pid == os.getpid():
            target = None
        if self.clipboard_window and self.clipboard_window.window.winfo_exists():
            self.clipboard_window.show(target)
        else:
            self.clipboard_window = ClipboardWindow(self.root, self.clipboard_tools, target)

    def open_search(self):
        from quick_search_gui import QuickSearchWindow
        if self.search_window and self.search_window.window.winfo_exists():
            self.search_window.window.deiconify()
            self.search_window.window.lift()
        else:
            self.search_window = QuickSearchWindow(self.root, self)

    def apply_search_settings(self, enabled, key, engine, engines):
        # Save only this window's options; unrelated unsaved main-window drafts stay drafts.
        catalog = getattr(self, 'language_catalog', None)
        lock = getattr(self, '_runtime_config_lock', None)
        if lock is None:
            lock = self._runtime_config_lock = threading.RLock()
        with lock:
            settings = replace(load_settings(catalog=catalog),
                quick_search=enabled, quick_search_key=key,
                quick_search_engine=engine,
                quick_search_engines=validate_engines(engines, engine))
            # Saving the language selection earlier does not change the mapper
            # until restart. A search chord must be free in BOTH the requested
            # future set and the currently running/loading engine, before any
            # profile file is touched. This lock also closes the publication
            # race with the asynchronous hook startup.
            compile_bindings(settings, catalog)
            hook = getattr(self, 'hook', None)
            effective = tuple(settings.active_languages)
            if hook is not None:
                effective = tuple(hook.engine.active_languages)
                running_engine_settings(settings, hook.engine)
            elif getattr(self, 'run_state', 'stopped') in ('starting', 'running'):
                effective = tuple(getattr(self, 'launch_languages', effective))
                pending_engine_settings(settings, effective, catalog)
            if catalog is None:
                save_settings(settings)
            else:
                save_settings(settings, SETTINGS_PATH, catalog=catalog)
            persisted = load_settings(SETTINGS_PATH, catalog=catalog)
            fields = ('quick_search', 'quick_search_key',
                      'quick_search_engine', 'quick_search_engines')
            if any(getattr(persisted, name) != getattr(settings, name) for name in fields):
                raise OSError('Не удалось подтвердить сохранение настроек поиска')
            self.search_settings = settings
            if hasattr(self, 'lexicon'):
                self._desired_runtime_config = (settings, self.lexicon)
                if hook is not None:
                    hook.request_config(settings, self.lexicon)
        self.quick_search.configure()
        self.clipboard_tools.configure(settings)
        if self.tray:
            self.tray.configure_clipboard(settings)
        if tuple(settings.active_languages) != effective:
            detail = ('Настройки поиска переданы переключателю; новый набор '
                      'языков вступит в силу после перезапуска TypeBridge')
        elif hook is not None:
            detail = 'Настройки поиска сохранены и переданы переключателю'
        elif getattr(self, 'run_state', 'stopped') == 'starting':
            detail = 'Настройки поиска сохранены; применятся после загрузки'
        else:
            detail = 'Настройки поиска сохранены и применены'
        self.emit('info', detail)

    def _poll_hook_watchdog(self) -> None:
        """Tk timer runs independently of the keyboard hook and event queue."""
        if self.closing:
            return
        if self._hook_watchdog is not None:
            try:
                active = bool(self.hook or self.run_state in ('starting', 'running', 'paused', 'stopping'))
                if active or not self._watchdog_idle_logged:
                    self._hook_watchdog.poll(self.hook, self.worker)
                    self._watchdog_idle_logged = not active
            except Exception as error:
                from dictionary_diagnostics import record_debug_event
                from pipeline_probe import emit_pipeline
                emit_pipeline(self._hook_watchdog, 'callback-error', method='watchdog',
                              reason=type(error).__name__)
        # Metadata for comparing the two PCs is collected by the GUI timer,
        # NEVER during the low-level keyboard callback. Limit to a single
        # Notepad/Notepad++ focus per debug run; no document content is read.
        if (DEBUG_ENABLED and BUILD_PROFILE == 'diagnostics' and self.hook is not None
                and not getattr(self, '_comparison_captured', False)):
            try:
                ctx = self.hook.context()
                if ctx is not None and ctx.process_name in ('notepad.exe', 'notepad++.exe'):
                    from comparison_diagnostics import capture_context
                    details = capture_context(self.hook.user32, ctx, '')
                    self._comparison_captured = True
                    self.emit('comparison', details)
            except (OSError, ValueError, AttributeError) as error:
                self.emit('diagnostic', 'comparison-capture-error:' + type(error).__name__)
        self.root.after(4000, self._poll_hook_watchdog)

    def _drain(self) -> None:
        started = perf_counter()
        try:
            for _ in range(100):
                if perf_counter() - started >= .008:
                    break
                kind, message = self.events.get_nowait()
                if kind == 'journal-io':
                    io = getattr(self, 'journal_io', None)
                    if io is not None:
                        io.receive(message)
                    continue  # Decrypted journal replies must never enter logs.
                if kind == 'native-trace':
                    if DEBUG_ENABLED:
                        for line in message:
                            self._queue_diagnostic('debug', ('diagnostic', line))
                    continue
                if kind == 'deferred-native-read':
                    hook, context = message
                    if hook is self.hook and DEBUG_WORDS_ENABLED:
                        hook._trace_native_after_boundary(context)
                    else:
                        hook._native_observation_pending = False
                    continue
                if kind == 'browser-readback-request':
                    service = getattr(self, '_browser_readback', None)
                    hook, context, details, revision, created = message
                    if (DEBUG_WORDS_ENABLED and hook is self.hook
                            and not self.closing and service is not None):
                        service.request(hook, context, details, revision, created)
                    continue
                if kind == 'browser-readback-result':
                    hook, context, details = message
                    if DEBUG_WORDS_ENABLED and hook is self.hook and not self.closing:
                        hook._trace_word('browser-readback', context, **details)
                    continue
                if kind == 'word-trace':
                    if DEBUG_WORDS_ENABLED:
                        self._queue_diagnostic('word', message)
                    continue
                if kind == 'comparison':
                    if DEBUG_ENABLED:
                        self._queue_diagnostic('comparison', message)
                    continue
                if kind == 'probe':
                    if DEBUG_ENABLED:
                        self._queue_diagnostic('debug', (kind, message))
                    continue
                if DEBUG_ENABLED:
                    self._queue_diagnostic('debug', (kind, message))
                if kind == "diagnostic":
                    self._queue_diagnostic('runtime', message)
                    message = "Замена текста: " + message
                if kind == "quick-search-result":
                    if self.tray:
                        text = self.quick_search.receive(message, self.clipboard_tools.settings, self.tray.layouts, self.closing)
                        from clipboard_selection import SearchPreview
                        if isinstance(text, SearchPreview):
                            from quick_search_gui import show_search_preview
                            show_search_preview(self.root, text, self)
                        elif text:
                            self.status.set(text)
                            if text != 'Поиск открыт в браузере':
                                messagebox.showwarning('Быстрый поиск', text, parent=self.root)
                    continue
                if kind == "journal":
                    generation, text = message
                    io = getattr(self, 'journal_io', None)
                    if io is not None and io.pending_config is not None:
                        continue
                    if generation != self.journal_service.generation:
                        continue
                    self.journal_status.set(text)
                    if self.tray:
                        self.tray.set_journal_enabled(self.journal_service.enabled)
                    continue
                if kind == "clipboard":
                    try:
                        self.clipboard_tools.receive(message)
                    except OSError as error:
                        self.status.set(str(error))
                        messagebox.showerror("Буфер обмена", str(error), parent=self.root)
                    continue
                if kind == "tray":
                    if isinstance(message, tuple):
                        if message[0] == 'tray-callback-error':
                            self._queue_diagnostic('runtime', message[1])
                            self.status.set('Ошибка обработчика трея; подробности: TypeBridge-data\\replacement-diagnostics.log')
                            continue
                        if message[0] == 'tray-diagnostic':
                            # Recovery is background maintenance, not a request
                            # to activate the settings window on every retry.
                            self.emit('probe', 'tray-registration:' + message[1])
                            continue
                        if message[0] == "quick-search":
                            self.quick_search.request(message[1], self.clipboard_tools.settings, self.tray.layouts)
                            continue
                        if message[0] in ("history", "plain-paste"):
                            try:
                                if message[0] == "history":
                                    self.open_clipboard_history(message[1])
                                else:
                                    self.clipboard_tools.request_plain(message[1])
                            except OSError as error:
                                self.status.set(str(error))
                            continue
                        if message[0] == "layout-changed":
                            kind, message = "info", f"Раскладка: {message[1]}"
                        else:
                            # Permanent tray resource failures may repeat on
                            # every retry. Keep the taskbar recovery button;
                            # never bring a visible editor's GUI to front.
                            if getattr(self, '_last_tray_error', None) == message[1]:
                                continue
                            self._last_tray_error = message[1]
                            if self.root.state() == 'withdrawn':
                                self.root.iconify()
                            kind, message = "error", f"Трей: {message[1]}"
                    else:
                        action = {"open": self.show, "snippets": self.open_snippets, "toggle": self.toggle_corrections,
                                  "toggle-caret-flag": self.toggle_caret_flag, "journal": self.open_journal,
                                  "restore-native-input": lambda: self.hook.restore_native_pending() if self.hook else None,
                                  "exit": self.close}.get(message)
                        if action:
                            action()
                        continue
                if kind == 'transport-quarantine':
                    self.native_quarantine_message = message
                elif kind == 'native-recovery-available':
                    if self.tray:
                        self.tray.set_native_recovery(bool(message))
                    continue
                elif kind == 'native-recovery-complete':
                    hook = getattr(self, 'hook', None)
                    if (hook is None or
                            not getattr(hook, 'native_edit_disabled', False)
                            and not getattr(hook, 'native_blocked_scopes', set())):
                        self.native_quarantine_message = ''
                    if self.tray:
                        self.tray.set_native_recovery(False)
                if kind == "ready":
                    self.run_state = "running"
                    self.last_start_error = ""
                elif kind == "paused":
                    self.run_state = "paused"
                elif kind == "error" and self.run_state == "starting":
                    self.run_state = "error"
                    self.last_start_error = message
                elif kind == "stopped":
                    self.native_quarantine_message = ""
                    if self.run_state == "error":
                        message = self.last_start_error
                    else:
                        self.run_state = "stopped"
                self._sync_tray_state()
                self.status.set(getattr(self, 'native_quarantine_message', '') or message)
                self.log.configure(state="normal")
                self.log.insert("end", tr(message) + "\n")
                if int(self.log.index("end-1c").split(".")[0]) > 300:
                    self.log.delete("1.0", "2.0")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        pending = not self.events.empty()
        if self.closing and not (self.worker and self.worker.is_alive()) and not pending:
            io = getattr(self, 'journal_io', None)
            if io is not None and not io.finished.is_set():
                deadline = getattr(self, '_journal_close_deadline', None)
                if deadline is None:
                    deadline = self._journal_close_deadline = monotonic() + 2
                if monotonic() < deadline:
                    self.root.after(20, self._drain)
                    return
            writer = getattr(self, '_diagnostic_writer', None)
            if writer is not None:
                writer.close()
                deadline = getattr(self, '_diagnostic_close_deadline', None)
                if deadline is None:
                    deadline = self._diagnostic_close_deadline = monotonic() + 2
                if not writer.finished.is_set() and monotonic() < deadline:
                    self.root.after(20, self._drain)
                    return
            self.root.destroy()
            return
        self.root.after(1 if pending else 100, self._drain)

    def _queue_diagnostic(self, kind, payload):
        writer = getattr(self, '_diagnostic_writer', None)
        return writer.submit(kind, payload) if writer is not None else False

    def start(self) -> None:
        if self.closing:
            return
        self.corrections_enabled.set(True)
        if self.worker and self.worker.is_alive():
            hook = self.hook
            if hook and hook.paused:
                hook.request_pause()
            return
        settings = self.save()
        if settings is None:
            self._sync_tray_state()
            return
        if settings.active_languages != getattr(self, 'launch_languages',
                                                settings.active_languages):
            self.emit('info', 'Новый набор языков начнёт действовать после полного выхода из TypeBridge и нового запуска')
            self.run_state = 'stopped'
            self._sync_tray_state()
            return
        self.run_state = "starting"
        self._watchdog_idle_logged = False
        self._comparison_captured = False
        self.last_start_error = ""
        self._sync_tray_state()
        self.cancel_start.clear()
        lexicon = self.lexicon

        def worker() -> None:
            try:
                require_no_original_switcher()
                with exclusive_live_hook():
                    data_path = source_data_dir()
                    if DEBUG_ENABLED:
                        import platform
                        import hashlib
                        from diagnostic_identity import computer_id
                        self.emit('info', f'Диагностика: компьютер {computer_id()}')
                        self.emit("info", f"Диагностика: Windows {platform.version()} · {platform.machine()} · EXE {sys.executable}")
                        if getattr(sys, 'frozen', False):
                            digest = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
                            self.emit('info', f'Диагностика: SHA256 EXE {digest}')
                    self.emit("info", f"Загрузка словарей из {data_path.resolve()}")
                    engine = LanguageEngine(
                        data_path, conservative=settings.conservative,
                        user_lexicon=lexicon, cache_dir=USER_DIR / 'dictionary-cache',
                        active_languages=settings.active_languages,
                        pack_root=LANGUAGE_PACK_DIR,
                        cache_progress=lambda state: self.emit('info',
                            'Словари: кэш проверен' if state == 'hit' else
                            'Словари: подготовка нового кэша'))
                    for message in dictionary_report(engine, data_path,
                                                      pack_root=LANGUAGE_PACK_DIR):
                        self.emit("info", message)
                    self.emit("info", "Активные системные раскладки: " +
                              ', '.join(f"{lang.upper()}={int(hkl) & 0xFFFFFFFF:08X}" for lang, hkl in engine.mapper.layouts.items()))
                    with self._runtime_config_lock:
                        current, current_lexicon = self._desired_runtime_config or (settings, lexicon)
                        engine_active = getattr(engine, 'active_languages', DEFAULT_ACTIVE_LANGUAGES)
                        if current.active_languages != engine_active:
                            # GUI saved a new subset while dictionaries loaded.
                            # Never change the mapper underneath native workers.
                            current = pending_engine_settings(
                                current, engine_active, engine.language_catalog)
                            self.emit('info', 'Новый набор языков применится после полного перезапуска TypeBridge')
                        engine.conservative = current.conservative
                        engine.user_lexicon = current_lexicon
                        self.hook = LiveHook(engine, current, self.emit,
                                             snippets=self.snippet_config,
                                             password_guard=staged_password_guard(current))
                    if self.cancel_start.is_set():
                        self.hook.request_stop()
                    self.hook.run()
            except Exception as error:
                self.emit("error", f"Ошибка запуска: {error}")
            finally:
                with self._runtime_config_lock:
                    self.hook = None
                self.emit("stopped", "Остановлен")

        self.status.set("Загрузка словарей…")
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.cancel_start.set()
        hook = getattr(self, 'hook', None)
        native_worker = getattr(hook, 'native_worker', None) if hook else None
        if native_worker is not None and native_worker.is_alive():
            # The RichEdit commit may already have started. Do not unhook and
            # let the daemon worker be killed mid-word while the GUI exits.
            self.status.set('Завершение нативной замены…')
            self.root.after(50, self.stop)
            return
        if self.run_state != "error":
            self.run_state = "stopping" if self.worker and self.worker.is_alive() else "stopped"
        self._sync_tray_state()
        hook = self.hook
        if hook:
            self.status.set("Останавливается…")
            hook.request_stop()

    def close(self) -> None:
        hook = getattr(self, 'hook', None)
        native_worker = getattr(hook, 'native_worker', None) if hook else None
        if native_worker is not None and native_worker.is_alive():
            # Postpone shutdown while the input barrier is still active.
            # Hook removal before the worker drains its held keys can result
            # in mixed old/new layout output or lost key releases.
            self.status.set('Завершение нативной замены…')
            self.root.after(50, self.close)
            return
        editor = getattr(self, 'snippets_editor', None)
        if editor and editor.window.winfo_exists() and not editor.close():
            return
        self.closing = True
        if getattr(self, '_browser_readback', None) is not None:
            self._browser_readback.close()
        if self._raw_probe is not None:
            self._raw_probe.stop()
        if self._control_probe is not None:
            self._control_probe.stop()
        self.quick_search.configure()
        self.clipboard_tools.close()
        io = getattr(self, 'journal_io', None)
        if io is not None:
            io.close()
        else:
            self.journal_service.request_stop()
        if self.tray:
            self.tray.stop()
        if self.caret_indicator:
            self.caret_indicator.stop()
        self.stop()


def self_test() -> dict:
    # Import the optional GUI dependencies without creating windows or hooks.
    from tray_win32 import TrayController
    from PIL import Image, ImageDraw
    from caret_process import smoke_test as caret_helper_smoke_test
    caret_helper_ok = caret_helper_smoke_test()
    engine = LanguageEngine(source_data_dir())
    cases = (("ru", "hello", "en", "hello"),
             ("en", "ghbdtn", "ru", "привет"),
             ("en", "akuo", "he", "שלום"),
             ("ru", "akuo", "he", "שלום"),
             ("he", "hello", "en", "hello"),
             ("he", "ghbdtn", "ru", "привет"))
    results = []
    for current, physical, target, replacement in cases:
        keys = [(ord(char.upper()), False) for char in physical]
        decision = engine.decide(keys, current)
        passed = bool(decision and decision.language == target
                      and decision.replacement == replacement)
        results.append({"from": current, "to": target, "passed": passed})
    return {
        'build_profile': BUILD_PROFILE,
        'word_trace_enabled': DEBUG_WORDS_ENABLED,
        "ok": all(item["passed"] for item in results)
              and ctypes.sizeof(Input) == 40 and caret_helper_ok,
        'caret_helper_ok': caret_helper_ok,
        "input_size": ctypes.sizeof(Input),
        "tray_dependencies": all((TrayController, Image, ImageDraw)),
        "dictionary_sizes": {name: len(words)
                             for name, words in engine.words.items()},
        "confident_sizes": {name: len(words)
                            for name, words in engine.confident.items()},
        "directions": results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-report", type=Path,
                        help="write self-test JSON, including failures, without showing a dialog")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--autostart", action="store_true",
                        help="start in tray with autocorrection after Windows logon")
    parser.add_argument("--accept-injected", action="store_true",
                        help="test only: let synthetic keyboard input reach the engine")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if args.self_test:
        try:
            report = self_test()
        except Exception as error:
            report = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.self_test_report:
            args.self_test_report.write_text(text + "\n", encoding="utf-8")
        if sys.stdout is not None:
            print(text)
        return 0 if report["ok"] else 1
    if args.headless:
        migrate_user_data()
        try:
            if LANGUAGE_PACK_DIR.exists():
                recover_package_transactions(LANGUAGE_PACK_DIR, SETTINGS_PATH)
            catalog = available_language_catalog(LANGUAGE_PACK_DIR)
            settings = load_settings(catalog=catalog)
        except (ValueError, OSError, RuntimeError) as error:
            # A legacy/interrupted journal must not be replayed blindly. Show
            # the manual recovery instructions and exit before any hook starts.
            print(f'TypeBridge startup refused: {error}', file=sys.stderr)
            return 1
        require_no_original_switcher()
        with exclusive_live_hook():
            engine = LanguageEngine(source_data_dir(),
                                    conservative=settings.conservative,
                                    active_languages=settings.active_languages,
                                    user_lexicon=load_user_lexicon(catalog=catalog),
                                    pack_root=LANGUAGE_PACK_DIR)
            LiveHook(engine, settings,
                     lambda kind, msg: print(f"[{kind}] {msg}", flush=True),
                     accept_injected=args.accept_injected,
                     snippets=SnippetConfig.load(USER_DIR / 'snippets.json'),
                     password_guard=staged_password_guard(settings)).run()
        return 0
    root = tk.Tk()
    try:
        migrate_user_data()
        gui = TypeBridgeGui(root)
    except StartupCancelled:
        root.destroy()
        return 1
    except (ValueError, OSError, RuntimeError) as error:
        root.withdraw()
        text = lexicon_error_text(error) if isinstance(error, LexiconLoadError) else str(error)
        messagebox.showerror("TypeBridge: ошибка настроек", text, parent=root)
        root.destroy()
        return 1
    if args.autostart:
        # Never launch into an inaccessible withdrawn window. Allow Tk and
        # the tray helper to initialize, then minimize to the taskbar with
        # a reliable taskbar button even if Explorer hides our tray icon.
        root.after(1200, gui.hide)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
