# TypeBridge — итоговая матрица тестов, 7 ноября 2026

> **Временной срез:** нижеследующая матрица относится к завершению недельного
> аудита, *до* последующих изменений `typebridge_app.py`, `tray_win32.py` и
> `ui_translations.py` ради ускорения браузера и нового меню трея. Адресные
> регрессии этих правок (275 PASS, 101 подслучай) зафиксированы отдельно в
> `docs/BROWSER-PERFORMANCE-20261107.md`. Их нельзя прибавлять к исторической
> матрице как новые уникальные тесты без повторной инвентаризации.

**Область:** 155 исходных модулей `test_*.py`, отдельные Windows subprocess,
отдельные журналы, запрет глобальных hooks, пользовательского ввода и GUI
foreground. Production-код не изменялся.

Уточнение: последние исправления четырёх `caret_word_recovery`-тестов не меняли production; более ранние подтверждённые production-исправления аудита перечислены в `docs/WEEKLY-CODE-AUDIT-20261107.md`.

## Финальная сверка уникальных тестовых методов и повторов

`docs/test-logs/CONSOLIDATED-METHOD-INVENTORY-20261107.json` — машиночитаемая сверка **всех 155 исторических модулей** с последней подтверждённой версией их own-unittest тестов, 22 private-desktop GUI-модулями, исправленными fixture failures, отдельным словарным прогоном и 3 новыми модулями. Это **не один монолитный запуск**; для каждого исходного модуля указаны число методов, фактический журнал и итоговый статус.

| Срез, без двойного счёта пересекающихся повторов | PASS | Ожидаемый FAIL | Неожиданный FAIL | BLOCKED / TIMEOUT |
|---|---:|---:|---:|---:|
| Собственные тесты исходных 155 модулей (**1539 методов**) | **1538** | **1** | **0** | **0** |
| Дополнительные 3 модуля с тестами (**16 методов**) | **16** | **0** | **0** | **0** |
| **Итого, 158 модулей с тестами, 1555 уникальных методов** | **1554** | **1** | **0** | **0** |

**Единственный ожидаемый FAIL:** `test_p4_readiness_20261031.P4ReadinessTests.test_existing_public_exe_profile_and_hash_agree` — существующая релизная проверка отказывается объявить EXE чистого коммита при `worktree_dirty=True`. Это не «пройденный» тест, а сознательно оставленный блокирующий release gate. Для проверки кода рабочее дерево не очищалось.

**Повторяемые/перекрывающиеся suites (не прибавлять к 1555):**

| Отдельное фактическое исполнение | PASS | FAIL | Подслучаи PASS | Журнал |
|---|---:|---:|---:|---|
| Исходный объединённый десятимодульный набор после исправления | **122** | **0** | **56** | `build/audit-20261107/combined-after-fix-20261107.log` (269,86 с) |
| Native transaction queue + clipboard hotkeys + password fields + browser Pause + UIA + input transport + browser readback | **96** | **0** | **47** | `build/audit-20261107/integration-native-clipboard-uia-20261107.log` (145,85 с) |
| Caret recovery + P0 generic Pause + Pause without KEYUP (отдельные модули) | **42** | **0** | — | `docs/test-logs/unittest-matrix-20261107-215708-7bbfced3/summary.json` |
| Новые 3 модуля, подтверждение фактического числа методов | **16** | **0** | — | `docs/test-logs/unittest-matrix-20261107-220721-731e78d7/summary.json` |
| 22 GUI-модуля на private desktop | **261** | **0** | — | `docs/GUI-MATRIX-EXPANDED-20261107.md` (входит в исходные 155) |
| Реальный словарный `test_engine_user_lexicon` | **4** | **0** | — | `build/audit-20261107/lexicon-600s-final.log` (243,804 с; входит в исходные 155) |

**Четыре предыдущих RED были воспроизведены самостоятельно:** все четыре strict UIA-кейса `test_caret_word_recovery.py` наследовали `simple_mode=True` от изменённой общей in-memory фикстуры. Исправлена только тестовая конфигурация; добавлен обратный регрессионный тест одного Space в simple-mode без UIA/SendInput. 5/5 адресных green (включая 4 предыдущих красных); после этого **122/122** исходного комбинированного набора PASS. Доказательства: `docs/test-logs/WORKER1-CARET-RECOVERY-20261107.md`. Исторический лог **117 PASS / 4 FAIL** оставлен и не суммируется с новым.

**Не включать в PASS:** физический реальный Chrome/Notepad++/HKL/LL hook/SendInput/Undo, современная Scintilla 5.x, потенциальная запоздалая Win32-запись после timeout. Эти acceptance gates **BLOCKED**, даже если соответствующие mock/unit-тесты зелёные. `SIMPLE_SWITCHER_MODE` сознательно не защищает парольные поля и не включает карантин. Аудит production code и native restrictions отдельно: `docs/WEEKLY-CODE-AUDIT-20261107.md` и `docs/DEEP-NATIVE-AUDIT-20261107.md`.

**Отдельная самопроверка инфраструктуры аудита (не прибавляется к 1555):** `build/audit-20261107/test_audit_ledger_integrity.py` — **5/5 PASS за 4,93 с**. Она сверяет Git-hunks и 36 коммитов, исходники, SHA-256 обеих сборок, пропущенные исполняемые строки (в том числе частично покрытые), соответствие CSV основному отчёту и SHA-256 шести скриптов в игнорируемом `build/` каталоге.

## Итоговая консолидированная проверка (по состоянию на завершение GUI и словаря)

Здесь объединены последняя по-модульная безопасная матрица исходных 155 модулей, отдельный реальный private-desktop Tk/GUI run и 243,804-секундный словарный повтор. **Это не результат одного нового монолитного запуска `unittest discover`**; отдельные логи и версии тестовых фикстур указаны ниже.

| Итоговый статус исходных 155 модулей | Модулей |
| --- | ---: |
| **PASS** (132 из безопасного общего прогона + 22 GUI из private desktop) | **154** |
| **FAIL** — только обязательный gate чистоты Git для release EXE | **1** |
| TIMEOUT | **0** |
| BLOCKED внутри 22 GUI-модулей | **0** |
| **Всего** | **155** |

Полный private-desktop охват всех **261/261** явно инвентаризованных unittest-методов в 22 GUI-модулях: 261 PASS, 0 FAIL, 0 TIMEOUT, 0 BLOCKED. `docs/GUI-MATRIX-EXPANDED-20261107.md`, включая отдельный реальный синтетический DPAPI тест и перезапуск 3 Scintilla/Pause кейсов.

После базовой матрицы появились ещё **5 файлов с префиксом `test_`**: `test_deep_native_browser_pause_20261107` (**11 PASS**), `test_p0_recovery_cancellation_triage_20261107` (**1 PASS**), `test_test_matrix_runner_unit` (**4 PASS**), а `test_matrix_module.py` и `test_matrix_runner.py` — тестовые запускающие программы без собственных `TestCase` (контракт запуска проверен указанными 4 тестами). Отдельный повтор всех трёх модулей: **16 PASS**, `docs/test-logs/unittest-matrix-20261107-220721-731e78d7/summary.json`. **Это не меняет знаменатель исторической матрицы 155**, но входит в итог 158 модулей/1555 методов.

## Историческая базовая матрица до полного GUI-охвата

| Состояние | Модули |
| --- | ---: |
| PASS | **132** |
| FAIL | **1** |
| TIMEOUT, статус неизвестен | **0** |
| GUI_BLOCKED, запуск на активном desktop запрещён | **22** |
| **Всего** | **155** |

Это консолидация первичной матрицы и независимых адресных повторных запусков,
а **не** результат одного повторного `unittest discover` после всех правок.
Новые тесты для самого matrix-runner были добавлены позднее и не входят в
исходные 155 модулей. Собственный `test_test_matrix_runner_unit` — **4/4 PASS**.

## Закрытые RED: подтверждённые устаревшие фикстуры

- `test_audit_unconfirmed_hkl_20261106.UnconfirmedHklTests.test_generic_manual_cycle_does_not_advance_remembered_word`: прежняя фикстура подменяла native HWND на generic Chrome-класс в **strict** режиме и не предоставляла UIA caret witness. Production правильно отказывал **до** SendInput/HKL, поэтому ожидание `SendMessageTimeoutW` было недостижимо. Исправленный тест моделирует реальный **simple-mode generic** путь с принятым пакетом и неподтверждённым HKL, сохраняя проверку неизменности remembered word и `token_blocked`. Добавлен обратный кейс: strict generic без caret proof не отправляет ни SendInput, ни HKL. Весь модуль **4/4 PASS**.
- `test_portable_diagnostics.PortableDiagnosticsTests.test_uncertain_boundary_disables_later_automatic_early_and_manual_edits`: `TrialHook` подменяет `_correct`, а его synthetic identity не проходит настоящий native route. Прямой вызов `LiveHook._correct` теперь снабжён разрешённым native процессом/классом, создаёт реальную `replace_prefix` uncertain write, и проверяет **field-scoped** quarantine, а не устаревший global `native_edit_disabled`. Повторные изменения того же поля блокируются; соседний тест доказывает, что другое поле не заблокировано. Модуль **9/9 PASS**.

Оба отказа сначала воспроизведены в исходном состоянии; после тестовых исправлений воспроизведены 13/13 PASS, журнал `docs/test-logs/worker1-red-green-20261107-203210.log`. Production-код **не менялся**, реальная production-регрессия этими сценариями не установлена.

## Точные незакрытые тесты и гейты

1. **FAIL, release gate:** `test_p4_readiness_20261031.P4ReadinessTests.test_existing_public_exe_profile_and_hash_agree` — проверка `worktree_dirty=False` закономерно красная, пока в рабочем дереве находятся незакоммиченные изменения. Не считать этот гейт пройденным до фактической проверки чистого дерева и профиля артефакта.
2. **Закрыт прежний TIMEOUT 240 секунд:** `test_engine_user_lexicon` — отдельный полный запуск принадлежащих модулю тестов завершился **4/4 PASS за 243,804 с** (`build/audit-20261107/lexicon-600s-final.log`). Причина прежней остановки: лимит 240 секунд недостаточен на 3,804 с; длительность обусловлена повторным созданием реального `LanguageEngine` с крупными словарями и независимыми конфигурациями наложений. Это был медленный прогон, а не зависание; проверки не отключались.

В этой **базовой** матрице 22 модуля с потенциальным `tk.Tk()` были `GUI_BLOCKED`, а не PASS. Позднее все 22 модуля были полностью инвентаризованы и отдельно выполнены в разрешённом private-desktop режиме: **261/261 PASS**. Поэтому интегральная сводка выше содержит 154 PASS / 1 expected release-gate FAIL, но исходные исторические блокировки не стерты из логов.

## Причина прежних 17 минут

Основная измеренная причина — дорогостоящая загрузка реальных словарей:
один тест `test_engine.LanguagePrototypeTests.test_english_typed_under_russian_layout`
занимает **42,8 с**, модуль `test_engine` — **91,6 с**, `test_word_recovery` —
**236,2 с**, `test_early_runtime` — **183,8 с**. Некоторые модули повторно
создавали `LanguageEngine` в `setUp` и импортировали чужие классы `TestCase`
по именам: стандартный loader повторно собирал их тесты. Новый
`test_matrix_module.py` выбирает только классы, принадлежащие самому модулю,
при этом оставляет настоящие наследованные тесты собственных классов.

## Последние проверенные исправления тестов

- **Дополнение (20:37):** два ранее красных native/Scintilla ожидания в
  `test_early_native_edit` признаны устаревшими и исправлены без production
  изменений. **43/43 SAFE PASS**, 1 GUI_BLOCKED (`TrialWindowTests.
  test_opening_withdrawn_trial_does_not_create_or_start_hook`). Повторные
  независимые `Pause` проверяются как отдельные native cycles; выделение
  Notepad++ идёт через `ScintillaSelectionAdapter`, с сохранением соседнего
  текста, без fallback на `SendInput`. Подробно:
  `docs/test-logs/WORKER1-EARLY-NATIVE-20261107.md`.

- `test_word_cycle`: адаптирован in-memory simplified-mode fixture; отдельно
  проверены Pause без KEYUP и F9 с подавлением повтора до KEYUP — **17/17 PASS**.
- `test_early_runtime`: наследует актуализированный in-memory fixture —
  **32/32 PASS**, 183,828 с.
- `test_r16_active_languages`: импорт чужого класса перестал дублировать
  чужие тесты — **7/7 PASS**.
- `test_word_trace`: собственные классы — **12/12 PASS**; прежние падения
  происходили в повторно собранных импортированных классах.
- `test_desktop_editors`: действующий native UIA policy с 21 сохранённым
  контрольным вектором — **10/10 PASS**.
- `test_r18_console_fallback`: действующий native UIA service вместо
  удалённого subprocess — **7/7 PASS**.
- `test_r16_startup_diagnostics`: текущий `simple_mode` контракт — **1/1 PASS**.
- `test_p0_five_findings`: пост-admission abort и fail-closed без caret proof — **7/7 PASS**.
- `test_native_uia_caret_20261107`: исходный одиночный сбой mock spawn не
  воспроизвёлся; повторно весь модуль **25/25 PASS** (природа флуктуации не доказана).
- `test_engine_user_lexicon`: **4/4 PASS**, 243,804 секунды с ненадуманным лимитом; замеренный журнал `build/audit-20261107/lexicon-600s-final.log`.

## Уникальные журналы

Подробности всех первоначальных результатов, отдельные тайм-ауты, имена
непрошедших тестов и продолжительность каждого модуля — в
`docs/test-logs/WORKER1-UNITTEST-MATRIX-20261107.md` и подпапках:

- `unittest-matrix-20261107-193828-588b768b` — 155 модулей, 22 с.
- `unittest-matrix-20261107-194256-640e6122` — 16 повторов, 150 с.
- `unittest-matrix-20261107-195552-555f9be5` — 3 повтора, 240 с.
- `unittest-matrix-20261107-200450-1066f69d` — 5 исправленных модулей, 47 тестов PASS.
- `unittest-matrix-20261107-201226-860b86cb` — 17 WordCycle PASS.
- `unittest-matrix-20261107-201331-94ec79f0` — 32 EarlyRuntime PASS.
- `unittest-matrix-20261107-200802-e9d3f1d5` — первоначальный engine lexicon TIMEOUT 240 с; окончательно закрыт 4/4 PASS отдельным исполнением.

Команда адресного прогона:

```powershell
python test_matrix_runner.py --modules test_word_cycle --timeout 120 --workers 1
```

**Ограничение:** mock/unittest не доказывают корректность физического ввода
в пользовательские редакторы; GUI_BLOCKED и release gate остаются отдельными
условиями приёмки.
