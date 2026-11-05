# R04: адресность записи и гонка между проверкой поля и записью — 04.11.2026

**Статус: SOURCE-ONLY / MODEL PROTOTYPE. R04 OPEN, No01 BLOCKED.**
Ни один из результатов ниже не доказывает безопасность физического ввода в браузере.
Задача этапа — понять, *какому конкретно получателю* адресуется изменение,
а не нарастить ещё один UIA-предчек перед глобальным `SendInput`.

## 0. База и ограничения

- Дерево: `E:\Downloads\devspace-test\typebridge\keyboard-layout-prototype`,
  `main`, базовый `HEAD d5eb3dc` (04.11.2026).
- До исследования `git status` показывал **только две чужие правки**
  `docs/outreach/CONTACT-LOG.csv` и `docs/outreach/PLATFORMS.csv`.
  Обе оставлены нетронутыми; код обычного запуска не изменён.
- Прочитаны `AGENTS.md`, `ROADMAP.md`, `docs/STATUS.md`,
  `docs/R04-STAGED-PASSWORD-GATE-20261030.md`,
  `docs/R04-POSTCHECK-BOUNDARY-20261104.md`,
  `docs/P0-R04-NO01-PHYSICAL-ACCEPTANCE-20261031.md`.
- Новых драйверов, установок, внедрения кода, глобальных hooks, VM,
  настоящего ввода, работы с пользовательскими паролями/документами/буфером,
  смены foreground/HKL, сборок EXE, коммитов или push нет. Нет субагентов.
  Поведение и настройки основного приложения не менялись.

## 1. Фактическая карта транспортов

Общие детали проверки: `LiveHook._context` в `typebridge_app.py:713+`
извлекает foreground top HWND, focused HWND через `GetGUIThreadInfo`,
PID/TID, class, HKL, проверяет исключения и `ES_PASSWORD`;
`PasswordFieldGuard.permit_revision` для браузеров/общих поверхностей
добавляет асинхронную UIA-идентичность элемента (Runtime ID), свежесть
метаданных и ревизию. `_word_scope` включает HWND/TID/ревизию, а
`_native_scope` — также ревизию наблюдаемого браузерного поля. Само по
себе наличие HWND/Runtime ID **не связывает** глобальную доставку клавиш с
проверенным объектом. У native-fast-path есть отдельные ограничения.

| Путь | Адресат / идентичность | Guard перед записью | Способ изменения и порядок | Отказ / подтверждение |
|---|---|---|---|---|
| Автоисправление после разделителя, не-RichEdit (`_correct_keyboard`) | Активное при обработке системное поле; до отправки фиксируются HWND/TID/HKL/UIA revision | `_send_guarded_inputs`: контекст и, при наличии guard, `revalidate` | Глобальный `SendInput` с Backspace/Unicode/разделителем, worker и bounded queue | Число принятых INPUT, не документ; частичная отправка и KEYUP cleanup, post-write guard запрещает последующие HKL/remember, **не отменяет уже принятого** |
| Раннее исправление до пробела, generic (`_try_keyboard_early`) | Текущий системный получатель, не сохранённый DOM/контрол | Off-hook worker, проверка scope/ревизии, `_send_guarded_inputs` | Глобальный `SendInput` без пробела, held trigger/очередь | `SendInput` count; текст не читается адресно, поздний guard перед HKL |
| Ручная команда и цикл, generic (`_cycle_word`) | То же, last-word scope и текущая раскладка | `still_valid`, `_send_guarded_inputs`, post-write | Глобальный `SendInput` с полным исправленным словом | Частичная отправка — no retry; post-write блокирует HKL/состояние, **не** предотвращает B-изменение при гонке |
| RichEdit после разделителя (`_correct_native`) | Сохранённый `context.focus` HWND; класс `RichEdit50W`/`RichEditD2DPT`, PID/TID, parent, стиль, источник/каретка | `NativeEditBridge.check_identity`, `valid`, snapshot/prefix/selection | `SendMessageTimeoutW(hwnd, EM_SETSEL/WM_CHAR/EM_STOPGROUPTYPING/...)`; off-hook worker и bounded queue | Чтение документа/каретки после записи; timeout после dispatch — **uncertain**, no retry, карантин; HKL/CapsLock после дополнительной проверки |
| RichEdit до пробела (`_try_early`, `NativeEditBridge`) | Тот же сохранённый HWND; проверенный полный исходный текст | Подтверждение доставленного held-key, `check_identity`, `still_valid` | Адресные `WM_CHAR` по UTF-16 плюс `EM_SETSEL`; не общий SendInput | Чтение итогового документа; возможна частичная запись при сбое внутри серии, `uncertain`, запрещён повтор |
| RichEdit ручной цикл (`_cycle_edit_word(native=True)`) | Сохранённый HWND; проверка предыдущего текста и каретки | `NativeEditBridge` + `valid`, проверка после записи | Адресные Win32-сообщения фиксированному HWND | `replace_prefix` с readback, затем HKL/remember только при разрешении |
| Notepad++ Scintilla до пробела / ручной цикл | HWND `Scintilla` приложения `notepad++.exe`, PID, класс, каретка/выделение; не DOM | `ScintillaEarlyBridge`, `valid` вокруг `replace_prefix`, readback | `SCI_SETTARGETSTART/END`, `SCI_REPLACETARGET`, `SCI_BEGIN/ENDUNDOACTION` на зафиксированный HWND; текстовые буферы через cross-process `VirtualAllocEx`/`WriteProcessMemory` (без исполнения кода) | Проверка реального текста/каретки; timeout/exceptions = uncertain, не повторять; память при timeout удерживается |
| Шаблоны/автозамена (`_expand_snippet`) | Только текущий системный фокус, scope/ревизия до записи | `_send_guarded_inputs` и worker, проверка matcher | Глобальный SendInput с Backspace/Unicode, возможны CR/TAB как действие приложения | Отправленные INPUT, не документ; no retry; CR/TAB могут отправить форму/сообщение |
| Досылка задержанных клавиш (`_drain_owned_native_input`) | Только проверяемый текущий scope и HKL, но **не** адресный получатель | Проверки original scope, модификаторов, HKL перед batch/remainder | Глобальный `SendInput`, bounded FIFO по owner/epoch; запрещён replay в новом поле при известном отказе | Accepted counts; при неопределённости карантин, часть безопасного несданного хвоста может сохраняться для явного восстановления |
| CapsLock и HKL после текстовой записи | CapsLock через глобальный `SendInput`, HKL запрос по HWND потока/окна, **не DOM элементу** | `_postwrite_permitted` проверяет текущий scope/HKL/ревизию | CapsLock DOWN/UP и `WM_INPUTLANGCHANGEREQUEST` после write | CapsLock partial+KEYUP cleanup, HKL readback; повторная проверка не делает ранний write атомарным |

В `early_native_edit.py` обычный `EDIT` **не** поддержан действующим
`supports_native_edit`; его нельзя считать автоматически адресно
защищённым. Для кнопки прямого выбора HKL (`_switch_direct`) не происходит
исправления текста: там отдельный thread/window scoped запрос раскладки,
не разрешение на текстовую мутацию.

## 2. Детерминированная гонка A → B

Изолированные файлы: `experiments/r04_addressed_richedit.py` и
`test_r04_addressed_prototype.py`. Фальшивые Windows/document объекты
хранят независимые A (`ghbdtn`) и B (`B_UNCHANGED`); безопасный guard
сначала разрешает A. В модели исходный глобальный поток состоит из 6
логических Backspace и 6 символов `привет`; это **12 логических действий
модели, НЕ 12 реальных INPUT и НЕ измеренная обработка Windows**.

| Детерминированный сценарий | Вызов / принято | Модель A | Модель B | Последующие действия |
|---|---|---|---|---|
| Глобальный ввод: перед получением смена на B (парольное / safe / unknown), полный пакет | Model-global, 12/12 действий | `ghbdtn` | **Изменено** | Реальные HKL/remember в модели не вызываются; постпроверка не может отменить доставку |
| То же, частично | Model-global, 2/12 | `ghbdtn` | **Изменено** | Текст мог быть частично изменён, повтор запрещён |
| Два разных DOM-поля внутри **одного** HWND, новый UIA-элемент получает глобальный ввод | Логическая модель | `ghbdtn` | **Изменено** | По одному HWND нельзя определить нужный DOM |
| Адресный RichEdit: фокус меняется на другой HWND внутри `EM_REPLACESEL` | 2 адресных сообщения A | `привет` | `B_UNCHANGED` | `uncertain`, нет auto-replay/Undo, хвост только для явного восстановления |
| Адресный RichEdit: стабильный A | 2 адресных сообщения A | `привет` | `B_UNCHANGED` | `verified` после модельного readback; порядок хвоста сохранён как tuple, **физически не воспроизводился** |
| A→B либо A→B→A **до** начала отправки | 0 | Без изменений | Без изменений | `refused` по изменившейся эпохе фокуса |
| Неизвестный/парольный/stale provider, тайм-аут чтения до записи | 0 | Без изменений | Без изменений | `refused` |
| Тайм-аут, исключение или частичная запись адресному A | 1–2 адресных сообщения A | Возможно частично изменено | Без изменений | `uncertain`, второе исправление запрещено |
| Сохранённый HWND уничтожен до preflight | 0 | Отсутствует | Без изменений | `refused` |
| **Контрпример**: уничтожение и повторное использование *того же числа HWND* с теми же PID/TID/class во время dispatch | 2 адресных сообщения | **Новому контролу, ошибочно** | Без изменений | Модель может выдать ложное `verified`: идентификатор HWND — не токен жизни контрола |

**Граница доказательства:** `FakeWindows` сам выбирает адресную
доставку сообщений. Модель проверяет логику нашего алгоритма *при
предположении* семантики Win32; она не подтверждает порядок Windows
message loop, реальный HWND reuse, реальные провайдеры UIA или атомарность
изменения документа. Проверка живых `NativeEditBridge.msg` и
`ScintillaEarlyBridge.msg` использует **заменённый Win32 API** и
подтверждает сохранённый адрес `context.focus`, а не прием реальным редактором.
В текущем `FocusContext` нет готовой системной `focus_epoch` с нужной
гарантией: это пока исключительно модельная абстракция.

## 3. NativeEdit и Scintilla: что реально обещает адресность

**RichEdit:** `NativeEditBridge.check_identity` проверяет `IsWindow`,
класс, отношение parent-child, PID/TID и стили `ES_PASSWORD`/readonly.
`snapshot` читает `EM_GETSEL`, `WM_GETTEXTLENGTH`, `WM_GETTEXT`,
сверяет source и position, `replace` проверяет `EM_SETSEL`, посылает
серийные `WM_CHAR`, затем readback. Сам `SendMessageTimeoutW(hWnd,...)`
адресует указанный HWND, а не новый foreground, но проверка HWND и отправка
сообщения тоже разделены во времени. Возможны уничтожение, reuse HWND,
редактирование самим приложением, timeout и неоднозначный результат.
`SMTO_ERRORONEXIT` улучшает диагностику уничтожения/выхода thread,
но не делает lifetime identity или два сообщения атомарными.
Прямой `EM_REPLACESEL` поддерживает замену выделения одним сообщением,
**но действующий код обходит его из-за ранее наблюдавшегося регресса Undo,
включая HE**. Экспериментальный прототип не доказывает сохранение Undo.

**Scintilla:** `SCI_REPLACETARGET` адресуется HWND Scintilla, а
начало/конец диапазона задаются через отдельные сообщения. Подтверждаются
текст, выделение и каретка. Обработчик может принять timeout, уже изменив
документ; remote text buffer должен сохраняться до гарантированной
безопасности освобождения. Уничтожение/замена контрола, гонка между
`SCI_SETTARGETSTART/END` и `SCI_REPLACETARGET`, неподтверждённый Undo,
UTF-8/IME и неполная отправка остаются отдельными задачами. Наличие
`VirtualAllocEx` здесь означает буфер текста в чужом процессе, **не**
инъекцию исполняемого кода; в данном этапе новых операций с памятью нет.

## 4. Сравнение механизмов Windows

| Кандидат | Реально закрывает | Не закрывает / цена внедрения | Решение |
|---|---|---|---|
| Повторные UIA checks + глобальный SendInput | Смену, обнаруженную **до** последнего check; post-write побочные действия | TOCTOU после check; same-HWND DOM; не адресует текст; мало изменений, но недостаточно | Не считать решением R04 |
| Адресные EDIT/RichEdit сообщения на сохранённый HWND | Перенаправление **на другой HWND при смене фокуса**, при условии что исходный контрол жив и HWND не переиспользован | HWND reuse, DOM с общим HWND, гонки между собственными сообщениями, Undo и timeout; умеренная цена | **Выбран только для узкого RichEdit-прототипа** |
| Scintilla SCI_REPLACETARGET на конкретный HWND | Тот же другой-HWND случай в Notepad++; readback | Смещение, lifetime, процессные буферы, Undo/IME, ограниченное покрытие приложений | Существующий адаптер; углублять после RichEdit |
| TSF `ITfContext::RequestEditSession` с `TF_ES_READWRITE` | Потенциально транзакция в контексте документа, если хост реализует TSF, у нас есть допустимый доступ к контексту и выдан edit lock | Не реализовано; требуется роль TSF text service, поддержка хоста/браузера, защищённых полей, Undo; высокая цена | Отдельная ветка исследований, не готовая замена |
| `IAccessibleEditableText::replaceText` по объекту доступности | Потенциальная адресность к конкретному объекту доступности (в отличие от HWND), если provider и приложение поддерживают интерфейс | Спецификация допускает семантику delete+insert, НЕ гарантирует атомарность; поддержка/фокус/Undo конкретного браузера не проверены; средне-высокая цена | Узкое read-only capability исследование перед любыми mutating trials |
| UI Automation `TextPattern` / `ValuePattern` | TextPattern: чтение; ValuePattern: установка **всего** значения для поддерживаемого контрола | Не даёт общей адресной replace-range с Undo, возможно submit/события в приложении; provider-specific | Не подходит как общий корректор |
| `AttachThreadInput`, наблюдение порядка, bounded queue | Совместное состояние фокуса/очередей потоков и отдельные гарантии порядка, без адресности DOM | Не связывает `SendInput` с проверенным полем, меняет состояние ввода; риск побочных эффектов | Не использовать как R04-fix |

Первичные источники и их точные границы:

- [Microsoft: SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) — серийная вставка INPUT в системный поток; HWND параметра нет.
- [Microsoft: SendMessageTimeoutW](https://learn.microsoft.com/en-us/windows/desktop/api/winuser/nf-winuser-sendmessagetimeoutw) — точный HWND адресата, `SMTO_ERRORONEXIT`, тайм-аут и границы межпроцессного marshalling.
- [Microsoft: EM_REPLACESEL](https://learn.microsoft.com/en-us/windows/win32/controls/em-replacesel) — замена выделения, семантика Undo для edit/RichEdit, без результата-числа написанных символов.
- [Microsoft: AttachThreadInput](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-attachthreadinput) — совместное состояние ввода и порядок, не DOM-блокировка.
- [Microsoft: ITfContext::RequestEditSession](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfcontext-requesteditsession) — read/write edit lock по ITfContext; отказ при блокировке или disconnected context, ограничения sync.
- [Microsoft: UI Automation control patterns](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpatternsoverview) — Text/Value и capabilities provider.
- [Linux Foundation: IAccessibleEditableText](https://accessibility.linuxfoundation.org/a11yspecs/ia2/docs/html/interface_i_accessible_editable_text.html) — адресный интерфейс `replaceText`, но его спецификация приравнивает реализацию к delete+insert, не гарантируя атомарность.
- [Scintilla documentation](https://www.scintilla.org/ScintillaDoc.html) — `SCI_REPLACETARGET`, `SCI_BEGINUNDOACTION`, `SCI_ENDUNDOACTION`.

Отдельная оговорка: наличие интерфейса в спецификации IA2 **не доказывает**
его реализацию и безопасное поведение в любом браузере, включая Chromium
и Firefox. Ни CapabilityQuery живых браузеров, ни COM-мутирующих проб не было.
Продуктовые браузерные расширения и их разработка в этой работе исключены.

## 5. Оригинальный SwitchER64: только ранее сохранённые REA/Ghidra-свидетельства

Активной REA/Ghidra-сессии нет: `list_documents` вернул
`target_unavailable: No app is open`. **Нового анализа бинарника не проводилось.**
Исследование оригинала выполнено только через чтение существующих
адресных отчётов `docs/reference/SWITCHER-NO01-FINAL-PARITY-20261022.md`,
`SWITCHER-REA-FUNCTION-CATALOG-20261017.md`,
`V1-ACCEPTANCE-MATRIX.md` — не чтение или запуск EXE другим способом.
Их указанный SHA-256 оригинала:
`496420852b1bd14870e4585eaff87dacec2d9083203de98a81cb028efa1f6e2d`.

- `FUN_140119a10` (outer WH_KEYBOARD_LL): ветви отрицательного `nCode`
  и занятого CMPXCHG отправляют события через `CallNextHookEx`;
  внутренний обработчик `FUN_1400c0280`.
- Архивные данные связывают `FUN_1400bf5e0`, `FUN_1400b9950`,
  `FUN_1400b9db0` и действие `0x19` с ранним исправлением **при
  условиях runtime state**, а не безусловно для любой конфигурации.
- `FUN_140175870`: отдельные `SendInput` DOWN/UP для Backspace;
  получатель не связан с UIA-полем, нет проверенного результата редактора.
  `FUN_140175f00` проверяет HKL и формирует key replay,
  `FUN_1400e4200` выбирает путь запроса раскладки.
- В сохранённых отчётах встречается `AttachThreadInput`; исследованные
  вызовы обслуживают чтение `GetGUIThreadInfo`, **не** установленную
  атомарную блокировку ввода вокруг замены. Это локальная реконструкция
  отдельных ветвей, не отрицательное доказательство по всему бинарнику.

**Вывод по аналогу:** подтверждены hook, Backspace, replay, HKL; **нет**
доказательства, что SwitchER64 предотвращает перенаправление исправления
из A в B или сохраняет все клавиши. Внешне успешный ввод этого не доказывает.

## 6. Изолированный прототип и проверка

Прототип **не подключён** к `typebridge_app.py` и не вызывается при обычном
запуске. Он использует строго определённый интерфейс FakeWindows и один
зафиксированный RichEdit-HWND. Перед записью проверяются class, PID/TID,
модельная `focus_epoch`, HKL, guard revision, безопасность, документ,
каретка и selection. `EM_SETSEL` и `EM_REPLACESEL` направляются по
сохранённому HWND; после write обязательный модельный readback. Failure после
отправки — `uncertain`, повтор и спекулятивный Undo запрещены. Очередь
возвращается в виде **неисполняемого упорядоченного tuple**, не автоматически
переигрывается. HWND reuse представлен в качестве **проваленного
security-предположения**, а не скрыт положительным отчётом.

Команды (из корня репозитория, только Python-модели):

```powershell
py -3.12 -m unittest -q test_r04_addressed_prototype
py -3.12 -m py_compile experiments/r04_addressed_richedit.py test_r04_addressed_prototype.py
git diff --check
git status --short --branch
```

Проверку расширенного набора и окончательное число тестов см. в текущем
`docs/STATUS.md`. Команды не устанавливают hook, не используют реальные
Win32 и не обращаются к пользовательскому вводу/буферу.

## 7. План маленьких патчей после независимого review

1. **Первый следующий патч (не внедрён):** выделить из NativeEdit
   модельно проверяемый read-only `TargetLease` и контракт адресного
   получателя (`HWND, PID, TID, class, document/caret/selection, guard
   revision`) с повторной проверкой непосредственно перед мутацией;
   добавить `SMTO_ERRORONEXIT` в изолируемом адаптерном эксперименте.
   Не менять default и не использовать глобальную отправку при отказе.
2. Независимо воспроизвести HWND reuse/destroy, slow provider, частичную
   замену и отсутствие дублирования в моделях **существующего**
   `NativeEditBridge`; отдельно проверить реальное Undo/Redo RU/EN/HE,
   включая HE, **только после разрешения**.
3. Переносить адресность на `_correct_native`, early/manual и Scintilla
   по одному пути, защищая pending trigger/FIFO и документируя частичную
   запись. Любой путь после очереди с глобальным SendInput остаётся
   независимым R04-риском.
4. Для браузеров сначала отдельно проверить *доступность и семантику*
   объектно-адресного IA2 `replaceText` и TSF context edit session на
   поддерживаемых редакторах без изменения текста пользователя.
   Не считать доступный объект автоматическим разрешением на запись.
5. Если остаётся непреодолимый конкретный сценарий, оформить **именно его**
   воспроизведение, минимальное условное ограничение и поведение отмены/
   восстановления; **не менять состояние по умолчанию без согласования**.
   Запрещены спекулятивный Undo и replay неопределённой записи в другом поле.

Сохранить перед будущим включением RU↔EN, RU↔HE, EN↔HE (шесть направлений),
раннюю коррекцию *до Space*, физический быстрый хвост, возврат удержанных клавиш,
CapsLock/HKL, выделение/Undo/Redo в редакторе. Тесты моделей в этом этапе
не закрывают ни один из критериев физической приёмки.

## 8. Остаётся непроверенным

Физическая A→B гонка при программном DOM-focus без mouse event; реальный
Chrome/Firefox UIA/IA2/TSF provider и защищённые поля; актуальность Runtime
ID при смене DOM; смена окна в середине обработки `SendMessageTimeoutW`;
уничтожение/повторное использование HWND в настоящем Windows; отсутствие
потери физического буфера и подтверждение реального порядка сообщений;
отмена/исключение и точный Undo/Redo после per-char `WM_CHAR`,
`EM_REPLACESEL`, Scintilla, IME/AltGr/Unicode; права процессов/UIPI;
точность и задержка под настоящей нагрузкой; шесть направлений с физической
клавиатурой. Ничего из перечисленного не объявлено прошедшим.

**Итог:** проверен узкий механизм, устраняющий именно перенаправление на
*другой живой HWND* при смене фокуса. Проблема глобального SendInput в
общих HWND/DOM и самостоятельная гонка жизни HWND остаются. **R04 OPEN.**

## 9. Следующий узкий шаг: критический повторный аудит и опциональный патч

**Выполнено 04.11.2026, только исходники/подменённые API, без субагентов.**
Проверены исходники экспериментальной модели, реальные точки Win32-отправки
в `NativeEditBridge.msg` и первичные документы Microsoft:
`SendMessageTimeoutW`, `IsWindow`, `EM_REPLACESEL`.

Повторный аудит подтвердил ограничения предыдущей модели:

1. `focus_epoch` из `FakeWindows` **не** предоставляется существующим
   `LiveHook` в виде атомарного системного токена. Реальный статус UIA/DOM,
   быстрый A→B→A и невидимые смены фокуса модель не доказывают.
2. Снимок числового `HWND`, даже с PID/TID/class, **не блокирует** уничтожение
   и повторное использование дескриптора. Microsoft явно предупреждает,
   что `IsWindow` чужого процесса может устареть сразу после вызова.
3. Опытная `EM_REPLACESEL` **не может заменить** живую последовательность
   `WM_CHAR` без регрессий Undo, в том числе для HE. Действующий
   `NativeEditBridge.replace` пока сохраняется без изменений.
4. `SendMessageTimeoutW` с `SMTO_ERRORONEXIT` лучше сообщает о
   разрушении окна или смерти потока во время обработки, но не обеспечивает
   уникальности окна или отката уже изменённого документа.
   `EM_REPLACESEL` не возвращает число вставленных символов.
5. Модельный `tail_ready_for_ordered_replay` — кортеж без реальной отправки.
   Ни один тест прототипа не удостоверяет lossless FIFO/Undo/No01.

**Подготовлен узкий патч (НЕ интегрирован с обычным запуском):**

- `early_native_edit.py` принимает необязательный
  `experimental_target_lease=False`. Его значение по умолчанию и все
  прежние caller'ы сохранены.
- При явном включении после `__enter__` фиксируется read-only
  `NativeTargetLease` (`HWND`, top, PID/TID контрола, отдельный TID владельца
  раскладки, точный класс, номер ревизии проверяемого поля и исходный полный
  HKL). Разделение TID важно: `FocusContext.thread_id` может быть потоком
  владельца раскладки, а не потоком HWND. Это **снимок**, не lock.
- Непосредственно перед `WM_CHAR`, `EM_SETSEL`,
  `EM_STOPGROUPTYPING` и экспериментальной `EM_REPLACESEL` сверяются
  прежняя идентичность контрола, `still_valid()` и полный HKL.
  При отсутствии lease/смене scope/class/PID/HKL/password style — отказ
  **до** отправки. Значение ревизии хранится в lease, а её свежесть зависит
  от существующего callback `still_valid`, не от нового UIA lock.
- Для этого экспериментального режима сообщения отправляются с
  `SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT` (0x23);
  обычный режим использует прежние флаги 0x03. Состояния timeout и
  already-submitted сохраняют существующий запрет blind retry.
- **Не вызывается** из `typebridge_app.py`, не меняет GUI, настройки,
  алгоритм замены, HKL, Undo или транзакционную очередь.

**Что эта правка даёт:** дополнительный наблюдаемый отказ при
смене/устаревании получателя *до* конкретного адресного сообщения и
отдельный test seam для последующих экспериментов. **Не даёт:** гарантии
«объект A всё ещё жив между проверкой и Windows dispatch»; полноты DOM-полей
и отсутствия перенаправления при HWND reuse; атомарности нескольких
`WM_CHAR`; доказанного восстановления Undo/FIFO.

Связанные тесты: `test_r04_native_target_lease.py` и исходный
`test_r04_addressed_prototype.py`. Все вызовы Win32 в них подменены,
пользовательский ввод, clipboard и focus не задействованы.

**Фактические результаты:** 196 исполнений в расширенном регрессионном
наборе без ошибок; после заключительного уточнения потока HKL и
экспериментального тайм-аута `EM_REPLACESEL` — 48/48 целевых
модельных тестов без ошибок. `py_compile` и `git diff --check`
прошли. Изменения последнего уточнения не проверялись повторным
196-тестовым прогоном: это важно учитывать при независимом ревью.

Точные команды для нового этапа:

```powershell
py -3.12 -m unittest -v test_r04_native_target_lease
py -3.12 -m unittest -q test_r04_native_target_lease test_r04_addressed_prototype test_early_native_edit test_r04_default_text_target test_p0_five_findings test_offhook_native test_early_runtime test_keyboard_early_transport test_hook_cycle_offhook
py -3.12 -m py_compile early_native_edit.py test_r04_native_target_lease.py
git diff --check
git status --short --branch
```

**Следующая небольшая задача, после оценки этого патча:** в
неактивном частном desktop (разрешённом `AGENTS.md`, без foreground/hook)
измерить адресные сообщения на собственном *непользовательском*
RichEdit, включая уничтожение/тайм-аут и точный Undo для RU/EN/HE.
Никакие результаты модели не заменяют реальный документный readback или
физическую приёмку; R04 остаётся **OPEN**, No01 — **BLOCKED**.

## Дополнение 05.11.2026 — повторная проверка чтения

Намеченная выше private-desktop проверка уже выполнена, см.
[R04-PRIVATE-RICHEDIT-WINDOWS-20261104.md](R04-PRIVATE-RICHEDIT-WINDOWS-20261104.md).
В независимой сверке opt-in расширен: guard вызывается перед чтением, а не
только изменением; адрес SendMessage берётся из неизменяемого lease.
Регрессии проверяют отказ от WM_GETTEXT после смены password style/guard
и привязку назначения. Это не OS lifetime lock, не решение HWND reuse или
DOM-атомарности; обычный runtime эксперимент не включает.
[Новый отчёт](R17-R18-R04-20261105.md).
