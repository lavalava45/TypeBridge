# R16 language package schema v1 (R16/3a)

> **Актуальный архитектурный статус R16/4e (29.10.2026):** файловые
> install/update/remove/restore используют crash-recovery journal,
> межпроцессную блокировку и строгую проверку состояния на следующем запуске;
> policy engine предоставляет необязательную точную диагностику отказов.
> Подробности и ограничения:
> [R16/4e — восстановление и диагностика](R16-4E-RECOVERY-AND-DIAGNOSTICS.md).
> Ниже сохранены исторические контракты v1 и этапы перехода. Упоминания
> «только модельного engine» описывают состояние **на R16/3b**, а не
> актуальный интегрированный source runtime. Работающие EXE не обновлялись.

> **R16/4d — актуальный порядок для новых исходников:** RU/EN/HE также
> поставляются как строгие, полностью декларативные **v2 language packs**.
> Их manifest, словари, дополнения и полные лицензии находятся в
> `bundled_language_packs/`. Пользователь может установить, обновить,
> отключить, архивно удалить или восстановить внешний пакет в GUI, но
> заменять или удалять **выбранный либо используемый hook язык запрещено**.
> После операций каталог перечитывается только при полном перезапуске.
> В settings.json сохраняется дополнительное поле `language_shortcuts` с
> парами `[language_code, shortcut]`, включая отсутствующие и выключенные
> языки, а `settings_languages.json` остаётся v1 для старых EXE.
> Формат v1 и старый EXE остаются совместимыми. Примеры и ограничения:
> [R16/4d](R16-4D-DECLARATIVE-PACKAGES.md). Всё это пока в исходниках —
> работающие EXE не были пересобраны.
>
> `he_custom.txt` — особый редактируемый *источник слов*, не файл для
> публикации из локального каталога разработчика: базовый HE-пакет содержит
> только воспроизводимый публичный текст. Изменения локального
> `data/he_custom.txt` остаются вне пакета; для него в v2-манифесте
> используется `editable_source=true`. Установка/обновление пакетов при
> нескольких запущенных экземплярах TypeBridge запрещена.

> **Обновление R16/4c (исходники, не выпущенная сборка):** v1 не отменён,
> но новый `LanguageEngine` уже принимает установленные data-only v2 пакеты
> через единый loader/decision policy. В GUI исходной версии есть установка
> ZIP, добавляющая пакет **только после полного перезапуска**. При выборе
> нового набора требуются уже доступные Windows HKL; максимум три активных,
> остальные установленные пакеты и их личные слова сохраняются.
> Старые RU/EN/HE пока обслуживаются compatibility adapter из `dist/data`,
> хотя уже проходят через общий интерфейс. Инструкции и честные ограничения:
> [R16/4c — единый runtime](R16-4C-UNIFIED-RUNTIME.md). Работающий EXE
> не пересобирался в этом этапе, и его поведение остаётся прежним.

**Implementation status:** validated metadata and selected-lexicon v2 cache
(R16/3a–3b/1), dynamic engine (R16/3b/2), source-level Settings/GUI/LiveHook
integration (R16/3b/3) and reversible language profiles, indicators and
integrated model acceptance (R16/3b/4).
`language_pack_loader.py` validates source files, `language_lexicon.py` reads
selected dictionaries and `LanguageEngine(..., pack_root=..., active_languages=...)`
can use them with description-based script, priority and already-loaded HKL
mapping. Early indices follow the same active set. The source GUI now discovers
installed verified packs and exposes their selection and optional shortcuts;
LiveHook resolves observed LANGIDs from the engine catalog. Language-set
changes only take effect after a complete application restart. **This has not
been built or physically tested.** The installed EXE does not support external
languages yet.

## Architecture contract

- Any number of language packages may be *installed/discovered*.
- **One to three** different languages may be *active*; the rest are excluded
  from recognition, tie-breaking and the manual cycle. The selection preserves
  user order. Built-in legacy profiles default to RU/EN/HE unchanged.
- User-facing language selection, interface language and personal dictionaries
  remain separate concepts.
- All source words and license terms must be suitable for redistribution before
  a pack is distributed. No third-party dictionary is bundled by this stage.
- The runtime must never download, import Python modules, execute scripts,
  modify input methods or install Windows keyboard layouts when reading packs.

## Directory format

Each package is a normal directory with a lowercase language code matching
`[a-z]{2,3}(?:-[a-z]{2,4})?`. For example:

```text
language_packs/
  README.md
  ar/
    manifest.json
    words.txt
    confident.txt
    LICENSE.txt
```

`dictionary_packs/` is a **different feature** (themed word lists) and is not
read by this loader. Current installed packages contain only `.txt` / `.md`
payloads and `manifest.json`. ZIP files, symlinks, junctions, nested folders,
hidden/executable files and undeclared payloads are rejected.

Example *structure* (the values `SHA256_OF_*` and sizes must be replaced with
the actual lowercase SHA-256 and UTF-8 byte lengths of the selected, licensed
dictionary files before this will validate):

```json
{
  "schema_version": 1,
  "package_version": "1.0.0",
  "code": "ar",
  "name": "Arabic",
  "langid": 1025,
  "unicode_script": "ARABIC",
  "alphabet": "ابتثجحخدذرزسشصضطظعغفقكلمنهوي",
  "normalization": "casefold",
  "dictionary_file": "words.txt",
  "confident_file": "confident.txt",
  "additions": [],
  "alternatives": ["ru", "en"],
  "source": {
    "name": "Name of the permitted dictionary source",
    "url": "https://example.org/source",
    "license_id": "CC0-1.0",
    "license_file": "LICENSE.txt"
  },
  "files": {
    "words.txt": {"bytes": 100, "sha256": "SHA256_OF_WORDS"},
    "confident.txt": {"bytes": 50, "sha256": "SHA256_OF_CONFIDENT"},
    "LICENSE.txt": {"bytes": 60, "sha256": "SHA256_OF_LICENSE"}
  }
}
```

The example is **not** a real licensed Arabic dictionary or usable manifest:
the source, file sizes and checksums are illustrative. `langid` is the decimal
value of the existing Windows language identifier, not a request to load an
input method. The `alphabet` uses Unicode letters from the declared writing
system; extra script-specific normalization remains future work. Format v1
deliberately supports only `normalization: "casefold"`; unsupported rules fail
validation rather than silently returning incorrect corrections.

`additions` may contain objects such as:

```json
{"filename": "extra.txt", "for_words": true, "for_confident": false}
```

Each additional filename must also be present in `files` with its own exact
size and checksum. The `alternatives` list records language-code priority
metadata; it cannot contain duplicates or the package's own code.

## Validation and safe usage

```powershell
python -m language_pack_loader --root language_packs
```

The command reads and validates the packages and prints a catalog, with the
notice that external packages are **not enabled**. It neither changes
`settings.json` nor starts TypeBridge. An invalid installed package fails the
catalog validation explicitly (nonzero exit), instead of silently falling back
to a wrong dictionary.

The validator checks JSON version and exact schema; duplicate fields; code
and directory agreement; language names, LANGID, Unicode script and alphabet;
source/license metadata; file names, duplicate files, byte sizes, SHA-256,
UTF-8 encoding and nonempty main/confident dictionaries. The manifest is
limited to 64 KiB, text dictionaries to 64 MiB each and the license to 1 MiB.
It refuses symlinks/junctions, path traversal, executable or unlisted files,
and any attempt to override RU/EN/HE or reuse an existing LANGID.

For programmatic use:

```python
from pathlib import Path
from language_pack_loader import available_language_catalog
from language_registry import validate_active_languages

catalog = available_language_catalog(Path('language_packs'))
selected = validate_active_languages(('ru', 'en', 'ar'), catalog)
```

The last line **validates a planned selection only**. It does not make Arabic
operational in the current `LanguageEngine`. An isolated data preparation API
is available for development and integration tests:

```python
from language_lexicon import load_selected_lexicon

snapshot, status = load_selected_lexicon(
    Path('data'), Path('language_packs'), ('ru', 'ar'),
    cache_dir=Path('build/dev-language-cache'),
)
# snapshot['words'], snapshot['confident'], snapshot['exclusions']
# status: 'hit', 'built', 'uncached' or 'disabled'
```

It reads only the selected built-in language files, verified external package
files and the shared `exclude.txt`. The legacy Russian ё/е, Hebrew supplement,
word additions and English single letters remain unchanged; a mixed RU/AR
snapshot need not load missing HE/EN dictionary files. For v1 external packs,
words are strictly Unicode letters of the declared script and casefold-only.
The first real Arabic and Latin packs still require language-specific review.

The v2 cache is named `words-v2-<selection-id>.cache`, bound to every source
file's content, logical path, package manifest and policy. It uses the same
bounded, checksummed, JSON-only format principles as v1 but never opens or
overwrites `words-v1.cache`. Corruption rebuilds safely; deleted or changed
source files invalidate the snapshot or stop preparation.

The default `LanguageEngine` continues using its original full RU/EN/HE
loading and v1 cache. For *model integration only*, an opt-in engine exists:

```python
from engine import LanguageEngine

model_engine = LanguageEngine(
    Path('data'), active_languages=('en', 'ar'),
    pack_root=Path('language_packs'), cache_dir=Path('build/dev-language-cache'),
)
# Call only with a deliberately mocked LayoutMapper in automated tests.
# Real initialization can bind existing HKLs and must not run unattended.
```

Without `pack_root`, external codes still fail validation, and old profiles
retain their RU/EN/HE engine and cache. When `pack_root` is supplied, it must
have an explicit 1–3-language set. Failures in pack validation stop startup
before constructing the mapping. The external-v1 normalization remains
casefold-only; legacy RU/EN/HE retain their historical normalization.

This source-level model passes synthetic full-word and early suggestions for
Arabic and Spanish without modifying any editor. Ambiguous targets or known
source-language words are rejected conservatively. `GetKeyboardLayoutList` and
`ToUnicodeEx` are mocked in tests; genuine Windows/IME behavior and the No01
physical-input acceptance gate remain unverified. R16/3b/3 now provides the
source-level personal-word, GUI, direct-shortcut and context integrations,
but production acceptance is still blocked by physical-input/HKL testing.

The main `user_lexicon.json` intentionally remains **version 1** so older
TypeBridge versions can still read it. External personal words and exclusions
are stored in the separate `user_lexicon_packages.json` sidecar, validated
against the installed package catalog and preserved across selection changes.
If the sidecar contains unavailable or invalid languages, the new application
fails safely rather than offering to erase the old personal dictionary.

## Reversible language settings and indicators (R16/3b/4)

For a future version with external packages, the portable profile contains:

- `settings.json`: legacy-compatible normal settings; `active_languages` is
  **only** its active built-in subset or RU/EN/HE if no built-in is active;
  `direct_extra` is empty. The old EXE can still load this file unchanged.
- `settings_languages.json`: versioned sidecar with the **complete** selected
  1–3 language codes and additional direct hotkeys. It is validated against
  the installed catalog, uses UTF-8 and atomic single-file replacement, and
  refuses unknown codes, duplicate JSON keys, symlinks and invalid schemas.
- `user_lexicon.json` remains v1 and `user_lexicon_packages.json` keeps
  external personal words, including those for currently inactive languages.

Returning from EN+AR or EN+ES to RU/EN/HE retains disabled-language shortcuts
and personal data. `settings_languages.json` remains the new app's authority;
the old app only understands the compatible legacy subset. The two files are
not replaced atomically at the filesystem level. **Current source code** uses
`settings_pair_transaction.json` to record SHA-256 hashes and full old file
contents before writing, and recovers both files on the next read if a crash
interrupts the commit. An already completed two-file commit is kept; a partial
commit rolls back to the previous pair. If either file has been independently
modified since the crash, recovery refuses to overwrite it automatically.
New UI language, journal and caret settings use the same catalog-aware save.
If a previously active language pack is missing, the new app
fails closed; it does not erase that language's settings or personal words.

For AR/ES the tray, taskbar icon and caret indicator show compact ASCII
language-code tiles rather than national flags, and use a neutral fallback
for unknown or inactive layouts. The separate, non-activating caret helper
process receives only the validated active code/LANGID map; it never receives
typed text or changes the system's foreground window.

The model acceptance uses `fixtures/r16-demo-packs/ar` and `es`. These are
**tiny test vocabularies**, not general Arabic or Spanish language coverage.
Verify them without installing anything:

```powershell
python fixtures/generate_r16_demo.py
python -m language_pack_loader --root fixtures/r16-demo-packs
python -m unittest -q test_r16_end_to_end test_r16_indicators
```

After building a new compatible EXE and **explicitly exiting TypeBridge**, a
tester can place the validated `ar` or `es` folder inside `language_packs/`
beside the EXE. TypeBridge must then be restarted and an appropriate Windows
HKL must already be present. Installing another pack requires **no Python-code
change or EXE rebuild**, only the validated data and a restart. Do not copy
demo packs into a running application or advertise them as production support.

The complete not-yet-run acceptance matrix is in
[`R16-CONTROLLED-ACCEPTANCE.md`](R16-CONTROLLED-ACCEPTANCE.md).

**Still blocked:** genuine Arabic/Spanish keyboard mappings, RTL/IME input,
physical No01 and Undo with real editors, fast held-key recovery, and proper
licensed full-size dictionaries. Passing model/Win32-mock tests does not prove
these physical guarantees; no release is published at this stage.

## Failed inactive packages and startup I/O (R16 P1/P2)

On startup, the GUI validates installed packages individually. Invalid packs
are excluded from the available list with a diagnostic; a damaged **inactive**
package cannot disable the built-in RU/EN/HE languages. If a user's selected
language is invalid, their settings cannot be validated and startup fails
closed; TypeBridge never silently substitutes another language for the chosen
one. Strict CLI validation (`python -m language_pack_loader`) still reports
invalid packages as an error.

Personal words belonging to unavailable packs remain dormant inside the
personal lexicon; they are never used in corrections or new word additions.
Saving other personal words preserves their entries, allowing the unavailable
language to recover when its verified package is restored.

`LanguageEngine` now verifies only the explicitly selected external language
directories. A verified package description is reused by the lexicon loader
instead of validating the same payload multiple times. The words-v2 cache
still fingerprints the selected sources (including manifests) to invalidate
changes or refuse a tampered warm cache. The full package inventory from the
GUI is not a fourth active correction language.
