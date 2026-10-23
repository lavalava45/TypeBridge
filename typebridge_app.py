"""Usable RU/EN/HE keyboard-layout autocorrector for Windows 11.

This is the canonical user-facing prototype.  The large experimental
``prototype.py`` remains available as research evidence, but is not imported
here.  The hook keeps only physical key identities for the unfinished word,
never writes typed words to disk, and ignores injected input.
"""

from __future__ import annotations

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
import threading
import tkinter as tk
from tkinter import ttk
from ui_i18n import (messagebox, tr, localize_window, LocalizedStringVar, ChoiceVar,
                     LANGUAGES, get_language, set_language)

from engine import LANGS, WORD_AFFIXES, LanguageEngine, candidate_token_key, source_data_dir
from runtime_safety import require_no_original_switcher
from single_instance import exclusive_live_hook
from user_lexicon import UserLexicon
from tray_layouts import layout_owner, WindowsLayouts
from direct_hotkeys import DEFAULTS, SUGGESTIONS, MANUAL_KEYS, Chord, compile_bindings
from word_cycle import CYCLE_KEYS, RecentWord, remember_word
from incremental_recognition import prepare_early, build_early_index, suggest_live_early
from early_scintilla import ScintillaEarlyBridge, replace_prefix
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
SETTINGS_PATH = USER_DIR / "settings.json"
DEFAULT_SETTINGS_PATH = APP_DIR / "typebridge-settings.json"

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
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
GWL_STYLE = -16
ES_PASSWORD = 0x0020
OWN_INPUT_MARKER = 0x4C53574954434831  # "LSWITCH1"
BOUNDARY_VKS = frozenset((VK_SPACE, VK_RETURN, VK_TAB))
PUNCTUATION_BOUNDARIES = frozenset(".!?;:…")
MOUSE_BOUNDARIES = frozenset((WM_LBUTTONDOWN, WM_RBUTTONDOWN,
                              WM_MBUTTONDOWN, WM_MOUSEWHEEL,
                              WM_XBUTTONDOWN))


class KbdHook(ctypes.Structure):
    _fields_ = (("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t))


class KeyInput(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t))


class InputUnion(ctypes.Union):
    # Win64 INPUT is 40 bytes because MOUSEINPUT makes the union 32 bytes.
    _fields_ = (("ki", KeyInput), ("reserved", ctypes.c_ubyte * 32))


class Input(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("u", InputUnion))


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
    corrections_enabled: bool = False
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
    show_caret_flag: bool = False
    fix_caps_lock: bool = False
    autostart: bool = False
    direct_ru: str = DEFAULTS["ru"]
    direct_en: str = DEFAULTS["en"]
    direct_he: str = DEFAULTS["he"]
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


def load_settings(path: Path | None = None) -> Settings:
    if path is None:
        path = SETTINGS_PATH if SETTINGS_PATH.exists() else DEFAULT_SETTINGS_PATH
    if not path.exists():
        return Settings()
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("settings must be a JSON object")
    excluded = raw.get("excluded_processes", list(Settings.excluded_processes))
    if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
        raise ValueError("excluded_processes must be a JSON array of names")
    for name in ("conservative", "manual_f8", "early_correction", "show_caret_flag",
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
    settings = Settings(ui_language=ui_language, journal_enabled=raw.get("journal_enabled", False),
                    quick_search=raw.get("quick_search", False),
                    quick_search_key=raw.get("quick_search_key", "Ctrl+Alt+G"),
                    quick_search_engine=raw.get("quick_search_engine", "Google"),
                    quick_search_engines=validate_engines(raw.get("quick_search_engines", DEFAULT_ENGINES), raw.get("quick_search_engine", "Google")),
                    journal_days=raw.get("journal_days", 7), journal_apps=journal_apps,
                    excluded_categories=categories, corrections_enabled=raw.get("corrections_enabled", False),
                    conservative=raw.get("conservative", True),
                    manual_f8=raw.get("manual_f8", True),
                    manual_key=raw.get("manual_key", "Scroll Lock"),
                    clipboard_history=raw.get("clipboard_history", False),
                    clipboard_history_key=raw.get("clipboard_history_key", "Ctrl+Alt+H"),
                    plain_paste=raw.get("plain_paste", False),
                    plain_paste_key=raw.get("plain_paste_key", "Ctrl+Shift+V"),
                    cycle_key=cycle_key,
                    early_correction=raw.get("early_correction", False),
                    show_caret_flag=raw.get("show_caret_flag", False),
                    fix_caps_lock=raw.get("fix_caps_lock", False),
                    autostart=raw.get("autostart", False),
                    direct_ru=raw.get("direct_ru", DEFAULTS["ru"]),
                    direct_en=raw.get("direct_en", DEFAULTS["en"]),
                    direct_he=raw.get("direct_he", DEFAULTS["he"]),
                    only_window_title=title.strip(),
                    excluded_processes=tuple(dict.fromkeys(
                        (*Settings.excluded_processes,
                         *(x.strip().casefold() for x in excluded if x.strip())))))
    compile_bindings(settings)
    return settings


def save_settings(settings: Settings, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def load_user_lexicon(folder: Path = USER_DIR) -> UserLexicon:
    folder.mkdir(parents=True, exist_ok=True)
    return UserLexicon.load(folder.resolve())


def foreground_language() -> str:
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
    return next((lang for lang, value in LANGS.items() if value == language_id), "idle")


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


class LiveHook:
    """Small global hook with a single, inspectable correction path."""

    def __init__(self, engine: LanguageEngine, settings: Settings,
                 emit=lambda _kind, _message: None,
                 *, accept_injected: bool = False, snippets: SnippetConfig | None = None):
        self.engine = engine
        self.settings = settings
        self.emit = emit
        self.accept_injected = accept_injected
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare_api()
        self.keys: list[tuple[int, bool]] = []
        self.token_blocked = False
        self.last_word: RecentWord | None = None
        self.early_done = False
        self.early_disabled = False
        if settings.early_correction:
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
        k.CloseHandle.argtypes = (wintypes.HANDLE,)

    def pressed(self, vk: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)

    def process_name(self, pid: int) -> str:
        if pid in self.process_cache:
            return self.process_cache[pid]
        handle = self.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            path = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(path))
            if not self.kernel32.QueryFullProcessImageNameW(
                    handle, 0, path, ctypes.byref(size)):
                return ""
            name = path.value.rsplit("\\", 1)[-1].casefold()
            self.process_cache[pid] = name
            return name
        finally:
            self.kernel32.CloseHandle(handle)

    def context(self) -> FocusContext | None:
        top = int(self.user32.GetForegroundWindow() or 0)
        if not top:
            return None
        pid = wintypes.DWORD()
        top_thread = int(self.user32.GetWindowThreadProcessId(
            top, ctypes.byref(pid)) or 0)
        if not top_thread or not pid.value or pid.value == os.getpid():
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
            return None
        process = self.process_name(focus_pid.value)
        host_process = self.process_name(pid.value)
        if (not process or not host_process
                or process in effective_exclusions(self.settings)
                or host_process in effective_exclusions(self.settings)):
            return None
        title_buffer = ctypes.create_unicode_buffer(512)
        self.user32.GetWindowTextW(top, title_buffer, len(title_buffer))
        title = title_buffer.value
        only_title = self.settings.only_window_title.casefold()
        if only_title and only_title not in title.casefold():
            return None
        class_buffer = ctypes.create_unicode_buffer(128)
        self.user32.GetClassNameW(focus, class_buffer, len(class_buffer))
        if (class_buffer.value.casefold() == "edit"
                and self.user32.GetWindowLongPtrW(focus, GWL_STYLE)
                & ES_PASSWORD):
            return None
        owner, owner_thread = layout_owner(self.user32, top, focus, top_thread, focus_thread)
        language_id = int(self.user32.GetKeyboardLayout(owner_thread) or 0) & 0xFFFF
        language = next((name for name, code in LANGS.items()
                         if code == language_id), "")
        if not language:
            return None
        return FocusContext(top, focus, owner_thread, language, process, title, owner)

    def clear_token(self, *, preserve_snippets=False) -> None:
        self.keys.clear()
        self.token_blocked = False
        self.last_word = None
        self.early_done = False
        if not preserve_snippets and getattr(self, "snippets", None):
            self.snippets.reset()

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
        events = correction_inputs(expansion.delete, '', None, '')
        # Template line breaks/tabs are literal Unicode characters, not commands
        # that could submit a form or move focus to another field.
        events.extend(unicode_inputs(expansion.text.replace('\n', '\r')))
        if expansion.separator in ('\n', '\t'):
            code = VK_RETURN if expansion.separator == '\n' else VK_TAB
            events.extend((input_key(code), input_key(code, KEYEVENTF_KEYUP)))
        else:
            events.extend(unicode_inputs(expansion.separator))
        self.clear_token()
        matcher.invalidate()
        self.token_blocked = True
        batch = (Input * len(events))(*events)
        sent = int(self.user32.SendInput(len(events), batch, ctypes.sizeof(Input)))
        if sent == 0:
            self.emit('error', 'Автозамена не отправлена')
            return False, False
        self.suppressed_keyups.add(vk)
        if sent != len(events):
            self.emit('error', f'Неполная автозамена: отправлено {sent}/{len(events)}; повтор не выполняется')
        else:
            self.emit('ok', 'Шаблон вставлен')
        return True, False

    def _try_early(self, context: FocusContext) -> bool:
        if (not self.settings.early_correction or self.paused
                or getattr(self, "early_done", False)
                or getattr(self, "early_disabled", False)
                or context.process_name != "notepad++.exe"):
            return False
        decision = suggest_live_early(self.engine, self.keys, context.language)
        if decision is None:
            return False
        source_hkl = int(self.engine.mapper.layouts[context.language]) & 0xFFFFFFFF
        def valid():
            current = self.context()
            return (current is not None and self._word_scope(current) == self._word_scope(context)
                    and current.language == context.language
                    and int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFFFFFF == source_hkl
                    and not any(self.pressed(vk) for vk in
                                (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)))
        bridge = None
        try:
            with ScintillaEarlyBridge(context) as bridge:
                outcome = replace_prefix(bridge, decision.typed[:-1],
                                         decision.replacement, valid)
        except Exception as error:
            self.early_disabled = True
            self.emit("error", f"Ранний режим отключён до перезапуска: {type(error).__name__}")
            return False
        if bridge.timeout or getattr(bridge, "budget_exceeded", False):
            self.early_disabled = True
            self.emit("error", "Ранний режим отключён: редактор не ответил вовремя")
        if outcome.state == "refused":
            return False  # No document write: forward the triggering key normally.
        if outcome.state != "verified":
            self.early_disabled = True
            self.clear_token()
            self.token_blocked = True
            self.emit("error", "Ранняя замена не подтверждена; повтор отключён")
            return True  # Mutation may already include this character; never replay.
        hkl = int(self.engine.mapper.layouts[decision.language])
        result = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(context.layout_window or context.focus,
            WM_INPUTLANGCHANGEREQUEST, 0, hkl, SMTO_ABORTIFHUNG | SMTO_BLOCK,
            30, ctypes.byref(result))
        observed = int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFFFFFF
        self.last_language = next((name for name, code in LANGS.items()
                                   if code == (observed & 0xFFFF)), "")
        if observed != hkl & 0xFFFFFFFF:
            self.early_disabled = True
            self.clear_token()
            self.token_blocked = True
            self.emit("error", "Ранний текст исправлен, но смена раскладки не подтверждена")
        else:
            self.early_done = True
            self.emit("ok", f"До пробела: {context.language.upper()} → {decision.language.upper()}"
                      f" · {len(self.keys)} символов")
        return True

    @staticmethod
    def _word_scope(context: FocusContext) -> tuple:
        return (context.top, context.focus, context.thread_id, context.process_name,
                context.layout_window)

    def _remember(self, context, keys, text, language, suffix, *, whole=False):
        self.last_word = remember_word(keys, text, language, suffix,
                                       self._word_scope(context), whole=whole)

    def _switch_direct(self, context: FocusContext, language: str) -> None:
        self.clear_token()
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
            language, replacement = word.next_variant(self.engine.mapper)
        except ValueError as error:
            self.emit("info", str(error))
            return
        events = correction_inputs(len(word.text + word.suffix),
                                   replacement + word.suffix, None, "")
        batch = (Input * len(events))(*events)
        sent = int(self.user32.SendInput(len(events), batch, ctypes.sizeof(Input)))
        if sent != len(events):
            self.clear_token()
            self.token_blocked = True
            self.emit("error", f"Ручная замена: отправлено {sent}/{len(events)}; повтор отменён")
            return
        hkl = self.engine.mapper.layouts[language]
        result = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(context.layout_window or context.focus,
            WM_INPUTLANGCHANGEREQUEST, 0, hkl, SMTO_ABORTIFHUNG | SMTO_BLOCK,
            180, ctypes.byref(result))
        observed = int(self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFF
        self.last_language = next((name for name, code in LANGS.items() if code == observed), "")
        word.text, word.language = replacement, language
        self.keys.clear()
        # Extending a manually changed unfinished word must not auto-correct
        # only its newly typed tail. A delimiter ends this protection.
        self.token_blocked = not bool(word.suffix)
        self.emit("ok" if self.last_language == language else "error",
                  f"Ручное переключение → {language.upper()}" if self.last_language == language
                  else "Слово переключено, но приложение не подтвердило смену раскладки")

    def _boundary(self, vk: int, language: str,
                  shift: bool) -> tuple[int | None, str] | None:
        if vk in BOUNDARY_VKS:
            return vk, ""
        if candidate_token_key(self.engine.mapper, vk, shift, language, bool(self.keys)):
            # OEM punctuation may be a letter anywhere, e.g. EN ';' is RU ж.
            # Resolve literal trailing punctuation once the token is complete.
            return None
        character = self.engine.mapper.char(vk, language, shift)
        if len(character) == 1 and character in PUNCTUATION_BOUNDARIES:
            return None, character
        return None

    def _correct(self, context: FocusContext, boundary_vk: int | None,
                 boundary_text: str, *, manual: bool = False) -> bool:
        suffix = " " if boundary_vk == VK_SPACE else boundary_text
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
        original_length = len(keys)
        self.clear_token(preserve_snippets=True)
        if decision is None:
            if remember and keys:
                typed = self.engine.mapper.word(keys, context.language)
                whole = False
                if typed and (not typed[0].isalpha() or not typed[-1].isalpha()):
                    match = self.engine.decide(keys, context.language, allow_rare=True)
                    whole = bool(match and match.reason == "unique dictionary match")
                self._remember(context, keys, typed, context.language, suffix, whole=whole)
            return False
        events = correction_inputs(original_length, decision.replacement,
                                   boundary_vk, boundary_text)
        if caps:
            events = [input_key(VK_CAPITAL), input_key(VK_CAPITAL, KEYEVENTF_KEYUP)] + events
            # Last-word cycling must preserve the corrected case with CapsLock off.
            keys = [(vk, not shift if 0x41 <= vk <= 0x5A else shift) for vk, shift in keys]
        batch = (Input * len(events))(*events)
        ctypes.set_last_error(0)
        sent = int(self.user32.SendInput(
            len(events), batch, ctypes.sizeof(Input)))
        if sent != len(events):
            self.token_blocked = True
            self.emit("error", f"SendInput: отправлено {sent}/{len(events)}")
            return False
        target_hkl = int(self.engine.mapper.layouts[decision.language])
        result = ctypes.c_size_t()
        self.user32.SendMessageTimeoutW(
            context.layout_window or context.focus, WM_INPUTLANGCHANGEREQUEST, 0, target_hkl,
            SMTO_ABORTIFHUNG | SMTO_BLOCK, 180, ctypes.byref(result))
        observed_language_id = int(
            self.user32.GetKeyboardLayout(context.thread_id) or 0) & 0xFFFF
        changed = observed_language_id == (target_hkl & 0xFFFF)
        self.last_language = next((name for name, code in LANGS.items()
                                   if code == observed_language_id), "")
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
        return True

    def handle_keyboard(self, code, message, param):
        if code < 0:
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        event = ctypes.cast(param, ctypes.POINTER(KbdHook)).contents
        vk = int(event.vkCode)
        if event.dwExtraInfo == OWN_INPUT_MARKER:
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if (event.flags & LLKHF_INJECTED) and not self.accept_injected:
            self.clear_token()
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if message in (WM_KEYUP, WM_SYSKEYUP):
            if vk in self.suppressed_keyups:
                self.suppressed_keyups.discard(vk)
                return 1
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if message not in (WM_KEYDOWN, WM_SYSKEYDOWN):
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if self.stopping:
            self.clear_token()
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        context = self.context()
        if context is None:
            self.clear_token()
            self.last_focus = 0
            self.last_language = ""
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if (vk == VK_PAUSE and self.pressed(VK_CONTROL)
                and self.pressed(VK_MENU)):
            # Key-repeat must not toggle the pause state more than once.
            if vk not in self.suppressed_keyups:
                self.toggle_pause()
                self.suppressed_keyups.add(vk)
            return 1
        if (context.focus != self.last_focus
                or context.language != self.last_language):
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
                self._direct_bindings = compile_bindings(self.settings)
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
            self.clear_token()
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        if vk == VK_BACK:
            if getattr(self, 'snippets', None): self.snippets.invalidate()
            self.last_word = None
            if not self.keys:
                self.token_blocked = True
            if self.keys:
                self.keys.pop()
            return self.user32.CallNextHookEx(
                self.keyboard_hook, code, message, param)
        shift = self.pressed(VK_SHIFT)
        if 0x41 <= vk <= 0x5A:
            shift ^= bool(self.user32.GetKeyState(VK_CAPITAL) & 1)
        expanded, pending_snippet = self._try_snippet(context, vk, shift)
        if expanded:
            return 1
        if pending_snippet:
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
                                context.language, bool(self.keys))
                or self.engine.mapper.char(vk, context.language, shift) in WORD_AFFIXES):
            self.keys.append((vk, shift))
            if len(self.keys) > 40:
                self.clear_token(preserve_snippets=True)
                self.token_blocked = True
            elif self._try_early(context):
                return 1
        else:
            self.clear_token(preserve_snippets=True)
            character = self.engine.mapper.char(vk, context.language, shift)
            self.token_blocked = bool(character and character.isprintable())
        return self.user32.CallNextHookEx(
            self.keyboard_hook, code, message, param)

    def handle_mouse(self, code, message, param):
        if code >= 0 and int(message) in MOUSE_BOUNDARIES:
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
        index = build_early_index(self.engine, lexicon) if settings.early_correction else None
        self.pending_config = (settings, lexicon, index)
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
        if self.stopping:
            self.thread_id = 0
            return
        self.keyboard_callback = self.callback_type(self.handle_keyboard)
        self.mouse_callback = self.callback_type(self.handle_mouse)
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
        self.emit("ready", "Автоисправление включено")
        message = wintypes.MSG()
        try:
            while self.user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0) > 0:
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
            if self.mouse_hook:
                self.user32.UnhookWindowsHookEx(self.mouse_hook)
            if self.keyboard_hook:
                self.user32.UnhookWindowsHookEx(self.keyboard_hook)
            self.mouse_hook = self.keyboard_hook = None
            self.thread_id = 0
            self.emit("stopped", "Автопереключение остановлено")


class TypeBridgeGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.hook: LiveHook | None = None
        self.worker: threading.Thread | None = None
        self.closing = False
        self.cancel_start = threading.Event()
        self.tray = None
        self.caret_indicator = None
        self.caret_indicator_error = ""
        self.last_tray_language = None
        self.run_state = "stopped"
        self.last_start_error = ""
        settings = load_settings()
        set_language(settings.ui_language)
        self.ui_language = tk.StringVar(value=LANGUAGES[settings.ui_language])
        self.lexicon = load_user_lexicon()
        self.snippet_config = SnippetConfig.load(USER_DIR / 'snippets.json')
        self.snippets_editor = None
        self.dictionary_import_window = None
        self.journal_window = None
        self.search_window = None
        self.search_settings = settings
        self.quick_search = QuickSearch(lambda result: self.events.put(("quick-search-result", result)))
        self.journal_enabled = tk.BooleanVar(value=settings.journal_enabled)
        self.journal_days = tk.StringVar(value=str(settings.journal_days))
        self.journal_apps = tk.StringVar(value=", ".join(settings.journal_apps))
        self.journal_status = LocalizedStringVar(value="Дневник включён" if settings.journal_enabled else "Дневник выключен")
        self.journal_service = JournalService(JournalStore(USER_DIR / 'journal' / 'journal.sqlite3'),
                                            lambda message: self.events.put(("journal", message)))
        self.journal_service.configure(settings)
        self.clipboard_window = None
        from clipboard_tools import ClipboardTools
        self.clipboard_tools = ClipboardTools(root, lambda message: self.events.put(("clipboard", message)))
        self.clipboard_tools.configure(settings)
        root.title("TypeBridge — RU / EN / HE")
        root.geometry("740x775")
        root.minsize(700, 735)
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
        self.early_correction = tk.BooleanVar(value=settings.early_correction)
        self.show_caret_flag = tk.BooleanVar(value=settings.show_caret_flag)
        self.fix_caps_lock = tk.BooleanVar(value=settings.fix_caps_lock)
        self.autostart = tk.BooleanVar(value=read_registration() is not None)
        self.corrections_enabled = tk.BooleanVar(value=settings.corrections_enabled)
        self.direct_keys = {lang: ChoiceVar(value=getattr(settings, f"direct_{lang}"))
                            for lang in LANGS}
        self.only_title = tk.StringVar(value=settings.only_window_title)
        self.excluded = tk.StringVar(value=", ".join(
            name for name in settings.excluded_processes if name not in PASSWORD_PROCESSES))
        self.excluded_categories = {name: tk.BooleanVar(value=name in settings.excluded_categories)
                                    for name in CATEGORIES}
        self.exclusions_window = None
        self._build()
        root._i18n_refresh = self._translate_word_rows
        localize_window(root)
        root.after(100, self._drain)
        root.after(150, self._start_tray)
        root.after(500, self._update_tray)
        root.after(200, lambda: self._apply_caret_indicator(self.show_caret_flag.get()))
        if settings.corrections_enabled:
            root.after(300, self._restore_corrections)

    def _build(self) -> None:
        language_bar = ttk.Frame(self.root)
        language_bar.pack(fill="x", padx=14, pady=(10, 0))
        ttk.Label(language_bar, text="Язык интерфейса:").pack(side="left")
        language_picker = ttk.Combobox(language_bar, textvariable=self.ui_language,
                                      values=tuple(LANGUAGES.values()), state="readonly", width=16)
        language_picker.pack(side="left", padx=8)
        language_picker.bind("<<ComboboxSelected>>", self.change_ui_language)
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        frame = ttk.Frame(notebook, padding=18)
        notebook.add(frame, text="Переключатель")
        ttk.Label(frame, text="Автопереключатель RU / EN / HE",
                  font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.status,
                  font=("Segoe UI", 11)).pack(anchor="w", pady=(4, 14))
        self.corrections_check = ttk.Checkbutton(
            frame, text="Включить автоисправление", variable=self.corrections_enabled,
            command=self._on_corrections_check)
        self.corrections_check.pack(anchor="w", pady=(0, 10))
        ttk.Checkbutton(
            frame, text="Осторожный режим: исправлять только уверенные совпадения",
            variable=self.conservative).pack(anchor="w")
        ttk.Checkbutton(frame, text="Исправлять до введения пробела",
                        variable=self.early_correction).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(frame, text="Исправлять случайный CapsLock: пРИВЕТ → Привет",
                        variable=self.fix_caps_lock).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(frame, text="Автозапуск в трее",
                        variable=self.autostart).pack(anchor="w", pady=(6, 0))
        manual_row = ttk.Frame(frame)
        manual_row.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(manual_row, text="Исправить набираемое слово до пробела",
                        variable=self.manual_f8).pack(side="left")
        ttk.Combobox(manual_row, textvariable=self.manual_key, state="readonly",
                     values=tuple(MANUAL_KEYS), width=15).pack(side="left", padx=10)
        cycle_row = ttk.Frame(frame)
        cycle_row.pack(fill="x", pady=(6, 0))
        ttk.Label(cycle_row, text="Перебрать раскладки последнего слова:").pack(side="left")
        ttk.Combobox(cycle_row, textvariable=self.cycle_key, state="readonly",
                     values=tuple(CYCLE_KEYS), width=15).pack(side="left", padx=10)
        title_row = ttk.Frame(frame)
        title_row.pack(fill="x", pady=(12, 4))
        ttk.Label(title_row, text="Только окно с заголовком:").pack(side="left")
        ttk.Entry(title_row, textvariable=self.only_title).pack(
            side="left", fill="x", expand=True, padx=(10, 0))
        ttk.Label(frame, text="Оставь поле пустым для всех приложений. "
                  "Исключения приложений настраиваются отдельно.",
                  foreground="#555").pack(anchor="w")
        tools = ttk.Frame(frame)
        tools.pack(fill="x", pady=(10, 2))
        ttk.Button(tools, text="Исключения приложений…", command=self.open_exclusions).pack(side="left")
        ttk.Button(tools, text="Дневник ввода…", command=self.open_journal).pack(side="left", padx=8)
        ttk.Button(tools, text="Быстрый поиск…", command=self.open_search).pack(side="left")
        ttk.Label(frame, textvariable=self.journal_status, foreground="#a12b20").pack(anchor="w")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=15)
        self.save_button = ttk.Button(buttons, text="Сохранить настройки", command=self.save)
        self.save_button.pack(side="left")
        ttk.Label(frame, text="События (слова не записываются):").pack(anchor="w")
        self.log = tk.Text(frame, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(5, 0))
        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Button(footer, text="Свернуть в трей", command=self.hide).pack(side="left")
        ttk.Button(footer, text="Автозамена и шаблоны…", command=self.open_snippets).pack(side="left", padx=8)
        ttk.Button(footer, text="Выход", command=self.close).pack(side="right")
        dictionary = ttk.Frame(notebook, padding=18)
        notebook.add(dictionary, text="Мои слова и исключения")
        self._build_dictionary(dictionary)
        shortcuts = ttk.Frame(notebook, padding=18)
        notebook.add(shortcuts, text="Горячие клавиши")
        ttk.Label(shortcuts, text="Установить раскладку без изменения текста",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 14))
        for lang, title in (("ru", "Русский"), ("en", "Английский"), ("he", "Иврит")):
            row = ttk.Frame(shortcuts)
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=title, width=16).pack(side="left")
            ttk.Combobox(row, textvariable=self.direct_keys[lang],
                         values=SUGGESTIONS, width=25).pack(side="left")
        ttk.Label(shortcuts, text="Выбери сочетание или введи своё, например Ctrl+Alt+1.\n"
                  "Для отключения выбери «Отключено». Команды работают при запущенном\n"
                  "переключателе, в том числе когда автоисправление отключено.",
                  wraplength=620).pack(anchor="w", pady=14)
        ttk.Button(shortcuts, text="Сохранить настройки", command=self.save).pack(anchor="w")
        clipboard = ttk.Frame(notebook, padding=18)
        notebook.add(clipboard, text="Буфер обмена")
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
        build_privacy_tab(notebook)
        ttk.Button(clipboard, text="Очистить историю", command=self.clipboard_tools.clear_history).pack(anchor="w", pady=6)
        ttk.Button(clipboard, text="Сохранить настройки", command=self.save).pack(anchor="w", pady=12)
        indication = ttk.Frame(notebook, padding=18)
        notebook.add(indication, text="Индикация")
        ttk.Checkbutton(indication, text="Показывать флажок раскладки рядом с текстовым курсором",
                        variable=self.show_caret_flag).pack(anchor="w", pady=(0, 12))
        ttk.Label(indication, text="Флажок показывает RU / EN / HE в активном поле ввода.\n"
                  "Не забирает фокус и пропускает клики. Работает независимо\n"
                  "от автоисправления. Если поле не сообщает положение каретки,\n"
                  "индикатор скрывается.", wraplength=620).pack(anchor="w", pady=(0, 14))
        ttk.Button(indication, text="Сохранить настройки", command=self.save).pack(anchor="w")

    def change_ui_language(self, event=None):
        selected = next((code for code, name in LANGUAGES.items() if name == self.ui_language.get()), None)
        if selected is None:
            return
        try:
            save_settings(replace(load_settings(), ui_language=selected))
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
            self.caret_indicator = CaretFlagIndicator()
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
            save_settings(replace(load_settings(), show_caret_flag=enabled))
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
        settings = replace(load_settings(), journal_enabled=enabled, journal_days=days, journal_apps=apps)
        save_settings(settings)
        self.journal_enabled.set(enabled)
        self.journal_days.set(str(days))
        self.journal_apps.set(", ".join(apps))
        self._configure_journal(settings)

    def _configure_journal(self, settings):
        self.journal_service.configure(settings)
        self.journal_status.set("Дневник включён" if self.journal_service.enabled else "Дневник выключен")
        if self.tray:
            self.tray.set_journal_enabled(self.journal_service.enabled)

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
            self.tray = TrayController(lambda action: self.events.put(("tray", action)))
            self.tray.set_state(self.run_state, self.last_start_error)
            self.tray.set_caret_flag(self.show_caret_flag.get())
            self.tray.set_journal_enabled(self.journal_service.enabled)
            self.tray.configure_clipboard(self.clipboard_tools.settings)
            self.tray.start()
        except Exception as error:
            self.emit("error", f"Трей недоступен: {error}")
            self.root.deiconify()

    def _update_tray(self) -> None:
        if self.closing:
            return
        try:
            language = foreground_language()
        except OSError:
            language = "idle"
        if self.tray and language != self.last_tray_language:
            self.tray.set_language(language)
            self.last_tray_language = language
        if self.tray:
            self.tray.observe_foreground()
        indicator = self.caret_indicator
        if indicator and indicator.error and indicator.error != self.caret_indicator_error:
            self.caret_indicator_error = indicator.error
            self.emit("error", f"Индикатор каретки недоступен: {indicator.error}")
        self.root.after(500, self._update_tray)

    def hide(self) -> None:
        if self.tray and self.tray.ready.is_set():
            self.root.withdraw()
        else:
            self.emit("info", "Трей ещё недоступен; окно оставлено открытым")

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
            self.tray.set_state(self.run_state, self.last_start_error)

    def current_settings(self) -> Settings:
        excluded = [x.strip().casefold() for x in self.excluded.get().split(",") if x.strip()]
        if any(not x.endswith(".exe") or any(c in x for c in "\\/:\n") for x in excluded):
            raise ValueError("Укажи имена приложений вроде notepad.exe через запятую")
        days = int(self.journal_days.get())
        journal_apps = validate_journal(days, tuple(x.strip().casefold() for x in self.journal_apps.get().split(",") if x.strip()))
        selected_language = next((code for code, name in LANGUAGES.items() if name == self.ui_language.get()), get_language())
        settings = Settings(ui_language=selected_language, journal_enabled=self.journal_enabled.get(), journal_days=days, journal_apps=journal_apps,
                        quick_search=self.search_settings.quick_search,
                        quick_search_key=self.search_settings.quick_search_key,
                        quick_search_engine=self.search_settings.quick_search_engine,
                        quick_search_engines=self.search_settings.quick_search_engines,
                        corrections_enabled=self.corrections_enabled.get(),
                        conservative=self.conservative.get(),
                        manual_f8=self.manual_f8.get(),
                        manual_key=self.manual_key.get(),
                        clipboard_history=self.clipboard_history.get(),
                        clipboard_history_key=self.clipboard_history_key.get().strip(),
                        plain_paste=self.plain_paste.get(),
                        plain_paste_key=self.plain_paste_key.get().strip(),
                        cycle_key=self.cycle_key.get(),
                        early_correction=self.early_correction.get(),
                        show_caret_flag=self.show_caret_flag.get(),
                        fix_caps_lock=self.fix_caps_lock.get(),
                        autostart=self.autostart.get(),
                        direct_ru=self.direct_keys["ru"].get().strip(),
                        direct_en=self.direct_keys["en"].get().strip(),
                        direct_he=self.direct_keys["he"].get().strip(),
                        only_window_title=self.only_title.get().strip(),
                        excluded_categories=tuple(name for name, var in self.excluded_categories.items() if var.get()),
                        excluded_processes=tuple(dict.fromkeys(
                            (*Settings.excluded_processes, *excluded))))
        compile_bindings(settings)
        return settings

    def save(self) -> Settings | None:
        try:
            settings = self.current_settings()
            previous_registration = read_registration()
            registration = startup_command() if settings.autostart else None
            write_registration(registration)
            try:
                save_settings(settings)
            except (ValueError, OSError):
                write_registration(previous_registration)
                raise
            hook = self.hook
            if hook:
                hook.request_config(settings, self.lexicon)
            self.clipboard_tools.configure(settings)
            self.quick_search.configure()
            self._configure_journal(settings)
            if self.tray:
                self.tray.configure_clipboard(settings)
            self._apply_caret_indicator(settings.show_caret_flag)
            self.emit("info", "Настройки сохранены и применены")
            return settings
        except (ValueError, OSError) as error:
            messagebox.showerror("Настройки", str(error), parent=self.root)
            return None

    def _build_dictionary(self, frame) -> None:
        ttk.Label(frame, text="Добавь имя, термин или слово, которое нельзя переключать.",
                  wraplength=650).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=12)
        self.word_language = tk.StringVar(value="ru")
        self.word_kind = tk.StringVar(value="words")
        self.word_text = tk.StringVar()
        self.word_sensitive = tk.BooleanVar(value=False)
        ttk.Combobox(row, textvariable=self.word_language, values=("ru", "en", "he"),
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
        ttk.Label(frame, text="Слова и настройки хранятся в %LOCALAPPDATA%\\TypeBridge.",
                  foreground="#555").pack(anchor="w")
        self._refresh_words()

    def _refresh_words(self) -> None:
        self.word_rows = {}
        for item in self.word_list.get_children():
            self.word_list.delete(item)
        for kind in ("words", "exclusions"):
            for lang in LANGS:
                for mode in ("insensitive", "sensitive"):
                    for word in sorted(self.lexicon.data[kind][lang][mode]):
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
        previous = load_user_lexicon()
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
            lexicon = load_user_lexicon()
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
        settings = replace(load_settings(), quick_search=enabled, quick_search_key=key,
                           quick_search_engine=engine, quick_search_engines=validate_engines(engines, engine))
        compile_bindings(settings)
        save_settings(settings)
        self.search_settings = settings
        self.quick_search.configure()
        self.clipboard_tools.configure(settings)
        if self.tray:
            self.tray.configure_clipboard(settings)
        self.emit("info", "Настройки сохранены и применены")

    def _drain(self) -> None:
        try:
            while True:
                kind, message = self.events.get_nowait()
                if kind == "quick-search-result":
                    if self.tray:
                        text = self.quick_search.receive(message, self.clipboard_tools.settings, self.tray.layouts, self.closing)
                        if text:
                            self.status.set(text)
                    continue
                if kind == "journal":
                    generation, text = message
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
                            self.show()
                            kind, message = "error", f"Трей: {message[1]}"
                    else:
                        action = {"open": self.show, "snippets": self.open_snippets, "toggle": self.toggle_corrections,
                                  "toggle-caret-flag": self.toggle_caret_flag, "journal": self.open_journal,
                                  "exit": self.close}.get(message)
                        if action:
                            action()
                        continue
                if kind == "ready":
                    self.run_state = "running"
                    self.last_start_error = ""
                elif kind == "paused":
                    self.run_state = "paused"
                elif kind == "error" and self.run_state == "starting":
                    self.run_state = "error"
                    self.last_start_error = message
                elif kind == "stopped":
                    if self.run_state == "error":
                        message = self.last_start_error
                    else:
                        self.run_state = "stopped"
                self._sync_tray_state()
                self.status.set(message)
                self.log.configure(state="normal")
                self.log.insert("end", tr(message) + "\n")
                if int(self.log.index("end-1c").split(".")[0]) > 300:
                    self.log.delete("1.0", "2.0")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        if self.closing and not (self.worker and self.worker.is_alive()):
            self.root.destroy()
            return
        self.root.after(100, self._drain)

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
        self.run_state = "starting"
        self.last_start_error = ""
        self._sync_tray_state()
        self.cancel_start.clear()
        lexicon = self.lexicon

        def worker() -> None:
            try:
                require_no_original_switcher()
                with exclusive_live_hook():
                    engine = LanguageEngine(
                        source_data_dir(), conservative=settings.conservative,
                        user_lexicon=lexicon)
                    self.hook = LiveHook(engine, settings, self.emit, snippets=self.snippet_config)
                    if self.cancel_start.is_set():
                        self.hook.request_stop()
                    self.hook.run()
            except Exception as error:
                self.emit("error", f"Ошибка запуска: {error}")
            finally:
                self.hook = None
                self.emit("stopped", "Остановлен")

        self.status.set("Загрузка словарей…")
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.cancel_start.set()
        if self.run_state != "error":
            self.run_state = "stopping" if self.worker and self.worker.is_alive() else "stopped"
        self._sync_tray_state()
        hook = self.hook
        if hook:
            self.status.set("Останавливается…")
            hook.request_stop()

    def close(self) -> None:
        editor = getattr(self, 'snippets_editor', None)
        if editor and editor.window.winfo_exists() and not editor.close():
            return
        self.closing = True
        self.clipboard_tools.close()
        self.journal_service.close()
        if self.tray:
            self.tray.stop()
        if self.caret_indicator:
            self.caret_indicator.stop()
        self.stop()


def self_test() -> dict:
    # Import the optional GUI dependencies without creating windows or hooks.
    from tray_win32 import TrayController
    from PIL import Image, ImageDraw
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
        "ok": all(item["passed"] for item in results)
              and ctypes.sizeof(Input) == 40,
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
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--autostart", action="store_true",
                        help="start in tray with autocorrection after Windows logon")
    parser.add_argument("--accept-injected", action="store_true",
                        help="test only: let synthetic keyboard input reach the engine")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if args.self_test:
        report = self_test()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if args.headless:
        migrate_user_data()
        settings = load_settings()
        require_no_original_switcher()
        with exclusive_live_hook():
            engine = LanguageEngine(source_data_dir(),
                                    conservative=settings.conservative,
                                    user_lexicon=load_user_lexicon())
            LiveHook(engine, settings,
                     lambda kind, msg: print(f"[{kind}] {msg}", flush=True),
                     accept_injected=args.accept_injected,
                     snippets=SnippetConfig.load(USER_DIR / 'snippets.json')).run()
        return 0
    migrate_user_data()
    root = tk.Tk()
    if args.autostart:
        root.withdraw()
    try:
        gui = TypeBridgeGui(root)
    except (ValueError, OSError) as error:
        root.withdraw()
        messagebox.showerror("TypeBridge: ошибка настроек", str(error), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
