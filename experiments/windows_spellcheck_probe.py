"""Read-only Windows Spell Checking COM probe, independent of TypeBridge runtime.

This file does NOT import or instantiate LiveHook, LayoutMapper or the GUI.
It never adds/ignores words, changes the user's dictionary, injects keys,
loads keyboard layouts, or logs user-typed text. Call on a worker process/thread
that owns its COM apartment; no time guarantees are provided by the OS.
"""

from __future__ import annotations

import sys
import time
from ctypes import POINTER, c_int, c_uint, c_wchar_p

from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from comtypes.client import CreateObject


CLSID_SPELL_CHECKER_FACTORY = GUID('{7AB36653-1796-484B-BDFA-E74F1DB7C1DC}')


class ISpellingError(IUnknown):
    _iid_ = GUID('{B7C82D61-FBE8-4B47-9B27-6C0D2E0DE0A3}')


ISpellingError._methods_ = [
    COMMETHOD([], HRESULT, 'get_StartIndex', (['out'], POINTER(c_uint), 'value')),
    COMMETHOD([], HRESULT, 'get_Length', (['out'], POINTER(c_uint), 'value')),
    COMMETHOD([], HRESULT, 'get_CorrectiveAction', (['out'], POINTER(c_int), 'value')),
    COMMETHOD([], HRESULT, 'get_Replacement', (['out'], POINTER(c_wchar_p), 'value')),
]


class IEnumSpellingError(IUnknown):
    _iid_ = GUID('{803E3BD4-2828-4410-8290-418D1D73C762}')


IEnumSpellingError._methods_ = [
    COMMETHOD([], HRESULT, 'Next', (['out'], POINTER(POINTER(ISpellingError)), 'value')),
]


class ISpellChecker(IUnknown):
    _iid_ = GUID('{B6FD0B71-E2BC-4653-8D05-F197E412770B}')


ISpellChecker._methods_ = [
    COMMETHOD([], HRESULT, 'get_LanguageTag', (['out'], POINTER(c_wchar_p), 'value')),
    COMMETHOD([], HRESULT, 'Check', (['in'], c_wchar_p, 'text'),
             (['out'], POINTER(POINTER(IEnumSpellingError)), 'value')),
    # Only read-only methods used in the experiment are defined. Interface
    # order is preserved: additional vtable slots intentionally not invoked.
]


class ISpellCheckerFactory(IUnknown):
    _iid_ = GUID('{8E018A9D-2415-4677-BF08-794EA61F94BB}')


ISpellCheckerFactory._methods_ = [
    # Enumeration omitted from this experiment; preserve its vtable slot.
    COMMETHOD([], HRESULT, 'get_SupportedLanguages',
             (['out'], POINTER(POINTER(IUnknown)), 'value')),
    COMMETHOD([], HRESULT, 'IsSupported', (['in'], c_wchar_p, 'languageTag'),
             (['out'], POINTER(c_int), 'value')),
    COMMETHOD([], HRESULT, 'CreateSpellChecker', (['in'], c_wchar_p, 'languageTag'),
             (['out'], POINTER(POINTER(ISpellChecker)), 'value')),
]


class WindowsSpellProbe:
    def __init__(self):
        if sys.platform != 'win32':
            raise RuntimeError('Windows is required')
        self.factory = CreateObject(CLSID_SPELL_CHECKER_FACTORY,
                                    interface=ISpellCheckerFactory)
        self.checkers = {}

    def supported(self, tag: str) -> bool:
        return bool(self.factory.IsSupported(tag))

    def checker(self, tag: str):
        if tag not in self.checkers:
            if not self.supported(tag):
                raise ValueError(f'Spell Checking API does not support {tag}')
            self.checkers[tag] = self.factory.CreateSpellChecker(tag)
        return self.checkers[tag]

    def check(self, tag: str, word: str) -> tuple[bool, float]:
        """Return (no-spelling-errors, elapsed_ms). Does NOT imply a valid word.

        Important: some spellers ignore unsupported scripts, digits or tokens.
        Callers must separately validate script; 'no errors' is not an
        affirmative language-identification signal.
        """
        if not word:
            raise ValueError('Empty words are forbidden by the COM API')
        checker = self.checker(tag)
        t0 = time.perf_counter_ns()
        errors = checker.Check(word)
        first = errors.Next()
        ms = (time.perf_counter_ns() - t0) / 1e6
        return not bool(first), ms


if __name__ == '__main__':
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
        instance = WindowsSpellProbe()
        for locale, samples in {
            'ru-RU': ('привет', 'приввет', 'hello'),
            'en-US': ('hello', 'helllo', 'привет'),
            'he-IL': ('שלום', 'שלללום', 'hello'),
        }.items():
            supported = instance.supported(locale)
            print(f'{locale}: supported={supported}')
            if supported:
                for sample in samples:
                    accepted, elapsed = instance.check(locale, sample)
                    print(f'  {sample!r}: no_errors={accepted}, ms={elapsed:.3f}')
    except Exception as error:
        print(f'Spell Checking COM probe failed: {type(error).__name__}: {error}',
              file=sys.stderr)
        raise SystemExit(1)
