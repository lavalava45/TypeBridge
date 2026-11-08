# TypeBridge — исправления по аудиту, 06.11.2026

**Статус: SOURCE ONLY.** База: `main`, `c5c0950`.
Эти изменения ещё **не входят** в опубликованную alpha.8 и не
испытаны физическим вводом. Чужие правки в двух outreach CSV сохранены.

## 1. Quick Search: зависание busy и устаревшие результаты

Воспроизведено на подменённом reader: после истечения срока
`QuickSearch.busy` оставался `True` до возврата reader; смена настроек
сама по себе не освобождала состояние.

Теперь `expire_pending()` от GUI-таймера выдаёт результат по сроку
без ожидания фонового reader. Каждому запросу присваивается token;
старые ответы не открывают браузер и не сбрасывают новый запрос.
`configure()` инвалидирует предыдущий token. Copy fallback
сериализован через отдельный `_copy_lock` с исходным дедлайном.
Число одновременно существующих search-worker ограничено двумя.
Третий запрос получает явный отказ, а не порождает ещё один зависший поток.

**Остаток:** нельзя безопасно убить Python thread, который уже находится
в необратимом Win32 clipboard API. Если два worker так и не вернутся,
дальнейшие запросы останутся недоступны до их завершения или перезапуска.
Это осознанный fail-closed, а не полное устранение блокировок Win32.

## 2. Plain paste: отмена после перезаписи clipboard

Ранее после `EmptyClipboard`/записи обычного текста поздняя смена
фокуса отменяла Ctrl+V, но оригинальный форматированный буфер уже исчезал.

Теперь перед изменением делается строгий ограниченный snapshot всех
поддерживаемых форматов; при неподдерживаемом/неполном снимке операция
не начинает замену. Повторно проверяются исходное поле, deadline,
поколение настроек, физические модификаторы и sequence.
Проверка приватных форматов выполняется под тем же clipboard lock
**до** перечисления и чтения содержимого; запрещённый clipboard
не копируется даже во временный snapshot.

При отмене **до попытки SendInput** или после точно нулевого результата
SendInput восстанавливается snapshot, только если clipboard всё ещё
имеет ожидаемую sequence и **точный HWND владельца TypeBridge**.
Чужое обновление буфера не затирается. Если хотя бы одно событие Ctrl+V
принято либо SendInput вызвал исключение, откат не делается: вставка
могла уже произойти или оставаться в очереди Windows. KEYUP-only
очистка и запрет автоматического повтора сохранены.

После успешной вставки clipboard остаётся в plain-text формате, как
раньше: немедленная реставрация могла бы опередить обработку Ctrl+V.

**Продолжение 06.11:** весь путь plain paste (snapshot, временная запись,
Ctrl+V, условный rollback) выполняется в одном отдельном ограниченном
worker вместо Tk GUI. После задержанного открытия буфера непосредственно
перед EmptyClipboard проверяются закрытие, поколение запроса, deadline,
поле и модификаторы. Пока предыдущий worker не завершился, следующая
вставка не запускается; новые команды не зависают в orphan pending.
Ошибка после EmptyClipboard теперь содержит подтверждённую sequence
нашей попытки, если её удалось наблюдать; неизвестное изменение,
возможно чужое, не разрешает откат.

**Остаток:** неподдерживаемые OLE/delayed-rendering форматы могут теперь
блокировать plain paste; их нельзя уничтожать без полной резервной
копии. Блокирующий Windows API удерживает один worker, но уже не
останавливает GUI и не создаёт неограниченные новые потоки.
После частично принятого Ctrl+V возможны поздняя доставка и смена
адресата; автоматическое восстановление тогда запрещено. Нет атомарности.

## 3. Клавиатурный callback

Для совпадающих PID окна и поля один уже проверенный `process_name()`
используется дважды; второй `OpenProcess/GetProcessTimes` в этом случае
исключён. Для разных host/focus PID обе независимые проверки сохранены.
Выигрыш по задержке **не измерялся**, PID reuse-защита не отключалась.

### Другие реальные дефекты, подтверждённые и исправленные в этом этапе

- Copy fallback: фиксированные C-UP/Ctrl-UP после частично принятого
  SendInput заменены на очистку только известных неотпущенных DOWN.
  Все префиксы 0/1/2/3 принятых событий покрыты регрессией.
  Ctrl+C не отправляется повторно.
- R03: при нулевом отказе password guard досылка удержанных букв
  ранее теряла возможность ручного восстановления. Добавлены четыре
  конкретные причины отказа в локальный fail-closed recovery.
  Автоматической досылки или записи в неизвестное/другое поле нет;
  вручную восстановить можно только после повторной проверки исходного
  контекста, HKL и модификаторов.

## 4. Результаты запуска

Из `E:\Downloads\devspace-test\typebridge\keyboard-layout-prototype`:

```powershell
py -3.12 -m unittest -q test_audit_clipboard_rollback_20261106 test_audit_quick_search_timeout_20261106 test_clipboard_tools test_clipboard_selection test_quick_search test_process_identity test_r04_default_text_target test_r04_native_target_lease test_native_transaction_queue test_hotkey_context
# Ran 123 tests ... OK

py -3.12 -m unittest -q test_r04_addressed_prototype test_r04_staged_hook_integration test_p0_r04_security_edge_20261031 test_p0_guard_transport_integration
# Ran 48 tests ... OK

py -3.12 -m py_compile clipboard_selection.py clipboard_tools.py quick_search.py typebridge_app.py test_audit_clipboard_rollback_20261106.py test_audit_quick_search_timeout_20261106.py test_process_identity.py
git diff --check
# exit code 0
```

Новые проверки: A→B после clipboard write, отказ/ошибка восстановления,
изменение буфера чужим владельцем, partial SendInput, строгий отказ
от неполной OLE-копии и отказ чтения приватного буфера; зависший reader,
поздний ответ, изменение настроек,
Copy-lock и ограничение числа worker. Это **модели с подменёнными API**.

## 5. R04: осталась открытой

Глобальный `SendInput` в keyboard correction, раннем исправлении,
ручном цикле, шаблонах, досылке и Ctrl+V/Copy не адресован исходному
DOM-полю. Между последней проверкой UIA и обработкой системных событий
фокус может смениться, в том числе на другое поле внутри общего HWND.
Post-write guard может остановить HKL/запоминание, но не откатить
принятое событие. Модель R04 этот контрпример сохраняет.

NativeEdit и Scintilla имеют узкую HWND-адресность; NativeTargetLease
остаётся экспериментальным opt-in и не блокирует HWND reuse.
Они не устраняют браузерный случай. Microsoft
[документирует системную очередь SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput);
[UIA TextPattern](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-textpattern-overview)
только читает текст, а
[ValuePattern.SetValue](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.valuepattern.setvalue)
меняет всё значение поддерживаемого контрола, не обеспечивает общий
безопасный range replace с Undo.

Результат новых адресных браузерных попыток, исходные API и
проверяемые ограничения описаны в
[R04-BROWSER-ADDRESSABILITY-20261106.md](R04-BROWSER-ADDRESSABILITY-20261106.md).
Edge на неактивном desktop не создал доступного окна; UIA native
EDIT в таком окружении вернул ElementNotAvailableException.
Нельзя трактовать эти отказы как доказательство отсутствия API в
интерактивном браузере. Chromium ValuePattern.SetValue является
установкой всего значения, IA2 replaceText не гарантирует
атомарность/Undo; новая реализация для браузера не подключена.

**R04 OPEN; No01 BLOCKED.** Автокоррекция не отключалась, экспериментальная
доставка не включалась по умолчанию. Следующий целевой этап:
read-only проверка поддерживаемых адресных Windows UIA/IA2/TSF
возможностей на одноразовом контроле и отдельная проверка lifetime/Undo,
без fallback на глобальную запись при неопределённом результате.

## 6. Повторная интеграционная проверка после заключительных правок

    py -3.12 -m unittest -q test_audit_r03_password_replay_20261106 test_audit_plain_paste_worker_20261106 test_audit_clipboard_rollback_20261106 test_audit_quick_search_timeout_20261106 test_clipboard_tools test_clipboard_selection test_quick_search test_process_identity test_r03_replay_state test_p0_five_findings test_native_transaction_queue test_password_fields test_r04_addressed_prototype test_r04_default_text_target test_r04_staged_hook_integration test_p0_guard_transport_integration test_p0_r04_security_edge_20261031 test_keyboard_early_transport test_offhook_native test_early_native_edit

Фактически: **307/307 тестов успешно, 85.055 секунды**. Это
модели/интеграции с подменёнными опасными интерфейсами, не физический ввод.
Повторная проверка разрешённого собственного неактивного Win32 desktop:

    py -3.12 early_native_harness.py --self-test
    py -3.12 experiments/r04_private_richedit_windows_probe.py

Фактически: шесть реальных NativeEdit замен RU/EN/HE с Undo,
три отдельные адресные A/B-замены с Undo/Redo, B неизменен;
тайм-аут Win32 1460, отказ при уничтоженном A. Отдельная
проверка одиночного EM_REPLACESEL для HE снова не сохранила Undo.
Это реальный synthetic Win32 на disposable desktop, но **не**
проверка переключения физического DOM-фокуса.

## 7. Ограничения выполнения

Не использованы VM, драйверы, browser extensions, инъекции в чужие
процессы, реальные пароли/документы/clipboard, глобальные hooks,
физический ввод, foreground/HKL, запуск SwitchER64. Сборка,
commit, push и публикация не выполнялись. Изменены только исходники,
изолированный Win32 test harness, новые тесты и документация;
незакоммиченные outreach CSV не трогали. Private-desktop синтетические
Win32 испытания не затрагивали рабочую программу.

## 8. Повторный независимый аудит и исправления в завершающем цикле

Повторно проверены фактические вызовы GUI:
`tray plain-paste → ClipboardTools.request_plain → _reader → receive
→ _paste_when_released → guarded native write/Ctrl+V/rollback`,
`tray quick-search → QuickSearch.request → reader/guarded_copy →
QuickSearch.receive`, а также `LiveHook` generic/native manual,
boundary и R03 replay. Все правки ниже включены **только в исходники**.

1. **Воспроизводимая гонка устаревших сообщений.** `receive(('cancelled',
   old_generation))` и `receive(('paste-error', old_error))`
   без проверки владельца сбрасывали `pending` нового paste-запроса.
   Каждая операция теперь имеет уникальный token. Перед обработкой
   `cancelled/prepared/paste-error` сверяются token и поколение;
   `configure/clear_history/delete_history/close` инвалидируют
   старый запрос. Новая интеграционная модель проверяет настоящую
   очередь `_reader → receive` без Windows clipboard.
2. **Quick Search против завершающейся вставки.** Раньше после
   `pending=False`, но до завершения `rollback.close()`, мог
   начаться новый Copy. `selection_copy` теперь отказывает, пока
   paste-worker ещё жив. Запоздавшие сообщения не снимают этот барьер.
3. **Ложная память при неподтверждённом HKL.** Generic boundary,
   NativeEdit boundary и generic manual cycle ранее могли считать
   замену завершённой и запоминать новую раскладку, несмотря на
   неподтверждённый `GetKeyboardLayout`. Исправлено: без подтверждения
   текст **не повторяется**, неверный вариант не запоминается, старый
   token сбрасывается и дальнейшее продолжение блокируется.
4. **Исключение во время досылки R03.** Исключение guard **до**
   первого вызова Win32 теперь сохраняет исходные задержанные буквы
   для явного возвращения после новой проверки поля/HKL. Исключение
   **после** входа в SendInput считается неопределённой доставкой:
   карантин, без replay. Нельзя автоматически возвращать прежний
   физический триггер.
5. **Ошибки провайдера при ручном восстановлении.**
   `restore_native_pending` теперь отказывает без исключения из
   команды и не стирает буфер при ошибке `context()` или чтения HKL.
6. **Нестабильный тест направлений:** тест маршрутизации RU/EN/HE
   зависел от отдельного потока с жёстким 25-мс таймером метаданных.
   При нагрузке это давало случайный fail-closed. В тесте именно
   маршрутизации успешная ревалидация теперь детерминирована;
   отдельные тесты реального guard/timeout не удалены.

Новые тесты: `test_audit_unconfirmed_hkl_20261106.py`,
расширения `test_audit_plain_paste_worker_20261106.py` и
`test_audit_r03_password_replay_20261106.py`.
Предел подтверждения не изменился: NativeEdit/Scintilla адресуются
собственным HWND, но не имеют доказанного atomic lifetime lock при
уничтожении/reuse; shared-HWND браузерные DOM-поля и глобальный
`SendInput` сохраняют R04-контрпример.

7. **Зависание подготовки plain paste до получения данных.** Раньше
   заблокированный `NativeClipboard.read()` удерживал `pending=True`
   без отдельного тайм-аута; повторные нажатия не давали результата.
   Добавлен независимый GUI `ClipboardTools.expire_pending()` с
   2-секундным сроком и единственным сообщением об отказе. Очередь
   reader ограничена одним ожидающим заданием: переполнение
   отвергается явно. Закрытие приложения использует `put_nowait` и
   не блокируется на полной очереди. Старый worker по-прежнему
   нельзя принудительно останавливать во время Win32 вызова:
   запоздавшая выдача отбрасывается по token. Это безопасный
   отказ, не обещание уничтожить зависший системный вызов.

**Расширенная регрессия после ВСЕХ правок:**

```powershell
py -3.12 -m unittest -q test_audit_r03_password_replay_20261106 test_audit_unconfirmed_hkl_20261106 test_audit_plain_paste_worker_20261106 test_audit_clipboard_rollback_20261106 test_audit_quick_search_timeout_20261106 test_clipboard_tools test_clipboard_selection test_quick_search test_process_identity test_r03_replay_state test_p0_five_findings test_native_transaction_queue test_password_fields test_r04_addressed_prototype test_r04_default_text_target test_r04_staged_hook_integration test_p0_guard_transport_integration test_p0_r04_security_edge_20261031 test_keyboard_early_transport test_offhook_native test_early_native_edit test_scintilla_undo_budget test_scintilla_worker test_hook_cycle_offhook
# Ran 345 tests in 83.732s — OK
```

Изолированные Win32 RichEdit-тесты уже успешно выполнены предыдущим
этапом и не повторялись в этом финальном цикле: нативная логика
NativeEdit/Scintilla не изменялась. Текущие изменения в
`LiveHook` проверены мокированными Win32-ответами HKL и
сохранением задержанных событий. Реальный физический RU/EN/HE
ввод, браузерный A→B и HWND reuse не проверялись.

**R04 OPEN, No01 BLOCKED**. Для доказательства закрытия R04
требуется отдельный разрешённый эксперимент на специально созданных
интерактивных браузерных DOM-полях A/B (в том числе A→B→A,
password/unknown, Undo/Redo, destroy/recreate) либо реализация
и проверка объектно-адресного транспорта, который не может изменить B.
Наличие HWND или UIA Runtime ID такого доказательства не даёт.
