# TypeBridge — аудит изменений кода за 1–7 ноября 2026 года

> **Последующие изменения после зафиксированного ниже аудита:** в исходники
> `typebridge_app.py`, `tray_win32.py`, `ui_translations.py` внесены ускорение
> браузерного пути и флаги в меню. См. `docs/BROWSER-PERFORMANCE-20261107.md`.
> Приведённые ниже хэши EXE и итоговая матрица — исторический проверенный
> **срез до этих новых правок**. Старый preview EXE больше не идентичен
> текущим исходникам. **Последующая штатная сборка** по отдельному запросу
> пользователя успешна: актуальный `dist/TypeBridge.exe` SHA-256
> `862eea2439b67cb4c10aba8d7f04f927ad45b38cde89e19562cffd67811c3114`,
> Diagnostics `726ab62192aaff343c08454a42ce6acac2546c03dacc1895fa7c2d44fada89db`.
> Оба 6/6 self-test PASS; фактическая физическая Chrome-приёмка всё ещё
> **не проведена**. Подробности — в отчёте о производительности.

Область: 36 Git-коммитов 1–7 ноября 2026 года, все незакоммиченные изменения, новые файлы и собственные исправления аудита. Дополнительный реестр **каждого Git-ханка/нового Python-файла/коммитного изменения** с контрактом, смежными тестами, динамическим покрытием и индивидуальным ограничением находится в `docs/WEEKLY-HUNK-EVIDENCE-20261107.csv`. Факт выполнения строки тестом НЕ означает подтверждения отсутствия всех дефектов.

Проверены исходники, обе изолированные (непользовательские) сборки EXE, model/unit-тесты и безопасные реальные Win32-контролы на неактивном desktop. Пользовательский `dist/TypeBridge.exe`, глобальные hooks, пользовательские окна/буфер, физический ввод и исходный SwitchER64 не изменялись и не запускались для испытаний.

## Итоговая сверка после расследования четырёх отказов и исправления учёта

**Контрольный машинный снимок итогов:** `build/audit-20261107/FINAL-VALIDATION-20261107.json` — Git HEAD, категория и число записей CSV, покрытие, уникальные тестовые методы, SHA-256 preview/Public/Diagnostics, отсутствие изменения production и блокированные native gates. Повторный запуск пяти offline-инвариантов реестра после подготовки снимка — **5 PASS, 0 FAIL за 6,03 с**; `git diff --check` — PASS.

**Тестовый результат:** четыре отказа `test_caret_word_recovery.py` воспроизведены отдельно (4 FAIL) и доказаны следствием **устаревшей strict-фикстуры**. `test_word_cycle.WordCycleTests.setUp()` теперь задаёт `simple_mode=True`, а strict UIA-кейс ошибочно наследовал его. Production обоснованно прекращал UIA recovery. В тестовой фикстуре явно установлен `simple_mode=False`, добавлен обратный тест **одного переданного Space без UIA/SendInput/worker** в простом режиме. 5/5 red→green; полный caret-модуль 20/20 PASS; два смежных Pause-модуля 22/22 PASS. **Тот же исходный совместный десятимодульный pytest-набор повторён целиком: 122 PASS, 0 FAIL, 56 дополнительных подслучаев PASS, 269,86 с**, `build/audit-20261107/combined-after-fix-20261107.log`. Исходные 117 PASS / 4 FAIL сохранены как историческое свидетельство; новые 122 PASS не «прибавляются» к ним. Первопричина и первичный RED/соседние прогоны: `docs/test-logs/WORKER1-CARET-RECOVERY-20261107.md`.

**Сводная инвентаризация методов:** первоначальные 155 модулей дают **1539 уникальных собственных тестовых методов** в объединённой последней подтверждённой матрице: **1538 PASS, 1 FAIL — ожидаемый релизный gate `worktree_dirty=False`**. Ещё три новых тестовых модуля добавляют **16 PASS**; два остальных `test_*.py` являются тестовыми runner-скриптами без `TestCase`. Итого **158 модулей с тестами; 1555 отдельных методов: 1554 PASS, 1 ожидаемый FAIL, 0 неожиданных FAIL**. Это *консолидированный учёт раздельных запусков, не один монолитный прогон*. Подтверждающий машинный реестр с именами и журналами каждого модуля: `docs/test-logs/CONSOLIDATED-METHOD-INVENTORY-20261107.json`. В него уже входят **261/261 GUI** и **4/4 `test_engine_user_lexicon` за 243,804 с**. Дополнительно проведён объединённый независимый набор очереди/UIA/clipboard: **96 PASS + 47 подслучаев**; повторные наборы перекрывают эту матрицу и не суммируются с ней.

**Изменения Git:** ровно **36 коммитов** за 1–7 ноября, **286 отдельных коммитных ханков** и **65 файловых кодовых единиц**; на текущем working tree — **477 изменённых Python-ханков**, **51 новый Python-файл** и **169 отдельно инвентаризованных определений** в новых файлах. Всего **1048 Git-единиц**. Ещё **6 скриптов самого аудита** в игнорируемом Git-каталоге `build/audit-20261107` учтены отдельной категорией с SHA-256, итого **1054 записи** в `docs/WEEKLY-HUNK-EVIDENCE-20261107.csv`. Отдельно перечислены все остальные текущие изменённые не-Python файлы и новые документационные материалы. Из 36 коммитов 14 содержат релевантные кодовые изменения; документы остальных перечислены без ложного per-commit runtime PASS. Отдельные **5/5 offline-тестов целостности реестра/хэшей/coverage PASS** (`build/audit-20261107/test_audit_ledger_integrity.py`, 4,93 с), `git diff --check` без ошибок.

**Динамика и границы покрытия:** обновлённая `coverage-focused.json` включает и пять повторно прошедших strict/simple recovery тестов. Среди **646 текущих диапазонов** (477 ханков + 169 новых символов): **311 полностью достигнуты**, **96 достигнуты частично**, **79 имеют исполняемые операторы, не достигнутые ни разу**, **71 не включён в инструментированный набор**, **59 не содержит исполняемых операторов**, **30 относятся к чистому удалению**. В 175 записях есть пропущенные исполняемые строки; это **725 уникальных пар `файл:строка`** с учётом пересекающихся диапазонов. Ранее эти частично пропущенные строки **не были перечислены**, что исправлено в `docs/UNOBSERVED-CODE-REVIEW-20261107.md` и новых полях CSV. Покрытие *не равно успешной функциональной проверке*, а невыполненный код *не равен дефекту*. Изменения проанализированы по контрактам, AST-кандидатам вызова, последствиям и имеющимся журналам; отдельная углублённая ревизия high-risk ветвей описана в `docs/UNOBSERVED-STATIC-WORKER1-20261107.md` и `docs/DEEP-NATIVE-AUDIT-20261107.md`. Для незапускаемых ветвей оставлен `STATIC/BLOCKED`, а не придуманный `PASS`.

**EXE:** текущие production-исходники по 94 SHA-256 из `build/audit-20261107/isolated-frozen-20261107/AUDIT-SOURCE-PROVENANCE.json` совпадают **без единого расхождения**. `dist/TypeBridge-preview-20261107.exe` совпадает по SHA-256 с изолированным Public: `b5c95f4f056979795bbd1549e3488e79b00d4d5114be0030bd2105bc2e7fd488`. Изолированный Diagnostics: `bed6170e0ea39f18fa568cebdc4d4820d66080b088e1cad2e3601f3c360faf85`. PyInstaller-архивы заново проверены: Public 1784 модулей, **без диагностических trace-модулей**; Diagnostics 1791 модуль с ожидаемыми шестью диагностическими компонентами. Обе ранее выполнили bounded self-test (`ok=true`, все шесть RU/EN/HE направлений). Production-код в заключительном исправлении четырёх тестов не менялся, поэтому новое копирование/пересборка не требовались. Основной пользовательский `dist/TypeBridge.exe` **оставлен нетронутым** и отличается от preview по SHA — не считать его проверенной текущей версией.

**Приёмка:** статус **предварительная сборка, НЕ проверенный релиз**. Физическая цепочка `WH_KEYBOARD_LL → HKL до Space → native/SendInput → editor undo` в реальных пользовательских Chrome и Notepad++ не принималась. Отдельный private Win32 RichEdit подтвердил замену, соседний текст, Undo/Redo и повторный цикл; но тот же native fixture показал, что после `SendMessageTimeout` возможно **запоздалое изменение исходного поля**, несмотря на отказ/тайм-аут — остаётся no-retry и явный риск. Два исторических падения Notepad++ из-за старой ABI `SCI_GETTEXTRANGE 2162` воспроизведены ранее; исходник исправлен на `SCI_GETTEXTRANGEFULL 2039`, но реальная Scintilla 5.2.3+ физически не принята; установленная старая 4.4.6 правильно возвращает `selection-incomplete-range`. На private desktop собственные Notepad++ и Edge не создали доступное тестовое окно; реальные DOM/UIA и Undo остаются **BLOCKED**, не PASS. В `SIMPLE_SWITCHER_MODE` защита пароля и карантин сознательно **не включены**. Ожидаемое падение dirty Git release gate сознательно **не устранялось очисткой дерева**.

## Обнаруженные проблемы и выполненные исправления

- Исправлен функциональный дефект `clipboard_hotkeys.py`: во время очередного обновления метаданных сохраняется уже подтверждённый контекст в пределах исходного TTL 200 мс; при каждом прежнем обновлении (интервал 50 мс) горячая клавиша могла пропасть. Добавлены проверки истечения срока, отказов, изменения фокуса и зависшего чтения.
- Исправлено ложное утверждение интерфейса в `settings_copy.py` и `ui_translations.py`: теперь явно указано, что в простом режиме проверка полей паролей отключена. **Защитные проверки не включались обратно**.
- Тесты приведены к актуальным интерфейсам: `test_clipboard_selection.py` использовал удалённый PowerShell, `test_build_profiles.py` не учитывал пять новых UIA-модулей, `test_punctuation_runtime.py` ожидал результат простого Pause от строгого тестового объекта. Добавлены проверки обоих режимов.
- Исправлен диагностический эксперимент `experiments/r10_selection_reuse_audit_20261107.py`: вместо PowerShell моделируется нативный UIA и передаётся `focus`. Синтетический прогон выполнен, префикс и суффикс сохранены.
- **N-01, подтверждённое исключение COM-помощника:** `UiaService.request()` прежде пропускал `RuntimeError` из `Process.start` и оставлял незапущенный дочерний объект. Исправлен закрытый отказ, освобождение IPC-каналов и восстановление после ошибки запуска; регрессионный тест добавлен в `test_native_uia_caret_20261107.py`, провален до и пройден после исправления.
- **B-01, подтверждённая порча текста в simple browser Pause:** при отказе `read_selected_word()` код мог заменить `PREFIX ch, SUFFIX` на `PREFIבית SUFFIX`, потому что fallback отправлял Backspace в неизвестное выделение. Исправлено в `typebridge_app.py`, `caret_word_recovery.py`, `uia_native_worker.py`: требуется повторяемый read-only UIA collapsed-caret proof с Runtime ID, прежним текстом/суффиксом/смещением перед первым и непосредственно перед последним `SendInput`. `test_deep_native_browser_pause_20261107.py` проверяет Chrome/Firefox, отказ, сохранность префикса, несколько пробелов, изменённое выделение/Runtime ID и A→B→A. Simple-mode password/карантин **не восстановлены**.
- **Устаревшие P0-тесты, не дефект production:** `test_p0_five_findings.py` раньше перехватывал `gate.capture` при восстановлении, но текущая очередь атомарно использует `gate.begin(recovery_events=...)`; тест исправлен на отмену после admission и сохранность снимка. Старый browser fixture также требовал `SendInput` без подтверждения выделения; теперь он утверждает безопасный отказ. Добавлен независимый `test_p0_recovery_cancellation_triage_20261107.py`.

## Ограничения достоверности

- Просмотр изменений и прохождение unit/integration-тестов не доказывают *отсутствие любых новых багов*. Не проводилась физическая приёмка набора Windows, Chrome DOM и собственного реального Notepad++-процесса. Собранные EXE проверены только на упаковку, безопасный self-test и хэши исходников, НЕ на интерактивный hook.
- `SIMPLE_SWITCHER_MODE=True` сознательно отключает защиту парольных полей и карантин. Generic Pause использует неатомарный пакет SendInput без подтверждения реального результата в редакторе. Изменение фокуса/выделения при отправке остаётся риском; откаты или password guard не добавлялись.
- Старые отдельные команды Scroll Lock и Pause объединены в одну клавишу переключения слова (по умолчанию Pause). Миграция сохранённых настроек намеренная, но не прошла физическую пользовательскую приёмку.
- При неопределённом фокусе или отказе провайдера хоткеи поиска/буфера пропускаются в целевое приложение. Неразрешимое восстановление задержанного ввода также может быть отклонено.
- Real Scintilla `selection-incomplete-range`: изолированный probe загрузил Pythonwin `scintilla.dll` 4.4.6, у которой НЕТ `SCI_GETTEXTRANGEFULL(2039)` (появился в 5.2.3). Адаптер корректно отверг неполный ответ, **его не изменяли ради теста**. Отдельный Notepad++ 8.9.8.1 на private desktop не создал доступного дочернего окна; реальная интеграция с поддерживаемой Scintilla 5.2.3+ пока не установлена.
- Браузерный Edge на private desktop также не создал окна. Проверка нового browser Pause выполнена на синтетическом редакторе и настоящих mock COM вызовах; не на Chrome DOM. Последний разрыв между UIA и `SendInput` неатомарен и остаётся риском.

## Результаты по подсистемам

| Source changes | Review and evidence | Remaining gate |
|---|---|---|
| `typebridge_app.py`, `word_cycle.py`, `engine.py`, `language_decision_v2.py`, `caret_word_recovery.py`, `pipeline_probe.py` | Contract and call-site review of Pause before/after Space, repeated Pause without KEYUP, punctuation-key ambiguity, HKL transitions and early correction. B-01 reproduced red→green, 11 focused Chrome/Firefox/UIA tests plus 79 independent neighboring tests passed. Real isolated RichEdit test covered 6 native cases. | Real physical typing in Chrome/Notepad++ and non-atomic SendInput/DOM outcome not proven |
| `native_transaction_queue.py`, `clipboard_selection.py`, `clipboard_tools.py`, `clipboard_recovery.py`, `password_fields.py`, `browser_readback.py` | Ownership, cancellation, clipboard restore, guarded reads and failure branches reviewed with 118 focused native/clipboard tests passing | Unreproducible native callback races and actual clipboard formats remain unverified |
| `uia_native_worker.py`, `uia_password_native.py`, `uia_selection_native.py`, `scintilla_selection.py`, `selection_cycle.py`, `early_native_harness.py`, `early_scintilla.py` | Исключение `Process.start` исправлено red→green. Реальный COM child запускается и повторно используется; 240 native/clipboard тестов + 131 подслучай прошли. `SCI_SETSEL` верно задаёт `(anchor,caret)`. Несовместимость старой DLL подтверждена, не замаскирована. | Native provider COM timing, Scintilla 5.2.3+ real selection and Notepad++ Undo/Redo integration require acceptance |
| `clipboard_hotkeys.py`, `tray_layouts.py`, committed `caret_indicator.py`/`caret_process.py` | Intermittent 50 ms hotkey availability defect corrected with 200 ms lease; 21 hotkey tests, 16 subtests passed. MSAA timeout/caret worker paths reviewed | Physical shortcut and caret indicator acceptance open |
| `quick_search.py`, `direct_hotkeys.py`, `tray_win32.py`, `build_profiles.py`, `ui_translations.py`, `settings_copy.py` | Timeout/token/clipboard guards, shortcut conflicts, tray routing and simple-mode warnings reviewed; 45 post-fix tests passed. Actual Public/Diagnostics frozen PYZ inventories checked with 1784/1791 modules and all UIA dependencies. | Interactive GUI and real hotkeys still require independent acceptance |
| New `experiments/*.py`, archived caret snapshot, tests and fixtures | All 12 newly examined experimental/archived modules parsed and import-time launch paths reviewed; 13 experiment tests passed, stale `r10_selection_reuse_audit_20261107.py` repaired and synthetic run completed | Live native experiments intentionally not launched |

**Interpretation of the hunk list below:** это навигационный перечень всех текущих изменений, а **не независимые доказательства отсутствия багов по каждому ханку**. Расширенная CSV-матрица различает выполненную строку, невыполненную строку, ошибку теста и непроверенный live-сценарий. Там, где не найдено воспроизведение, правильный вывод — *в выполненных проверках регрессия не продемонстрирована*, но не *регрессии невозможны*.

## Коммиты недели — полный список

| Commit | Date | Subject | Code paths touched |
|---|---|---|---|
| `c5c0950` | 2026-11-05 | docs(release): verify alpha.8 assets and record live field/runtime limitations | Documentation only |
| `59bbc79` | 2026-11-05 | docs(release): prepare alpha.8 changelog and explicit acceptance limits | Documentation only |
| `6195213` | 2026-11-05 | docs: record verified MSAA isolation build and remaining acceptance limits | Documentation only |
| `a2d1e0b` | 2026-11-05 | fix(caret): isolate stalled MSAA providers without disabling native carets | `caret_indicator.py`, `caret_process.py`, `test_caret_process.py`, `test_caret_provider_isolation.py` |
| `a36e51d` | 2026-11-05 | docs: record verified hotkey build and remaining alpha acceptance gates | Documentation only |
| `7ffc214` | 2026-11-05 | docs: sync reliability roadmap and record focused safety checks | Documentation only |
| `dc75908` | 2026-11-05 | test(safety): preserve opt-in addressed transport and lifetime counterexamples | `early_native_edit.py`, `experiments/r04_addressed_richedit.py`, `experiments/r04_private_richedit_windows_probe.py`, `test_r04_addressed_prototype.py`, `test_r04_native_target_lease.py` |
| `ea3c957` | 2026-11-05 | fix(hotkeys): isolate clipboard hooks and prepare target metadata off callback | `clipboard_hotkeys.py`, `clipboard_selection.py`, `test_app_exclusions.py`, `test_clipboard_hotkeys.py`, `test_clipboard_selection.py`, `test_hotkey_context.py`, `test_quick_search.py`, `test_r18_console_fallback.py`, `tray_layouts.py`, `tray_win32.py` |
| `cc4e9b1` | 2026-11-05 | fix(caret): bound helper retries and preserve worker across focus changes | `caret_indicator.py`, `caret_process.py`, `test_caret_indicator.py`, `test_caret_process.py`, `typebridge_app.py` |
| `d38b122` | 2026-11-05 | docs: record verified R02 local build and acceptance limits | Documentation only |
| `0b98d16` | 2026-11-05 | fix(input): recover verified caret words after lost tracking | `caret_word_recovery.py`, `test_caret_word_recovery.py`, `test_p0_guard_transport_integration.py`, `test_r04_default_text_target.py`, `typebridge_app.py`, `ui_translations.py` |
| `d5eb3dc` | 2026-11-04 | docs: record audited input fixes and verified local build | Documentation only |
| `83cdf63` | 2026-11-04 | fix(input): harden replay ownership, password barriers and search deadlines | `clipboard_selection.py`, `input_transport.py`, `native_transaction_queue.py`, `quick_search.py`, `test_app_controls.py`, `test_clipboard_selection.py`, `test_hook_cycle_offhook.py`, `test_native_transaction_queue.py`, `test_p0_five_findings.py`, `test_quick_search.py`, `test_r03_replay_state.py`, `test_r04_default_text_target.py`, `typebridge_app.py`, `ui_translations.py` |
| `ca4becf` | 2026-11-03 | fix(input): preserve modifier releases and sync project status | `native_transaction_queue.py`, `test_native_transaction_queue.py`, `test_offhook_native.py`, `typebridge_app.py` |
| `9b19a30` | 2026-11-03 | fix(tray): shorten scoped correction warning and expire menu notice | `test_app_controls.py`, `test_tray_recovery.py`, `tray_win32.py`, `typebridge_app.py` |
| `3ae3e20` | 2026-11-03 | Merge remote-tracking branch 'origin/main' | Documentation only |
| `992bf6e` | 2026-11-03 | docs: track TypeBridge outreach and completed GitHub SEO | Documentation only |
| `f7f0de2` | 2026-11-03 | docs: normalize roadmap priority table and remove stale test-count claim | Documentation only |
| `0c97b0e` | 2026-11-03 | docs: sync current alpha.7 status and remove stale active CI blockers | Documentation only |
| `a1308bd` | 2026-11-03 | docs: sync roadmap and R20 with released alpha.7 and current P0 priorities | Documentation only |
| `f829a50` | 2026-11-03 | docs: archive outdated TypeBridge status before alpha.7 refresh | Documentation only |
| `324e190` | 2026-11-01 | docs: explain TypeBridge engineering challenges in English and Russian | Documentation only |
| `db8e5af` | 2026-11-01 | Prepare alpha.7 release notes and update verified CI status | Documentation only |
| `13acca0` | 2026-11-01 | Canonicalize R16 test roots and localize journal browser scope | `test_r16_4g_zip_confinement.py`, `test_r16_package_transactions.py`, `ui_translations.py` |
| `243cc52` | 2026-11-01 | Canonicalize R16 fault injection destination on Windows runners | `test_r16_4f_transactions.py` |
| `1a474aa` | 2026-11-01 | Keep R16 fixture payloads LF on Windows and install schema test dependency | `requirements-build.txt` |
| `f844e40` | 2026-11-01 | Merge main documentation and record R15 CI blockers and browser Pause retest | Documentation only |
| `9e3c091` | 2026-11-01 | Clarify journal browser limits, refresh language banner and normalize CI paths | `journal_gui.py`, `test_portable_diagnostics.py` |
| `90c5ca1` | 2026-11-01 | Clarify reported Pause-cycle failure separately from dictionary ambiguity | Documentation only |
| `2632011` | 2026-11-01 | Clarify reported Pause-cycle failure separately from dictionary ambiguity | Documentation only |
| `fb0584f` | 2026-11-01 | Sync development documentation to main without merging application code | Documentation only |
| `10929dc` | 2026-11-01 | Refresh current status roadmap changelog and development guides | Documentation only |
| `2cddb9e` | 2026-11-01 | Separate pre-freeze dictionary checks from installed local build readiness | `test_p4_readiness_20261031.py` |
| `a28e349` | 2026-11-01 | Clarify settings and language indicators and add a persistent optional event panel | `settings_copy.py`, `test_settings_copy.py`, `test_word_cycle.py`, `typebridge_app.py`, `ui_translations.py` |
| `2477b90` | 2026-11-01 | Record successful user ordinary-password-ordinary field trial | Documentation only |
| `0bd9b8e` | 2026-11-01 | Record user Word acceptance and prepare offline password-field trial | Documentation only |

## Незакоммиченный Python-код — все Git-ханки на текущий момент

Точное текущее число: **477** фрагментов. CSV `docs/WEEKLY-HUNK-EVIDENCE-20261107.csv` фиксирует для каждого фрагмента контракт/call sites, потенциальные тесты, исполнение новых строк в целевом coverage-прогоне и ограничения. Недостаточное/нулевое покрытие — не `PASS` и не доказательство бага.

| Файл | Новая строка | Область | Динамическое покрытие фрагмента |
|---|---:|---|---|
| `browser_readback.py` | 6 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `browser_readback.py` | 7 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `browser_readback.py` | 8 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `browser_readback.py` | 12 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `browser_readback.py` | 18 | `CaretReader` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `browser_readback.py` | 20 | `CaretReader` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `browser_readback.py` | 24 | `CaretReader.warm` | STATEMENTS_PARTIAL 1/2 |
| `browser_readback.py` | 28 | `CaretReader.read` | STATEMENTS_PARTIAL 7/8 |
| `browser_readback.py` | 40 | `CaretReader.close` | STATEMENTS_COVERED 1/1 |
| `browser_readback.py` | 43 | `CaretReader.cancel` | STATEMENTS_COVERED 1/1 |
| `build_profiles.py` | 69 | `verify_frozen_profile` | STATEMENTS_COVERED 4/4 |
| `caret_word_recovery.py` | 7 | `<module>` | STATEMENTS_COVERED 2/2 |
| `caret_word_recovery.py` | 16 | `CaretWord` | STATEMENTS_COVERED 2/2 |
| `caret_word_recovery.py` | 22 | `<module>` | STATEMENTS_PARTIAL 37/44 |
| `caret_word_recovery.py` | 120 | `parse_caret_word` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `caret_word_recovery.py` | 122 | `parse_caret_word` | STATEMENTS_COVERED 12/12 |
| `caret_word_recovery.py` | 136 | `parse_caret_word` | STATEMENTS_COVERED 2/2 |
| `caret_word_recovery.py` | 144 | `parse_caret_word` | STATEMENTS_COVERED 1/1 |
| `caret_word_recovery.py` | 146 | `parse_caret_word` | STATEMENTS_PARTIAL 10/11 |
| `caret_word_recovery.py` | 161 | `parse_caret_word` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `caret_word_recovery.py` | 165 | `parse_caret_word` | DELETION_NOT_DIRECTLY_COVERABLE |
| `caret_word_recovery.py` | 171 | `parse_caret_word` | STATEMENTS_COVERED 1/1 |
| `caret_word_recovery.py` | 174 | `read_caret_word` | STATEMENTS_COVERED 2/2 |
| `caret_word_recovery.py` | 179 | `read_caret_word` | DELETION_NOT_DIRECTLY_COVERABLE |
| `caret_word_recovery.py` | 181 | `read_caret_word` | STATEMENTS_PARTIAL 8/9 |
| `caret_word_recovery.py` | 196 | `read_caret_word` | STATEMENTS_PARTIAL 1/2 |
| `clipboard_hotkeys.py` | 137 | `ClipboardHotkeys._refresh_context` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `clipboard_hotkeys.py` | 142 | `ClipboardHotkeys._refresh_context` | STATEMENTS_COVERED 1/1 |
| `clipboard_hotkeys.py` | 153 | `ClipboardHotkeys._refresh_context` | STATEMENTS_COVERED 1/1 |
| `clipboard_hotkeys.py` | 155 | `ClipboardHotkeys._refresh_context` | STATEMENTS_COVERED 3/3 |
| `clipboard_selection.py` | 89 | `copy_transaction` | STATEMENTS_COVERED 1/1 |
| `clipboard_selection.py` | 122 | `NativeCopy.__init__` | STATEMENTS_PARTIAL 1/3 |
| `clipboard_selection.py` | 131 | `NativeCopy.__init__` | STATEMENTS_UNOBSERVED 0/2 |
| `clipboard_selection.py` | 134 | `NativeCopy.__init__` | STATEMENTS_UNOBSERVED 0/2 |
| `clipboard_selection.py` | 140 | `NativeCopy.close` | STATEMENTS_PARTIAL 20/24 |
| `clipboard_selection.py` | 203 | `NativeCopy.snapshot` | STATEMENTS_COVERED 1/1 |
| `clipboard_selection.py` | 207 | `NativeCopy.snapshot` | STATEMENTS_COVERED 3/3 |
| `clipboard_selection.py` | 216 | `NativeCopy.snapshot` | STATEMENTS_COVERED 1/1 |
| `clipboard_selection.py` | 304 | `NativeCopy.copy` | STATEMENTS_PARTIAL 13/14 |
| `clipboard_selection.py` | 325 | `NativeCopy.restore` | STATEMENTS_COVERED 1/1 |
| `clipboard_selection.py` | 330 | `NativeCopy.restore` | STATEMENTS_COVERED 1/1 |
| `clipboard_selection.py` | 357 | `NativeCopy.restore` | STATEMENTS_COVERED 2/2 |
| `clipboard_selection.py` | 364 | `NativeCopy.restore` | STATEMENTS_COVERED 1/1 |
| `clipboard_selection.py` | 371 | `NativeCopy.restore` | DELETION_NOT_DIRECTLY_COVERABLE |
| `clipboard_selection.py` | 374 | `NativeCopy.restore` | STATEMENTS_PARTIAL 6/7 |
| `clipboard_tools.py` | 19 | `ClipboardWriteUncertain` | STATEMENTS_COVERED 4/4 |
| `clipboard_tools.py` | 139 | `NativeClipboard.write_plain` | STATEMENTS_COVERED 1/1 |
| `clipboard_tools.py` | 142 | `NativeClipboard.write_plain` | STATEMENTS_COVERED 1/1 |
| `clipboard_tools.py` | 147 | `NativeClipboard.write_plain` | STATEMENTS_COVERED 2/2 |
| `clipboard_tools.py` | 152 | `NativeClipboard.write_plain` | STATEMENTS_COVERED 1/1 |
| `clipboard_tools.py` | 155 | `NativeClipboard.write_plain` | STATEMENTS_PARTIAL 7/9 |
| `clipboard_tools.py` | 169 | `NativeClipboard.prepare_plain_rollback` | STATEMENTS_PARTIAL 7/23 |
| `clipboard_tools.py` | 219 | `ClipboardTools.__init__` | STATEMENTS_COVERED 5/5 |
| `clipboard_tools.py` | 228 | `ClipboardTools.__init__` | STATEMENTS_COVERED 1/1 |
| `clipboard_tools.py` | 240 | `ClipboardTools.configure` | STATEMENTS_COVERED 4/4 |
| `clipboard_tools.py` | 255 | `ClipboardTools.clear_history` | STATEMENTS_COVERED 4/4 |
| `clipboard_tools.py` | 264 | `ClipboardTools.delete_history` | STATEMENTS_UNOBSERVED 0/4 |
| `clipboard_tools.py` | 292 | `ClipboardTools._reader` | STATEMENTS_PARTIAL 2/3 |
| `clipboard_tools.py` | 298 | `ClipboardTools._reader` | STATEMENTS_PARTIAL 1/2 |
| `clipboard_tools.py` | 307 | `ClipboardTools._reader` | STATEMENTS_UNOBSERVED 0/1 |
| `clipboard_tools.py` | 315 | `ClipboardTools.receive` | STATEMENTS_COVERED 5/5 |
| `clipboard_tools.py` | 332 | `ClipboardTools.receive` | STATEMENTS_UNOBSERVED 0/3 |
| `clipboard_tools.py` | 338 | `ClipboardTools.receive` | STATEMENTS_COVERED 3/3 |
| `clipboard_tools.py` | 342 | `ClipboardTools.receive` | DELETION_NOT_DIRECTLY_COVERABLE |
| `clipboard_tools.py` | 349 | `ClipboardTools.receive` | STATEMENTS_COVERED 1/1 |
| `clipboard_tools.py` | 352 | `ClipboardTools.receive` | STATEMENTS_UNOBSERVED 0/3 |
| `clipboard_tools.py` | 356 | `ClipboardTools.receive` | STATEMENTS_COVERED 1/1 |
| `clipboard_tools.py` | 363 | `ClipboardTools.request_plain` | STATEMENTS_COVERED 2/2 |
| `clipboard_tools.py` | 370 | `ClipboardTools.request_plain` | STATEMENTS_PARTIAL 13/18 |
| `clipboard_tools.py` | 395 | `ClipboardTools.paste_item` | STATEMENTS_UNOBSERVED 0/2 |
| `clipboard_tools.py` | 409 | `ClipboardTools.paste_item` | STATEMENTS_PARTIAL 1/6 |
| `clipboard_tools.py` | 425 | `ClipboardTools._paste_when_released` | STATEMENTS_PARTIAL 13/21 |
| `clipboard_tools.py` | 463 | `ClipboardTools._paste_when_released.keys_pressed` | STATEMENTS_PARTIAL 6/9 |
| `clipboard_tools.py` | 479 | `ClipboardTools._paste_when_released` | STATEMENTS_PARTIAL 11/12 |
| `clipboard_tools.py` | 513 | `ClipboardTools._paste_when_released` | STATEMENTS_PARTIAL 2/3 |
| `clipboard_tools.py` | 529 | `ClipboardTools._paste_when_released` | STATEMENTS_PARTIAL 1/2 |
| `clipboard_tools.py` | 537 | `ClipboardTools._paste_when_released` | STATEMENTS_COVERED 6/6 |
| `clipboard_tools.py` | 544 | `ClipboardTools._paste_when_released` | STATEMENTS_PARTIAL 19/23 |
| `clipboard_tools.py` | 584 | `ClipboardTools.close` | STATEMENTS_COVERED 4/4 |
| `clipboard_tools.py` | 589 | `ClipboardTools.close` | STATEMENTS_COVERED 11/11 |
| `clipboard_tools.py` | 611 | `ClipboardTools.selection_copy` | STATEMENTS_COVERED 1/1 |
| `direct_hotkeys.py` | 5 | `<module>` | STATEMENTS_COVERED 2/2 |
| `direct_hotkeys.py` | 43 | `pause_toggle_binding` | STATEMENTS_PARTIAL 11/14 |
| `direct_hotkeys.py` | 67 | `compile_bindings` | STATEMENTS_COVERED 1/1 |
| `direct_hotkeys.py` | 93 | `compile_bindings` | STATEMENTS_COVERED 1/1 |
| `direct_hotkeys.py` | 97 | `compile_bindings` | STATEMENTS_PARTIAL 5/7 |
| `direct_hotkeys.py` | 121 | `compile_bindings` | STATEMENTS_PARTIAL 1/2 |
| `direct_hotkeys.py` | 123 | `compile_bindings` | DELETION_NOT_DIRECTLY_COVERABLE |
| `direct_hotkeys.py` | 125 | `compile_bindings` | STATEMENTS_UNOBSERVED 0/1 |
| `direct_hotkeys.py` | 131 | `compile_bindings` | STATEMENTS_COVERED 2/2 |
| `early_native_harness.py` | 44 | `worker.windows` | STATEMENTS_UNOBSERVED 0/1 |
| `early_native_harness.py` | 50 | `worker.windows` | STATEMENTS_UNOBSERVED 0/2 |
| `early_native_harness.py` | 55 | `worker.windows` | STATEMENTS_UNOBSERVED 0/2 |
| `early_native_harness.py` | 59 | `worker.windows` | STATEMENTS_UNOBSERVED 0/3 |
| `early_native_harness.py` | 67 | `worker.windows` | STATEMENTS_UNOBSERVED 0/2 |
| `early_native_harness.py` | 78 | `worker.windows` | STATEMENTS_UNOBSERVED 0/1 |
| `early_scintilla.py` | 106 | `ScintillaEarlyBridge.check_deadline` | STATEMENTS_UNOBSERVED 0/2 |
| `early_scintilla.py` | 119 | `ScintillaEarlyBridge.msg` | STATEMENTS_UNOBSERVED 0/2 |
| `engine.py` | 600 | `LanguageEngine.decide_token` | STATEMENTS_COVERED 3/3 |
| `experiments/r04_private_richedit_windows_probe.py` | 19 | `<module>` | NOT_IN_INSTRUMENTED_SUITE |
| `experiments/r04_private_richedit_windows_probe.py` | 153 | `probe` | NOT_IN_INSTRUMENTED_SUITE |
| `language_decision_v2.py` | 23 | `punctuation_edges_are_literal` | STATEMENTS_PARTIAL 12/13 |
| `language_decision_v2.py` | 221 | `PolicyDecisionEngine.decide_token` | STATEMENTS_COVERED 1/1 |
| `language_decision_v2.py` | 238 | `PolicyDecisionEngine.decide_token` | STATEMENTS_COVERED 3/3 |
| `language_decision_v2.py` | 252 | `PolicyDecisionEngine.decide_token` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `native_transaction_queue.py` | 57 | `NativeTransactionQueue.begin` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `native_transaction_queue.py` | 59 | `NativeTransactionQueue.begin` | STATEMENTS_COVERED 1/1 |
| `native_transaction_queue.py` | 66 | `NativeTransactionQueue.begin` | STATEMENTS_COVERED 1/1 |
| `native_transaction_queue.py` | 119 | `NativeTransactionQueue.capture` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `native_transaction_queue.py` | 126 | `NativeTransactionQueue.capture_trigger_keyup` | STATEMENTS_PARTIAL 8/12 |
| `password_fields.py` | 1 | `<module>` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `password_fields.py` | 3 | `<module>` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `password_fields.py` | 8 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `password_fields.py` | 11 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `password_fields.py` | 22 | `Snapshot` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 27 | `PasswordFieldGuard.__init__` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `password_fields.py` | 30 | `PasswordFieldGuard.__init__` | STATEMENTS_COVERED 2/2 |
| `password_fields.py` | 55 | `PasswordFieldGuard.__init__` | STATEMENTS_COVERED 2/2 |
| `password_fields.py` | 59 | `PasswordFieldGuard._update` | STATEMENTS_COVERED 2/2 |
| `password_fields.py` | 68 | `PasswordFieldGuard._update` | STATEMENTS_COVERED 15/15 |
| `password_fields.py` | 95 | `PasswordFieldGuard._update` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 101 | `PasswordFieldGuard._update` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `password_fields.py` | 108 | `PasswordFieldGuard.ingest` | STATEMENTS_COVERED 2/2 |
| `password_fields.py` | 112 | `PasswordFieldGuard.ingest` | STATEMENTS_COVERED 2/2 |
| `password_fields.py` | 116 | `PasswordFieldGuard.ingest` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 123 | `PasswordFieldGuard.ingest` | STATEMENTS_PARTIAL 12/14 |
| `password_fields.py` | 139 | `PasswordFieldGuard.ingest` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 145 | `PasswordFieldGuard.ingest` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 148 | `PasswordFieldGuard.ingest` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 154 | `PasswordFieldGuard.ingest` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 160 | `PasswordFieldGuard.ingest` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 168 | `PasswordFieldGuard.ingest` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 171 | `PasswordFieldGuard.ingest` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `password_fields.py` | 175 | `PasswordFieldGuard.invalidate` | STATEMENTS_COVERED 2/2 |
| `password_fields.py` | 182 | `PasswordFieldGuard.permit_revision` | STATEMENTS_COVERED 2/2 |
| `password_fields.py` | 185 | `PasswordFieldGuard.permit_revision` | STATEMENTS_COVERED 4/4 |
| `password_fields.py` | 191 | `PasswordFieldGuard.permit_revision` | STATEMENTS_COVERED 1/1 |
| `password_fields.py` | 200 | `PasswordFieldGuard.start` | STATEMENTS_COVERED 2/2 |
| `password_fields.py` | 213 | `PasswordFieldGuard.revalidate` | STATEMENTS_COVERED 4/4 |
| `password_fields.py` | 243 | `PasswordFieldGuard._watch_once` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `password_fields.py` | 245 | `PasswordFieldGuard._watch_once` | STATEMENTS_COVERED 7/7 |
| `password_fields.py` | 260 | `PasswordFieldGuard._watch_once` | STATEMENTS_PARTIAL 2/4 |
| `password_fields.py` | 270 | `PasswordFieldGuard._watch_once` | STATEMENTS_COVERED 16/16 |
| `password_fields.py` | 304 | `PasswordFieldGuard.close` | STATEMENTS_COVERED 3/3 |
| `pipeline_probe.py` | 16 | `<module>` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `pipeline_probe.py` | 26 | `<module>` | STATEMENTS_COVERED 1/1 |
| `quick_search.py` | 1 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `quick_search.py` | 51 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `quick_search.py` | 53 | `read_selection` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `quick_search.py` | 61 | `read_selection` | STATEMENTS_COVERED 5/5 |
| `quick_search.py` | 70 | `read_selection` | STATEMENTS_COVERED 1/1 |
| `quick_search.py` | 72 | `read_selection` | STATEMENTS_COVERED 4/4 |
| `quick_search.py` | 85 | `QuickSearch.__init__` | STATEMENTS_COVERED 7/7 |
| `quick_search.py` | 97 | `QuickSearch.configure` | STATEMENTS_COVERED 12/12 |
| `quick_search.py` | 121 | `QuickSearch.guarded_copy` | STATEMENTS_COVERED 3/3 |
| `quick_search.py` | 127 | `QuickSearch.guarded_copy` | STATEMENTS_PARTIAL 2/8 |
| `quick_search.py` | 146 | `QuickSearch.request` | STATEMENTS_COVERED 1/1 |
| `quick_search.py` | 152 | `QuickSearch.request` | STATEMENTS_COVERED 2/2 |
| `quick_search.py` | 155 | `QuickSearch.request` | STATEMENTS_COVERED 9/9 |
| `quick_search.py` | 169 | `QuickSearch.request.run` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `quick_search.py` | 177 | `QuickSearch.request.run` | STATEMENTS_COVERED 1/1 |
| `quick_search.py` | 179 | `QuickSearch.request.run` | STATEMENTS_COVERED 1/1 |
| `quick_search.py` | 182 | `QuickSearch.request.run` | STATEMENTS_COVERED 3/3 |
| `quick_search.py` | 190 | `QuickSearch.request` | STATEMENTS_COVERED 2/2 |
| `quick_search.py` | 196 | `QuickSearch.request` | STATEMENTS_COVERED 1/1 |
| `quick_search.py` | 200 | `QuickSearch.receive` | STATEMENTS_COVERED 5/5 |
| `quick_search.py` | 206 | `QuickSearch.receive` | DELETION_NOT_DIRECTLY_COVERABLE |
| `quick_search.py` | 209 | `QuickSearch.receive` | STATEMENTS_COVERED 3/3 |
| `settings_copy.py` | 5 | `<module>` | STATEMENTS_COVERED 1/1 |
| `settings_copy.py` | 7 | `<module>` | STATEMENTS_COVERED 1/1 |
| `settings_copy.py` | 9 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_app_controls.py` | 128 | `HookControlTests.test_uncertain_native_hwnd_write_is_scoped_but_generic_remains_global` | STATEMENTS_COVERED 13/13 |
| `test_app_controls.py` | 253 | `HookControlTests.test_single_manual_f8_works_without_automatic_mode_and_swallows_keyup` | STATEMENTS_COVERED 1/1 |
| `test_app_controls.py` | 255 | `HookControlTests.test_single_manual_f8_works_without_automatic_mode_and_swallows_keyup` | STATEMENTS_COVERED 2/2 |
| `test_app_controls.py` | 259 | `HookControlTests.test_single_manual_f8_works_without_automatic_mode_and_swallows_keyup` | STATEMENTS_COVERED 2/2 |
| `test_app_controls.py` | 264 | `HookControlTests.test_scroll_lock_custom_selection_swallows_repeat_and_keyup` | STATEMENTS_COVERED 1/1 |
| `test_app_controls.py` | 266 | `HookControlTests.test_scroll_lock_custom_selection_swallows_repeat_and_keyup` | STATEMENTS_COVERED 2/2 |
| `test_app_controls.py` | 268 | `HookControlTests.test_scroll_lock_custom_selection_swallows_repeat_and_keyup` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_app_controls.py` | 270 | `HookControlTests.test_scroll_lock_custom_selection_swallows_repeat_and_keyup` | STATEMENTS_COVERED 1/1 |
| `test_app_controls.py` | 277 | `HookControlTests.test_custom_manual_key_and_disabled_key` | STATEMENTS_COVERED 2/2 |
| `test_app_controls.py` | 283 | `HookControlTests.test_custom_manual_key_and_disabled_key` | STATEMENTS_COVERED 1/1 |
| `test_app_controls.py` | 286 | `HookControlTests.test_custom_manual_key_and_disabled_key` | STATEMENTS_COVERED 1/1 |
| `test_app_controls.py` | 309 | `HookControlTests.test_paused_hook_does_not_correct` | STATEMENTS_COVERED 1/1 |
| `test_app_controls.py` | 311 | `HookControlTests.test_paused_hook_does_not_correct` | STATEMENTS_COVERED 3/3 |
| `test_app_controls.py` | 314 | `HookControlTests.test_paused_hook_does_not_correct` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_audit_17_20.py` | 95 | `PlainPastePartialAuditTests.test_partial_actual_paste_never_retries_ctrl_v` | STATEMENTS_UNOBSERVED 0/3 |
| `test_browser_readback.py` | 9 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_browser_readback.py` | 125 | `BrowserReadbackTests.test_browser_diagnostic_uses_shared_native_com_reader_without_powershell` | STATEMENTS_COVERED 13/13 |
| `test_build_profiles.py` | 14 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_build_profiles.py` | 34 | `ProfileContractTests.test_public_forbids_text_tracers_and_diagnostics_retains_them` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `test_build_profiles.py` | 40 | `ProfileContractTests.test_public_forbids_text_tracers_and_diagnostics_retains_them` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `test_build_profiles.py` | 44 | `ProfileContractTests.test_public_forbids_text_tracers_and_diagnostics_retains_them` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `test_build_profiles.py` | 47 | `ProfileContractTests.test_public_forbids_text_tracers_and_diagnostics_retains_them` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `test_build_profiles.py` | 51 | `ProfileContractTests.test_missing_native_uia_dependency_is_rejected_in_both_profiles` | STATEMENTS_COVERED 6/6 |
| `test_caps_startup.py` | 159 | `GuiTests.test_one_checkbox_no_stop_or_pause_label_and_settings` | STATEMENTS_UNOBSERVED 0/3 |
| `test_caps_startup.py` | 177 | `GuiTests.test_correction_preference_saved_and_restored_without_installing_hook` | STATEMENTS_UNOBSERVED 0/1 |
| `test_caps_startup.py` | 182 | `GuiTests.test_correction_preference_saved_and_restored_without_installing_hook` | STATEMENTS_UNOBSERVED 0/3 |
| `test_caret_word_recovery.py` | 1 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_caret_word_recovery.py` | 2 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_caret_word_recovery.py` | 7 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 28 | `CaretParserTests.test_exact_hebrew_word_maps_to_russian_and_keeps_spaces` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 31 | `CaretParserTests.test_previous_word_before_next_word_is_separated_by_spaces` | STATEMENTS_COVERED 20/20 |
| `test_caret_word_recovery.py` | 79 | `CaretParserTests.test_reader_does_not_start_without_verified_identity_or_valid_context` | STATEMENTS_COVERED 2/2 |
| `test_caret_word_recovery.py` | 82 | `CaretParserTests.test_reader_does_not_start_without_verified_identity_or_valid_context` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 86 | `CaretParserTests.test_changed_context_timeout_and_bad_reply_fail_closed` | STATEMENTS_COVERED 3/3 |
| `test_caret_word_recovery.py` | 90 | `CaretParserTests.test_changed_context_timeout_and_bad_reply_fail_closed` | STATEMENTS_COVERED 2/2 |
| `test_caret_word_recovery.py` | 96 | `CaretParserTests.test_helper_parses_without_executing_uia_or_reading_foreground` | STATEMENTS_COVERED 4/4 |
| `test_caret_word_recovery.py` | 113 | `CaretRecoveryIntegrationTests.setUp` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 145 | `CaretRecoveryIntegrationTests.read_fixture` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 147 | `CaretRecoveryIntegrationTests.read_fixture` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 151 | `CaretRecoveryIntegrationTests.read_fixture` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `test_caret_word_recovery.py` | 211 | `CaretRecoveryIntegrationTests.test_repeated_single_command_after_space_preserves_word` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 216 | `CaretRecoveryIntegrationTests.test_repeated_single_command_after_space_preserves_word` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_caret_word_recovery.py` | 218 | `CaretRecoveryIntegrationTests.test_repeated_single_command_after_space_preserves_word` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 221 | `CaretRecoveryIntegrationTests.test_repeated_single_command_after_space_preserves_word` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 228 | `CaretRecoveryIntegrationTests.test_pause_recovers_untracked_word_after_space` | STATEMENTS_UNOBSERVED 0/1 |
| `test_caret_word_recovery.py` | 232 | `CaretRecoveryIntegrationTests.test_pause_recovers_untracked_word_after_space` | STATEMENTS_COVERED 1/1 |
| `test_caret_word_recovery.py` | 246 | `CaretRecoveryIntegrationTests.test_simple_mode_untracked_space_passes_once_without_uia` | STATEMENTS_COVERED 12/12 |
| `test_case_direct_hotkeys.py` | 29 | `ShortcutSettingsTests.test_manual_key_conflicts_and_invalid_values` | STATEMENTS_UNOBSERVED 0/1 |
| `test_case_direct_hotkeys.py` | 32 | `ShortcutSettingsTests.test_manual_key_conflicts_and_invalid_values` | STATEMENTS_UNOBSERVED 0/1 |
| `test_case_direct_hotkeys.py` | 46 | `ShortcutSettingsTests.test_conflicts_use_canonical_chords_and_reserved_actions` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `test_clipboard_selection.py` | 14 | `TransactionTests.test_copy_partial_send_cleans_only_accepted_keydowns` | STATEMENTS_COVERED 20/20 |
| `test_clipboard_selection.py` | 165 | `TransactionTests.test_only_explicit_unsupported_uia_pattern_uses_copy` | STATEMENTS_COVERED 4/4 |
| `test_clipboard_selection.py` | 172 | `TransactionTests.test_only_explicit_unsupported_uia_pattern_uses_copy` | STATEMENTS_COVERED 3/3 |
| `test_clipboard_selection.py` | 176 | `TransactionTests.test_only_explicit_unsupported_uia_pattern_uses_copy` | STATEMENTS_COVERED 4/4 |
| `test_clipboard_tools.py` | 147 | `NativeClipboardTests.test_delayed_clipboard_open_cannot_mutate_after_cancellation` | STATEMENTS_COVERED 18/18 |
| `test_desktop_editors.py` | 11 | `<module>` | STATEMENTS_UNOBSERVED 0/1 |
| `test_desktop_editors.py` | 148 | `DesktopEditorTests.test_real_compiled_metadata_policy_for_desktop_capabilities` | STATEMENTS_UNOBSERVED 0/3 |
| `test_early_native_edit.py` | 135 | `TrialDeliveryTests.test_uncertain_native_pause_quarantines_following_cycle_presses` | STATEMENTS_UNOBSERVED 0/2 |
| `test_early_native_edit.py` | 145 | `TrialDeliveryTests.test_native_pause_independent_keydowns_each_verify_one_cycle_without_keyup` | STATEMENTS_COVERED 1/1 |
| `test_early_native_edit.py` | 151 | `TrialDeliveryTests.test_native_pause_independent_keydowns_each_verify_one_cycle_without_keyup` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_early_native_edit.py` | 155 | `TrialDeliveryTests.test_native_pause_independent_keydowns_each_verify_one_cycle_without_keyup` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `test_early_native_edit.py` | 160 | `TrialDeliveryTests.test_native_pause_independent_keydowns_each_verify_one_cycle_without_keyup` | STATEMENTS_PARTIAL 1/4 |
| `test_early_native_edit.py` | 166 | `TrialDeliveryTests.test_notepad_plus_plus_manual_cycle_prioritizes_verified_selected_range` | STATEMENTS_PARTIAL 1/10 |
| `test_early_native_edit.py` | 180 | `TrialDeliveryTests.test_notepad_plus_plus_manual_cycle_prioritizes_verified_selected_range` | STATEMENTS_PARTIAL 2/20 |
| `test_early_native_edit.py` | 429 | `TrialDeliveryTests.test_uncertain_early_write_blocks_boundary_and_manual_cycle_even_after_space` | STATEMENTS_PARTIAL 1/2 |
| `test_engine_user_lexicon.py` | 11 | `EngineLexiconTests` | STATEMENTS_UNOBSERVED 0/3 |
| `test_engine_user_lexicon.py` | 18 | `EngineLexiconTests.setUp` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_hook_cycle_offhook.py` | 8 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_hook_cycle_offhook.py` | 10 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_hook_cycle_offhook.py` | 44 | `GenericCycleOffHookTests.fixture` | STATEMENTS_COVERED 6/6 |
| `test_hook_cycle_offhook.py` | 81 | `GenericCycleOffHookTests.test_manual_only_paused_hook_still_commits_offhook_cycle` | STATEMENTS_COVERED 10/10 |
| `test_hotkey_context.py` | 9 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_hotkey_context.py` | 52 | `ContextTests.test_blocked_metadata_reader_retains_verified_cache_only_until_ttl` | STATEMENTS_COVERED 1/1 |
| `test_hotkey_context.py` | 55 | `ContextTests.test_blocked_metadata_reader_retains_verified_cache_only_until_ttl` | STATEMENTS_COVERED 1/1 |
| `test_hotkey_context.py` | 61 | `ContextTests.test_blocked_metadata_reader_retains_verified_cache_only_until_ttl` | STATEMENTS_COVERED 2/2 |
| `test_hotkey_context.py` | 68 | `ContextTests.test_blocked_metadata_reader_retains_verified_cache_only_until_ttl` | STATEMENTS_COVERED 2/2 |
| `test_hotkey_context.py` | 72 | `ContextTests.test_blocked_metadata_reader_retains_verified_cache_only_until_ttl` | STATEMENTS_COVERED 4/4 |
| `test_hotkey_context.py` | 83 | `ContextTests.test_refresh_with_50_to_100ms_delay_keeps_hotkey_for_same_focus` | STATEMENTS_COVERED 37/37 |
| `test_p0_five_findings.py` | 32 | `FiveFindingsRegressionTests.test_r04_manual_without_caret_proof_refuses_before_send_hkl_and_memory` | STATEMENTS_COVERED 1/1 |
| `test_p0_five_findings.py` | 44 | `FiveFindingsRegressionTests.test_r04_manual_without_caret_proof_refuses_before_send_hkl_and_memory` | STATEMENTS_COVERED 1/1 |
| `test_p0_five_findings.py` | 49 | `FiveFindingsRegressionTests.test_r04_manual_without_caret_proof_refuses_before_send_hkl_and_memory` | STATEMENTS_COVERED 1/1 |
| `test_p0_five_findings.py` | 119 | `FiveFindingsRegressionTests.test_r03_cancel_during_recovery_preparation_retains_original_snapshot` | STATEMENTS_COVERED 13/13 |
| `test_p0_guard_transport_integration.py` | 62 | `P0GuardTransportIntegration.test_hidden_field_guard_option_retains_saved_policy_without_running_hook` | STATEMENTS_COVERED 1/1 |
| `test_p0_guard_transport_integration.py` | 69 | `P0GuardTransportIntegration.test_hidden_field_guard_option_retains_saved_policy_without_running_hook` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `test_p0_guard_transport_integration.py` | 74 | `P0GuardTransportIntegration.test_hidden_field_guard_option_retains_saved_policy_without_running_hook` | STATEMENTS_UNOBSERVED 0/1 |
| `test_p0_guard_transport_integration.py` | 80 | `P0GuardTransportIntegration.test_runtime_allows_unknown_but_rejects_explicit_password_same_hwnd` | STATEMENTS_COVERED 1/1 |
| `test_p0_guard_transport_integration.py` | 84 | `P0GuardTransportIntegration.test_runtime_allows_unknown_but_rejects_explicit_password_same_hwnd` | STATEMENTS_COVERED 1/1 |
| `test_p0_guard_transport_integration.py` | 91 | `P0GuardTransportIntegration.test_runtime_allows_unknown_but_rejects_explicit_password_same_hwnd` | STATEMENTS_COVERED 1/1 |
| `test_p0_guard_transport_integration.py` | 115 | `P0GuardTransportIntegration.test_runtime_does_not_block_on_missing_or_malformed_identity` | STATEMENTS_COVERED 1/1 |
| `test_p0_guard_transport_integration.py` | 121 | `P0GuardTransportIntegration.test_runtime_does_not_block_on_missing_or_malformed_identity` | STATEMENTS_COVERED 1/1 |
| `test_p0_guard_transport_integration.py` | 147 | `P0GuardTransportIntegration.test_no_conversion_or_injection_from_explicit_password_fields` | STATEMENTS_COVERED 1/1 |
| `test_p0_guard_transport_integration.py` | 156 | `P0GuardTransportIntegration.test_no_conversion_or_injection_from_explicit_password_fields` | STATEMENTS_COVERED 1/1 |
| `test_p0_guard_transport_integration.py` | 168 | `P0GuardTransportIntegration.test_password_prefix_then_safe_same_hwnd_suffix_is_not_corrected` | STATEMENTS_COVERED 1/1 |
| `test_p0_guard_transport_integration.py` | 178 | `P0GuardTransportIntegration.test_password_prefix_then_safe_same_hwnd_suffix_is_not_corrected` | STATEMENTS_COVERED 2/2 |
| `test_password_fields.py` | 8 | `<module>` | STATEMENTS_COVERED 2/2 |
| `test_password_fields.py` | 17 | `MetadataTests.test_native_edit_metadata_does_not_authorize_browser_or_password_styles` | STATEMENTS_COVERED 6/6 |
| `test_password_fields.py` | 100 | `MetadataTests.test_hung_helper_is_terminated_without_authorizing_input` | STATEMENTS_COVERED 7/7 |
| `test_password_fields.py` | 108 | `MetadataTests.test_hung_helper_is_terminated_without_authorizing_input` | STATEMENTS_COVERED 1/1 |
| `test_password_fields.py` | 110 | `MetadataTests.test_hung_helper_is_terminated_without_authorizing_input` | STATEMENTS_COVERED 1/1 |
| `test_pipeline_probe.py` | 36 | `PipelineProbeTests.test_manual_pause_probe_records_stages_and_elapsed_time_without_text` | STATEMENTS_COVERED 17/17 |
| `test_portable_diagnostics.py` | 12 | `<module>` | STATEMENTS_UNOBSERVED 0/1 |
| `test_portable_diagnostics.py` | 108 | `PortableDiagnosticsTests.test_uncertain_boundary_disables_later_automatic_early_and_manual_edits` | STATEMENTS_UNOBSERVED 0/1 |
| `test_portable_diagnostics.py` | 123 | `PortableDiagnosticsTests.test_uncertain_boundary_disables_later_automatic_early_and_manual_edits` | STATEMENTS_UNOBSERVED 0/2 |
| `test_portable_diagnostics.py` | 136 | `PortableDiagnosticsTests.test_uncertain_write_quarantine_does_not_block_unrelated_field` | STATEMENTS_UNOBSERVED 0/7 |
| `test_process_identity.py` | 10 | `ProcessIdentityTests.test_one_verified_identity_for_same_host_and_focus_pid` | STATEMENTS_COVERED 16/16 |
| `test_punctuation_runtime.py` | 3 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_punctuation_runtime.py` | 10 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_punctuation_runtime.py` | 16 | `PunctuationRuntimeTests.setUpClass` | STATEMENTS_COVERED 7/7 |
| `test_punctuation_runtime.py` | 35 | `PunctuationRuntimeTests.type_text` | STATEMENTS_COVERED 1/1 |
| `test_punctuation_runtime.py` | 38 | `PunctuationRuntimeTests.type_text` | STATEMENTS_COVERED 1/1 |
| `test_punctuation_runtime.py` | 51 | `PunctuationRuntimeTests.type_text` | STATEMENTS_COVERED 1/1 |
| `test_punctuation_runtime.py` | 79 | `PunctuationRuntimeTests.type_text` | STATEMENTS_COVERED 3/3 |
| `test_punctuation_runtime.py` | 120 | `PunctuationRuntimeTests.test_unambiguous_questions_and_exclamations_keep_literal_marks` | STATEMENTS_COVERED 2/2 |
| `test_punctuation_runtime.py` | 125 | `PunctuationRuntimeTests.test_letter_or_punctuation_suffix_is_not_silently_detached` | STATEMENTS_COVERED 8/8 |
| `test_quick_search.py` | 1 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_quick_search.py` | 4 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_quick_search.py` | 7 | `<module>` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_quick_search.py` | 13 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_quick_search.py` | 126 | `SearchTests.test_native_selection_reader_uses_verified_persistent_uia_service` | STATEMENTS_COVERED 1/1 |
| `test_quick_search.py` | 128 | `SearchTests.test_native_selection_reader_uses_verified_persistent_uia_service` | STATEMENTS_COVERED 3/3 |
| `test_quick_search.py` | 132 | `SearchTests.test_native_selection_reader_uses_verified_persistent_uia_service` | STATEMENTS_COVERED 8/8 |
| `test_quick_search.py` | 144 | `SearchTests.test_native_selection_validates_process_focus_and_selected_range_without_input` | STATEMENTS_COVERED 26/26 |
| `test_r04_default_text_target.py` | 11 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_r04_default_text_target.py` | 21 | `R04DefaultTargetTests.test_stale_native_target_flag_cannot_promote_browser_edit` | STATEMENTS_COVERED 9/9 |
| `test_r04_default_text_target.py` | 58 | `R04DefaultTargetTests.test_postwrite_metadata_change_without_dom_event_blocks_hkl_and_remember` | STATEMENTS_COVERED 5/5 |
| `test_r04_default_text_target.py` | 164 | `R04DefaultTargetTests.test_browser_boundary_and_manual_packets_keep_all_six_directions` | STATEMENTS_COVERED 2/2 |
| `test_r04_default_text_target.py` | 177 | `R04DefaultTargetTests.test_explicit_password_browser_keys_pass_without_mutations` | STATEMENTS_COVERED 2/2 |
| `test_r04_default_text_target.py` | 296 | `R04DefaultTargetTests.test_shared_browser_scope_requires_addressable_uia_id_for_mutation` | STATEMENTS_COVERED 1/1 |
| `test_r04_default_text_target.py` | 299 | `R04DefaultTargetTests.test_shared_browser_scope_requires_addressable_uia_id_for_mutation` | STATEMENTS_COVERED 1/1 |
| `test_r04_default_text_target.py` | 303 | `R04DefaultTargetTests.test_prewrite_blocks_password_unknown_or_changed_element` | STATEMENTS_COVERED 1/1 |
| `test_r04_default_text_target.py` | 312 | `R04DefaultTargetTests.test_prewrite_blocks_password_unknown_or_changed_element` | STATEMENTS_COVERED 1/1 |
| `test_r04_default_text_target.py` | 313 | `R04DefaultTargetTests.test_prewrite_blocks_password_unknown_or_changed_element.refresh` | DELETION_NOT_DIRECTLY_COVERABLE |
| `test_r04_default_text_target.py` | 315 | `R04DefaultTargetTests.test_prewrite_blocks_password_unknown_or_changed_element.refresh` | STATEMENTS_COVERED 3/3 |
| `test_r04_default_text_target.py` | 323 | `R04DefaultTargetTests.test_helper_stall_does_not_require_refresh_and_hook_never_waits` | STATEMENTS_COVERED 1/1 |
| `test_r04_default_text_target.py` | 327 | `R04DefaultTargetTests.test_helper_stall_does_not_require_refresh_and_hook_never_waits` | STATEMENTS_COVERED 1/1 |
| `test_r16_active_languages.py` | 12 | `<module>` | STATEMENTS_COVERED 1/1 |
| `test_r16_active_languages.py` | 110 | `ActiveLanguagesTests.test_manual_cycle_hook_model_uses_engine_active_languages` | STATEMENTS_COVERED 1/1 |
| `test_r16_startup_diagnostics.py` | 58 | `StartupDiagnosticsTests.test_gui_worker_starts_en_ar_through_dictionary_report_and_hook_boundary.fake_hook` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `test_r16_startup_diagnostics.py` | 62 | `StartupDiagnosticsTests.test_gui_worker_starts_en_ar_through_dictionary_report_and_hook_boundary.fake_hook` | STATEMENTS_UNOBSERVED 0/8 |
| `test_r18_console_fallback.py` | 49 | `ConsoleFallbackTests.test_uia_selection_in_console_does_not_use_fallback` | STATEMENTS_COVERED 1/1 |
| `test_r18_console_fallback.py` | 53 | `ConsoleFallbackTests.test_uia_selection_in_console_does_not_use_fallback` | STATEMENTS_COVERED 1/1 |
| `test_r18_console_fallback.py` | 55 | `ConsoleFallbackTests.test_uia_selection_in_console_does_not_use_fallback` | STATEMENTS_COVERED 1/1 |
| `test_ui_i18n.py` | 107 | `HiddenWindowTests.test_all_windows_switch_in_place_without_changing_words_templates_or_enabling_services.widget_labels` | STATEMENTS_UNOBSERVED 0/23 |
| `test_word_cycle.py` | 24 | `WordCycleTests.setUp` | STATEMENTS_COVERED 1/1 |
| `test_word_cycle.py` | 124 | `WordCycleTests.test_pause_second_keydown_cycles_without_keyup` | STATEMENTS_COVERED 1/1 |
| `test_word_cycle.py` | 129 | `WordCycleTests.test_pause_second_keydown_cycles_without_keyup` | STATEMENTS_PARTIAL 2/7 |
| `test_word_cycle.py` | 140 | `WordCycleTests.test_function_key_repeat_is_swallowed_until_keyup` | STATEMENTS_UNOBSERVED 0/3 |
| `tray_win32.py` | 38 | `<module>` | STATEMENTS_COVERED 1/1 |
| `tray_win32.py` | 99 | `menu_items` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `tray_win32.py` | 465 | `TrayController._show_menu` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 49 | `<module>` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 55 | `<module>` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 73 | `<module>` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 103 | `<module>` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 135 | `<module>` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 203 | `Settings` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 233 | `FocusContext` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 269 | `load_settings` | STATEMENTS_PARTIAL 2/3 |
| `typebridge_app.py` | 311 | `load_settings` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 351 | `staged_password_guard` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 354 | `staged_password_guard` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 515 | `manual_cycle_inputs` | STATEMENTS_PARTIAL 12/14 |
| `typebridge_app.py` | 569 | `LiveHook` | STATEMENTS_COVERED 6/6 |
| `typebridge_app.py` | 582 | `LiveHook.__init__` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 586 | `LiveHook.__init__` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 635 | `LiveHook.__init__` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 795 | `LiveHook._context` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 802 | `LiveHook._context` | STATEMENTS_PARTIAL 1/2 |
| `typebridge_app.py` | 827 | `LiveHook._context` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 844 | `LiveHook._context` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 853 | `LiveHook._context` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 861 | `LiveHook._context` | STATEMENTS_COVERED 3/3 |
| `typebridge_app.py` | 868 | `LiveHook._context` | STATEMENTS_COVERED 6/6 |
| `typebridge_app.py` | 881 | `LiveHook._context` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 897 | `LiveHook._context` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 901 | `LiveHook.clear_token` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 942 | `LiveHook._native_blocked` | STATEMENTS_PARTIAL 1/2 |
| `typebridge_app.py` | 959 | `LiveHook._quarantine_native` | STATEMENTS_COVERED 3/3 |
| `typebridge_app.py` | 983 | `LiveHook._quarantine_native` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 989 | `LiveHook._quarantine_native` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 994 | `LiveHook._quarantine_native` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1105 | `LiveHook._send_guarded_inputs` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1136 | `LiveHook._send_guarded_inputs` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1145 | `LiveHook._send_guarded_inputs` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1150 | `LiveHook._send_guarded_inputs` | STATEMENTS_PARTIAL 5/7 |
| `typebridge_app.py` | 1222 | `LiveHook._schedule_native` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 1230 | `LiveHook._schedule_native` | STATEMENTS_COVERED 7/7 |
| `typebridge_app.py` | 1262 | `LiveHook._schedule_native.work` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1275 | `LiveHook._schedule_native.work` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1281 | `LiveHook._schedule_native.work` | STATEMENTS_PARTIAL 3/4 |
| `typebridge_app.py` | 1314 | `LiveHook._schedule_native.work` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 1324 | `LiveHook._schedule_native` | STATEMENTS_PARTIAL 1/2 |
| `typebridge_app.py` | 1338 | `LiveHook._schedule_keyboard` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 1350 | `LiveHook._schedule_keyboard.finish` | STATEMENTS_COVERED 4/4 |
| `typebridge_app.py` | 1385 | `LiveHook._schedule_keyboard.released_job` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1389 | `LiveHook._schedule_keyboard` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 1420 | `LiveHook._browser_field_changed_after_submission` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1464 | `LiveHook._postwrite_permitted` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 1555 | `LiveHook._drain_owned_native_input` | STATEMENTS_COVERED 13/13 |
| `typebridge_app.py` | 1616 | `LiveHook._drain_owned_native_input` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 1631 | `LiveHook._drain_owned_native_input` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1636 | `LiveHook._drain_owned_native_input` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 1643 | `LiveHook._drain_owned_native_input` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 1649 | `LiveHook._drain_owned_native_input` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1656 | `LiveHook._drain_owned_native_input` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1662 | `LiveHook._drain_owned_native_input` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 1669 | `LiveHook._drain_owned_native_input` | STATEMENTS_PARTIAL 3/4 |
| `typebridge_app.py` | 1726 | `LiveHook.restore_native_pending` | STATEMENTS_COVERED 5/5 |
| `typebridge_app.py` | 1739 | `LiveHook.restore_native_pending` | STATEMENTS_PARTIAL 2/5 |
| `typebridge_app.py` | 1777 | `LiveHook.restore_native_pending` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1784 | `LiveHook.restore_native_pending` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 1788 | `LiveHook.restore_native_pending` | DELETION_NOT_DIRECTLY_COVERABLE |
| `typebridge_app.py` | 1928 | `LiveHook._try_early` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1935 | `LiveHook._try_early` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 1937 | `LiveHook._try_early` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 2026 | `LiveHook._try_early` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 2067 | `LiveHook._try_early` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 2184 | `LiveHook._remember` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 2188 | `LiveHook._remember` | STATEMENTS_PARTIAL 16/19 |
| `typebridge_app.py` | 2263 | `LiveHook._try_caret_recovery` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 2273 | `LiveHook._try_caret_recovery` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 2278 | `LiveHook._try_caret_recovery` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 2282 | `LiveHook._try_caret_recovery` | STATEMENTS_PARTIAL 1/2 |
| `typebridge_app.py` | 2307 | `LiveHook._try_caret_recovery.recover` | STATEMENTS_UNOBSERVED 0/2 |
| `typebridge_app.py` | 2311 | `LiveHook._try_caret_recovery.recover` | STATEMENTS_PARTIAL 3/4 |
| `typebridge_app.py` | 2321 | `LiveHook._try_caret_recovery.recover` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 2333 | `LiveHook._try_caret_recovery.recover` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 2340 | `LiveHook._try_caret_recovery` | STATEMENTS_PARTIAL 58/115 |
| `typebridge_app.py` | 2533 | `LiveHook._cycle_browser_selection` | STATEMENTS_PARTIAL 46/65 |
| `typebridge_app.py` | 2648 | `LiveHook._cycle_word` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 2650 | `LiveHook._cycle_word` | STATEMENTS_COVERED 10/10 |
| `typebridge_app.py` | 2665 | `LiveHook._cycle_word` | DELETION_NOT_DIRECTLY_COVERABLE |
| `typebridge_app.py` | 2667 | `LiveHook._cycle_word` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 2670 | `LiveHook._cycle_word` | STATEMENTS_PARTIAL 2/3 |
| `typebridge_app.py` | 2677 | `LiveHook._cycle_word` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 2687 | `LiveHook._cycle_word` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 2713 | `LiveHook._cycle_word` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 2718 | `LiveHook._cycle_word` | STATEMENTS_COVERED 4/4 |
| `typebridge_app.py` | 2730 | `LiveHook._cycle_word.base_valid` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 2732 | `LiveHook._cycle_word.base_valid` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 2735 | `LiveHook._cycle_word.base_valid` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 2741 | `LiveHook._cycle_word.matches` | STATEMENTS_PARTIAL 29/36 |
| `typebridge_app.py` | 2812 | `LiveHook._cycle_word` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 2820 | `LiveHook._cycle_word` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 2831 | `LiveHook._cycle_word` | STATEMENTS_PARTIAL 1/6 |
| `typebridge_app.py` | 2842 | `LiveHook._cycle_word` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 2848 | `LiveHook._cycle_word` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 2875 | `LiveHook._cycle_edit_word` | STATEMENTS_PARTIAL 1/2 |
| `typebridge_app.py` | 2899 | `LiveHook._cycle_edit_word` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 2903 | `LiveHook._cycle_edit_word` | STATEMENTS_UNOBSERVED 0/2 |
| `typebridge_app.py` | 3033 | `LiveHook._correct` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 3038 | `LiveHook._correct` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 3129 | `LiveHook._correct_keyboard` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 3141 | `LiveHook._correct_keyboard` | STATEMENTS_COVERED 3/3 |
| `typebridge_app.py` | 3183 | `LiveHook._correct_native` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 3196 | `LiveHook._correct_native` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 3238 | `LiveHook._correct_native` | STATEMENTS_UNOBSERVED 0/2 |
| `typebridge_app.py` | 3242 | `LiveHook._correct_native` | STATEMENTS_UNOBSERVED 0/5 |
| `typebridge_app.py` | 3283 | `LiveHook.handle_keyboard` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 3299 | `LiveHook.handle_keyboard` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 3369 | `LiveHook.handle_keyboard` | STATEMENTS_COVERED 3/3 |
| `typebridge_app.py` | 3372 | `LiveHook.handle_keyboard` | DELETION_NOT_DIRECTLY_COVERABLE |
| `typebridge_app.py` | 3381 | `LiveHook.handle_keyboard` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 3383 | `LiveHook.handle_keyboard` | STATEMENTS_COVERED 5/5 |
| `typebridge_app.py` | 3420 | `LiveHook.handle_keyboard` | STATEMENTS_PARTIAL 1/2 |
| `typebridge_app.py` | 3436 | `LiveHook.handle_keyboard` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 3471 | `LiveHook.handle_keyboard` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 3474 | `LiveHook.handle_keyboard` | STATEMENTS_COVERED 2/2 |
| `typebridge_app.py` | 3484 | `LiveHook.handle_keyboard` | DELETION_NOT_DIRECTLY_COVERABLE |
| `typebridge_app.py` | 3727 | `LiveHook.run` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 3810 | `TypeBridgeGui.__init__` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 3874 | `TypeBridgeGui.__init__` | STATEMENTS_UNOBSERVED 0/2 |
| `typebridge_app.py` | 3880 | `TypeBridgeGui.__init__` | DELETION_NOT_DIRECTLY_COVERABLE |
| `typebridge_app.py` | 3886 | `TypeBridgeGui.__init__` | DELETION_NOT_DIRECTLY_COVERABLE |
| `typebridge_app.py` | 3915 | `TypeBridgeGui.__init__` | STATEMENTS_UNOBSERVED 0/2 |
| `typebridge_app.py` | 4018 | `TypeBridgeGui._build` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 4026 | `TypeBridgeGui._build` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 4033 | `TypeBridgeGui._build` | STATEMENTS_UNOBSERVED 0/4 |
| `typebridge_app.py` | 4062 | `TypeBridgeGui._build` | STATEMENTS_UNOBSERVED 0/7 |
| `typebridge_app.py` | 4200 | `TypeBridgeGui.restore_clipboard_backup` | STATEMENTS_PARTIAL 20/28 |
| `typebridge_app.py` | 4386 | `TypeBridgeGui.pause` | STATEMENTS_UNOBSERVED 0/2 |
| `typebridge_app.py` | 4412 | `TypeBridgeGui._sync_tray_state` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 4450 | `TypeBridgeGui.current_settings` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 4454 | `TypeBridgeGui.current_settings` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 4460 | `TypeBridgeGui.current_settings` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 4888 | `TypeBridgeGui._drain` | STATEMENTS_PARTIAL 4/6 |
| `typebridge_app.py` | 4902 | `TypeBridgeGui._drain` | STATEMENTS_PARTIAL 1/3 |
| `typebridge_app.py` | 5021 | `TypeBridgeGui._drain` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 5095 | `TypeBridgeGui.start` | STATEMENTS_COVERED 1/1 |
| `typebridge_app.py` | 5098 | `TypeBridgeGui.start` | STATEMENTS_UNOBSERVED 0/2 |
| `typebridge_app.py` | 5101 | `TypeBridgeGui.start` | STATEMENTS_UNOBSERVED 0/3 |
| `typebridge_app.py` | 5108 | `TypeBridgeGui.start` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 5170 | `TypeBridgeGui.start.worker` | STATEMENTS_UNOBSERVED 0/1 |
| `typebridge_app.py` | 5248 | `self_test` | STATEMENTS_UNOBSERVED 0/15 |
| `typebridge_app.py` | 5291 | `self_test` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 5293 | `self_test` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `typebridge_app.py` | 5351 | `main` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `ui_translations.py` | 1323 | `<module>` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `ui_translations.py` | 1658 | `<module>` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `ui_translations.py` | 1694 | `<module>` | STATEMENTS_COVERED 2/2 |
| `word_cycle.py` | 6 | `<module>` | STATEMENTS_COVERED 1/1 |
| `word_cycle.py` | 12 | `effective_word_key` | STATEMENTS_PARTIAL 9/11 |
| `word_cycle.py` | 38 | `RecentWord` | STATEMENTS_COVERED 1/1 |
| `word_cycle.py` | 55 | `remember_word` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |
| `word_cycle.py` | 69 | `remember_word` | STATEMENTS_PARTIAL 1/2 |
| `word_cycle.py` | 72 | `remember_word` | NO_EXECUTABLE_STATEMENTS_IN_RANGE |

## Остальные изменённые отслеживаемые файлы

Изменено **8** файлов вне Python (содержимое также просмотрено/отнесено к документации либо конфигурации):

- `CHANGELOG.md`
- `ROADMAP.md`
- `docs/PRIVACY.md`
- `docs/SETTINGS.md`
- `docs/STATUS.md`
- `docs/outreach/CONTACT-LOG.csv`
- `docs/outreach/PLATFORMS.csv`
- `requirements.txt`

## Новые Python-файлы — полный список

Новые незакоммиченные Python-файлы: **51**. Их код также проходит AST проверку; модельные тесты и isolated/native попытки указаны в CSV, но не все новые эксперименты разрешены к запуску.

- `clipboard_recovery.py`
- `docs/archive/pre-cleanup/caret_word_recovery-before-native-20261107.py`
- `docs/gui_private_matrix_20261107.py`
- `experiments/dictionary_mode_evaluation_20261106.py`
- `experiments/layout_quality_20261106.py`
- `experiments/r04_edge_private_uia_probe.py`
- `experiments/r04_private_uia_value_probe.py`
- `experiments/r06_context_20261107.py`
- `experiments/r10_private_notepad_full_range_20261107.py`
- `experiments/r10_private_scintilla_20261107.py`
- `experiments/r10_selection_reuse_audit_20261107.py`
- `experiments/test_layout_quality_20261106.py`
- `experiments/test_r06_context_20261107.py`
- `experiments/uia_native_startup_smoke_20261107.py`
- `scintilla_selection.py`
- `selection_cycle.py`
- `test_audit_clipboard_rollback_20261106.py`
- `test_audit_plain_paste_worker_20261106.py`
- `test_audit_quick_search_timeout_20261106.py`
- `test_audit_r03_password_replay_20261106.py`
- `test_audit_unconfirmed_hkl_20261106.py`
- `test_browser_search_fields_20261106.py`
- `test_browser_selected_pause_20261107.py`
- `test_clipboard_recovery_20261107.py`
- `test_deep_native_browser_pause_20261107.py`
- `test_field_states_20261106.py`
- `test_manual_only_lifecycle_20261106.py`
- `test_matrix_module.py`
- `test_matrix_runner.py`
- `test_native_password_20261107.py`
- `test_native_uia_caret_20261107.py`
- `test_notepad_barrier_replay_20261107.py`
- `test_p0_queue_overflow_recovery_20261107.py`
- `test_p0_recovery_cancellation_triage_20261107.py`
- `test_p0d_generic_pause_20261107.py`
- `test_pause_no_keyup_20261107.py`
- `test_pause_toggle_settings_20261107.py`
- `test_punctuation_letter_edges_20261107.py`
- `test_r10_pause_scintilla_20261107.py`
- `test_r10_selection_foundation_20261107.py`
- `test_scintilla_selection_20261107.py`
- `test_simple_no_password_20261107.py`
- `test_simple_selected_word_20261107.py`
- `test_simple_switcher_runtime_20261107.py`
- `test_test_matrix_runner_unit.py`
- `test_unified_manual_20261106.py`
- `test_user_field_policy_20261106.py`
- `test_worker_release_recovery_20261107.py`
- `uia_native_worker.py`
- `uia_password_native.py`
- `uia_selection_native.py`

## Остальные новые файлы — полный список, тестовые логи сгруппированы

Новые непрограммные файлы: **34** плюс **24** файлов отдельных тестовых журналов под `docs/test-logs/`.

- `docs/DEEP-NATIVE-AUDIT-20261107.md`
- `docs/DICTIONARY-CHOICE-20261106.md`
- `docs/EXE-TEMP-OWNERSHIP-20261106.md`
- `docs/FIELD-CHECK-MATRIX-20261106.md`
- `docs/GUI-MATRIX-EXPANDED-20261107.md`
- `docs/GUI-PRIVATE-MATRIX-20261107.md`
- `docs/NATIVE-UIA-ARCHITECTURE-20261107.md`
- `docs/P0-AUDIT-20261107.md`
- `docs/P0-CLIPBOARD-RECOVERY-20261107.md`
- `docs/P0-D-INDEPENDENT-REVIEW-20261107.md`
- `docs/PUNCTUATION-LETTER-EDGES-20261107.md`
- `docs/QUALITY-BASELINE-20261106.md`
- `docs/R04-BROWSER-ADDRESSABILITY-20261106.md`
- `docs/R06-CONTEXT-EXPERIMENT-20261107.md`
- `docs/R10-NOTEPAD-CRASH-20261107.md`
- `docs/R10-PAUSE-INTEGRATION-20261107.md`
- `docs/R10-SCINTILLA-ADAPTER-20261107.md`
- `docs/R10-SELECTION-FOUNDATION-20261107.md`
- `docs/RELEASE-READINESS-20261107.md`
- `docs/RELIABILITY-AUDIT-FIXES-20261106.md`
- `docs/SIMPLE-SWITCHER-MODE-20261107.md`
- `docs/TEST-MATRIX-FINAL-20261107.md`
- `docs/UNIFIED-WORD-ACCEPTANCE-20261106.md`
- `docs/UNOBSERVED-CODE-REVIEW-20261107.md`
- `docs/UNOBSERVED-STATIC-WORKER1-20261107.md`
- `docs/USER-FIELD-POLICY-20261106.md`
- `docs/WEEKLY-HUNK-EVIDENCE-20261107.csv`
- `docs/WORKER-RELEASE-RECOVERY-20261107.md`
- `experiments/fixtures/layout_quality_baseline_20261106.json`
- `experiments/fixtures/quality_keymap_20261106.json`
- `experiments/fixtures/r06_context_cases_20261107.json`
- `experiments/fixtures/r06_context_result_20261107.json`
- `experiments/fixtures/r10_scintilla_native_result_20261107.json`
- `experiments/fixtures/r10_selection_reuse_result_20261107.json`

Журналы матрицы и private GUI:

- `docs/test-logs/CONSOLIDATED-METHOD-INVENTORY-20261107.json/` — 0 файлов
- `docs/test-logs/WORKER1-CARET-RECOVERY-20261107.md/` — 0 файлов
- `docs/test-logs/WORKER1-EARLY-NATIVE-20261107.md/` — 0 файлов
- `docs/test-logs/WORKER1-UNITTEST-MATRIX-20261107.md/` — 0 файлов
- `docs/test-logs/gui-private-matrix-20261107-200843-b09bdea6/` — 1 файлов
- `docs/test-logs/gui-private-matrix-20261107-200906-d0a20b1c/` — 1 файлов
- `docs/test-logs/gui-private-matrix-20261107-201008-ebd74e83/` — 1 файлов
- `docs/test-logs/gui-private-matrix-20261107-202535-fee8298e/` — 2 файлов
- `docs/test-logs/gui-private-matrix-20261107-202746-4ec93294/` — 2 файлов
- `docs/test-logs/gui-private-matrix-20261107-203810-9d36ef21/` — 2 файлов
- `docs/test-logs/unittest-matrix-20261107-193828-588b768b/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-194256-640e6122/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-195552-555f9be5/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-200117-3a92d9e2/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-200450-1066f69d/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-200736-657e7724/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-200802-e9d3f1d5/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-201226-860b86cb/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-201331-94ec79f0/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-215708-7bbfced3/` — 1 файлов
- `docs/test-logs/unittest-matrix-20261107-220721-731e78d7/` — 1 файлов

## Журнал проверок — подтверждённое выполнением

### Исходники и точное покрытие изменений

- 36 из 36 коммитов, **286 commit-hunks**, 477 изменённых текущих Python-ханков и 51 новый Python-файл сверены с Git; 65 committed code-file deltas отдельно присутствуют в `docs/WEEKLY-HUNK-EVIDENCE-20261107.csv`. Для **169** функций/классов из новых исходников отдельные строки связывают контракт, AST-кандидатов вызывающего кода, имеющиеся тесты и ограничения. Шесть игнорируемых Git скриптов самого аудита учтены отдельно и не смешаны с Git-ханками.
- Текущий AST parse: **100/100** изменённых/новых Python-файлов успешно (49 отслеживаемых Python-файлов + 51 новый); `git diff --check` без ошибок пробелов (имеются предупреждения Git об ожидаемом LF→CRLF). Служебные скрипты аудита из игнорируемого каталога компилируются отдельно: 6/6 `py_compile` PASS, 5/5 независимых тестов целостности реестра PASS.
- Инструментированная coverage-база включает сохранённый основной прогон **305 PASS / 304 subtests**, отдельные native **115 PASS / 63 subtests**, первый общий core **117 PASS / 4 FAIL / 56 subtests** и после исправления строгой фикстуры **5 PASS / 15 deselected**. Ссылки: `build/audit-20261107/coverage-focused-suite.log`, `coverage-extra-native.log`, `coverage-extra-core.log`, `coverage-caret-red-green-after-fix.log`; единый JSON `coverage-focused.json`. Запуски пересекаются; падающие тесты не объявляются успешно завершёнными.
- Построчный итог **646 текущих диапазонов**: 311 покрыты полностью; 96 частично; 79 содержат только неисполненные операторы; 71 отсутствует в инструментированном прогоне; 59 не имеет исполняемых операторов; 30 — чистое удаление. В **175 записях** — **725 уникальных пар `файл:строка`** неисполненных операторов (включая пропуски в частично покрытых участках), полная карта `docs/UNOBSERVED-CODE-REVIEW-20261107.md`. Это не процент отсутствия регрессий. Для промежуточных версий коммитов нынешнее runtime coverage **не приписывается**.
- Дополнительная независимая нативная серия: **240 тестов PASS, 131 subtests PASS**, один GUI-тест из этого прогона отдельно выполнен на private desktop. Результаты и call-site review: `docs/DEEP-NATIVE-AUDIT-20261107.md`.
- Важный browser Pause B-01 воспроизведён до исправления на трёх синтетических Chrome/Firefox документах. После исправления собственные и соседние тесты успешны, включая выделение, A→B→A, exact caret/suffix/offset, повторные пробелы и strict/simple различия: `test_deep_native_browser_pause_20261107.py`, `test_browser_selected_pause_20261107.py`, `test_p0d_generic_pause_20261107.py`.
- Ранее исправлены устаревшие тестовые интерфейсы PowerShell→native UIA, обязательный frozen-модульный inventory, простой vs строгий режим Pause и объяснение настроек. Дополнительно исправлены устаревшие P0 R03/R04 тестовые точки вмешательства без снижения их безопасности.

### Настоящие Win32/COM проверки — без пользовательского ввода

- `python -B experiments.uia_native_startup_smoke_20261107`: настоящий дочерний COM UIA процесс создался (cold 249.0 мс), повторный IPC использовал тот же процесс (warm 0.2 мс). Это не чтение реального Chrome UIA.
- `experiments/r04_private_richedit_windows_probe.py`: **6 PASS** на собственных Win32 RichEdit контролах, включая замену RU/EN/HE, неизменность соседнего поля, Undo/Redo, отмену при смене поля; JSON `build/audit-20261107/r04-private-richedit-probe.json`.
- `early_native_harness.py --self-test`: **6 PASS**, повторный ручной цикл, Unicode/RTL, Undo, long word off-hook, правильный отказ при stale source; `integration_gate=BLOCKED_PENDING_PHYSICAL_INPUT`. JSON `build/audit-20261107/early-native-private-rich-edit.json`.
- `experiments/r10_private_scintilla_20261107.py` с системной Pythonwin Scintilla 4.4.6 честно отказал в `SCI_GETTEXTRANGEFULL(2039)`; API старой DLL несовместим, production fallback НЕ ослаблен. `experiments/r10_private_notepad_full_range_20261107.py` с Notepad++ 8.9.8.1 не получил HWND на private desktop (`fixture-no-scintilla-window`), а `experiments/r04_edge_private_uia_probe.py` не получил private Edge окно (`private-edge-window-not-created`). Эти проверки остаются BLOCKED, а не PASS.
- `docs/gui_private_matrix_20261107.py`: настоящий Tk 8.6.15 на private **неактивном** desktop; исторические первые 29 PASS затем расширены до **261/261 GUI-методов PASS во всех 22/22 ранее пропущенных модулях**. Поимённая итоговая матрица и журналы: `docs/GUI-MATRIX-EXPANDED-20261107.md`, `docs/GUI-PRIVATE-MATRIX-20261107.md`. Эти private испытания не подменяют physical input.

### Изолированные сборки EXE из актуальных исходников

- Public: `build/audit-20261107/isolated-frozen-20261107/TypeBridge.exe`, SHA-256 `b5c95f4f056979795bbd1549e3488e79b00d4d5114be0030bd2105bc2e7fd488`. PyInstaller PYZ 1784 модуля, обязательные UIA присутствуют, диагностических нет, frozen self-test PASS, шесть языковых направлений 6/6.
- Diagnostics: `build/audit-20261107/isolated-frozen-20261107/profiles/Diagnostics/TypeBridge.exe`, SHA-256 `bed6170e0ea39f18fa568cebdc4d4820d66080b088e1cad2e3601f3c360faf85`. PYZ 1791 модуль, диагностика и UIA присутствуют, frozen self-test PASS, шесть языковых направлений 6/6.
- Для обеих сборок хэши **всех production `.py`** записаны до/после; drift=0, EXE SHA совпадают с provenance. `build/audit-20261107/isolated-frozen-20261107/AUDIT-SOURCE-PROVENANCE.json`. Канонический пользовательский `dist/TypeBridge.exe` не заменялся. Это не релизная публикация и не приёмка hook.

### Полная матрица unittest и причины длительного выполнения

- Исходный `python -m unittest discover -q` выполнялся около 17 минут без финального summary и был прерван в предыдущем этапе. Причина — множество повторных загрузок крупных словарей, импортированные `TestCase` как дополнительные тесты, GUI/Tk случаи и отсутствие тайм-аутов на уровне модуля. Никакого формального PASS для него нет.
- Первый изолированный прогон 155 модулей: **109 passed, 8 failed, 16 timeout (22 секунды), 22 safety-blocked GUI**. Log `docs/test-logs/unittest-matrix-20261107-193828-588b768b/summary.json`.
- Повтор 16 медленных модулей при 150 секундах: **10 passed, 3 failed, 3 timeout**. Следующие 3 при 240 секундах: **1 passed, 1 failed, 1 timeout**. Все результаты и точные имена test IDs лежат в `docs/test-logs/unittest-matrix-20261107-194256-640e6122/summary.json` и `...195552-555f9be5/summary.json`.
- `test_engine` проходит примерно за 92 секунды; `test_word_recovery` — за 236 секунд. `test_early_runtime` после исправления фикстуры дал 32 PASS. `test_engine_user_lexicon` прежде превысил лимит 240 секунд, но **отдельный полный повтор завершился 4/4 PASS за 243,804 с**, `build/audit-20261107/lexicon-600s-final.log`: это был неверно установленный лимит, а не зависание.
- Два класса реальных неудач не скрывались: (a) устаревшие тестовые ожидания синхронного SendInput или старых UIA/PowerShell путей, исправляемые сохранением строгих контрактов; (b) release-gate `test_p4_readiness_20261031` требует **чистого** Git-дерева и правильно остаётся отрицательным во время большого незакоммиченного аудита. Его нельзя переписать ради зелёного статуса.
- Все точечные фиксы тестовых контрактов имеют собственные повторные запуски. **154 исходных модуля PASS, 1 release gate ожидаемо FAIL; по 155 исходным и 3 дополнительным тестовым модулям 1554 метода PASS / 1 ожидаемо FAIL**. GUI 261/261 и лексический 4/4 учтены. Полный реестр по модулям: `docs/test-logs/CONSOLIDATED-METHOD-INVENTORY-20261107.json`, `docs/TEST-MATRIX-FINAL-20261107.md`. Недоказанные *live*-пункты остаются **BLOCKED**, а не классифицируются как успех.

### Известные остаточные риски

1. **Нет проверенного live-сценария Windows-ввода от hook к Chrome/Notepad++**: не останавливалось работающее приложение и не изменялся foreground/HKL пользователя. Реальные гонки UIA→SendInput, ошибки IME, потеря физического KEYUP и Undo в произвольном редакторе полностью не исключены.
2. Реальная Scintilla 5.2.3+ / Notepad++ в private desktop пока недоступна, старая Pythonwin 4.4.6 не поддерживает нужную команду. Вывод по адаптеру ограничен unit-моделями и строгим отказом от несовместимого API.
3. `SIMPLE_SWITCHER_MODE=True` намеренно не проверяет поля паролей и не включает карантин. Для браузерного Pause теперь требуется подтверждённая каретка: при неподдерживаемом UIA он корректно **ничего не делает**, что может снизить функциональность именно в таких полях. Проверить доступность реального провайдера нужно в безопасном тестовом окне с отдельным разрешением.
4. Ни 311 полностью исполненных диапазонов из 646, ни 1554 подтверждённых отдельных методов PASS, ни frozen self-test нельзя интерпретировать как доказательство отсутствия багов в каждом изменении. Невыполненные ветви (725 уникальных строк), физические Windows-acceptance gates и возможные асинхронные гонки перечислены явно.
