# R04 — проверка адресной записи в браузерные поля, 06.11.2026

**Итог: R04 OPEN; прототип НЕ подключён к обычному запуску.**
Ни один тест не изменял пользовательский браузер или поле.

## Исследованный механизм

| API/транспорт | Адресность | Чего не доказывает |
|---|---|---|
| SendInput | Глобальная очередь событий, не конкретный DOM-элемент | Проверка UIA до SendInput не атомарна с получением события |
| UIA TextPattern | Чтение ranges/selection конкретного UIA-объекта | Не предоставляет обычный API редактирования диапазона |
| UIA ValuePattern.SetValue | Установка всего значения поддерживаемого контрола | Не диапазонная замена с гарантией Undo, caret и IME |
| IAccessibleEditableText.replaceText (IA2) | Интерфейс диапазонной правки по объекту доступности | По спецификации эквивалентен удалению и вставке, не гарантирует атомарность/Undo; поддержка браузера не подтверждена |
| TSF ITfContext.RequestEditSession | Edit lock контекста текстовой службы, если хост его предоставляет | Требует роли text service/допустимого контекста; это не публичный универсальный внешне-адресный DOM API |
| NativeEdit/Scintilla | Адресные сообщения HWND известного контрола | Один HWND не различает DOM-элементы, возможен HWND reuse и частичные транзакции |

Исходные первичные сведения:

- Microsoft: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
- Microsoft: https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpatternsoverview
- Microsoft: https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfcontext-requesteditsession
- IA2 спецификация: https://accessibility.linuxfoundation.org/a11yspecs/ia2/docs/html/interface_i_accessible_editable_text.html
- Chromium: platform accessibility code AXPlatformNodeWin::SetValue (action kSetValue, установка всего значения) в ui/accessibility/platform/ax_platform_node_win.cc. Это свидетельство об одном пути, не универсальный аудит всех browser API.

## Изолированные реальные эксперименты

1. Собственная синтетическая HTML-страница: поля A/B/password, две кнопки фокуса и команды Undo/Redo. Профиль Edge и desktop создавались одноразово без расширений, без пользовательского ввода или фокуса. Скрипт experiments/r04_edge_private_uia_probe.py. Выполнен дважды: **unavailable, private-edge-window-not-created**. Отсутствие окна не доказывает отсутствие UIA/IA2 в Edge, поэтому SetValue/Undo здесь не испытаны.
2. Два независимых нативных EDIT-контрола в собственном inactive desktop через расширенный тестовый early_native_harness.py --worker --edit-fixture. Скрипт experiments/r04_private_uia_value_probe.py. Оба раза PowerShell UIA.FromHandle завершился **ElementNotAvailableException**; фактические A/B остались без изменений. Атомарность/Undo ValuePattern не удалось проверить на этом desktop; вызов EM_UNDO без предшествующей мутации не является проверкой Undo.
3. Отдельный уже проверенный private RichEdit эксперимент использовал адресные SendMessageTimeoutW по сохранённому HWND A: 3 направления RU/HE/EN, B неизменен, Undo/Redo прошли; уничтожение A перед следующей записью дало отказ. См. R04-PRIVATE-RICHEDIT-WINDOWS-20261104.md.
4. Детерминированные модели test_r04_addressed_prototype.py и test_r04_staged_hook_integration.py сохраняют контрпример A→B между последним guard и глобальным SendInput, включая два DOM-элемента одного HWND. Нельзя приравнивать их к физическому браузерному эксперименту.

Команды запуска (только собственные disposable поля, без VM и пользовательских данных):

    py -3.12 experiments/r04_edge_private_uia_probe.py
    py -3.12 experiments/r04_private_uia_value_probe.py
    py -3.12 early_native_harness.py --self-test
    py -3.12 experiments/r04_private_richedit_windows_probe.py

## Условия, без которых нельзя включить адресный browser transport

1. Проверена доступность **конкретного** DOM-объекта A как изменяемого через штатный API без смены фокуса и инъекции кода.
2. Изменяющий вызов не перенаправляется в B при A→B, A→B→A и при смене DOM identity внутри того же HWND.
3. После destroy/recreate A ошибка явно различима от успешной записи, повтор при неопределённом результате запрещён.
4. Readback исходного/итогового текста и caret, Undo/Redo во всех RU/EN/HE сценариях; отдельная проверка composition/IME и event semantics браузера.
5. Нет вмешательства в password/unknown/readonly поля, нет отправки fallback-глобальных клавиш после неопределённой адресной попытки.

Ни одно из этих условий не выведено только из наличия HWND, Runtime ID, ValuePattern или успешного чтения.

**Решение на текущем этапе:** не заменять работающий путь без проверяемой записи. Сохранены уже доказанные узкие NativeEdit/Scintilla-транспорты, имеющийся fail-closed guard и post-write барьер; новый browser path по умолчанию не включён. Для R04 в браузере остаётся окно гонки глобальной доставки. Для окончательного принятия понадобится разрешённый эксперимент на собственном настоящем интерактивном browser DOM без пользовательских данных (без расширений/VM), либо отдельно подтверждённый адресный transport. До этого **R04 OPEN, No01 BLOCKED**.
