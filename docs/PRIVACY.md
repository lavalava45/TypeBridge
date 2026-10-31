# TypeBridge: privacy and input access

This describes the current source version. The published alpha EXE may contain
fewer features; see [roadmap and limitations](../ROADMAP.md). This is a description
of behavior and limitations, not a claim of independent security certification.

**Source-only update, 31 October 2026 (supersedes the 27 October source status
below, not the published EXE):** an *experimental*, **default-off** option now
connects `PasswordFieldGuard` at GUI/headless hook startup. It requires fresh
out-of-process UIA metadata identifying the focused element, including distinct
HTML elements within one HWND. `unknown`, `password`, stale responses and missing
element IDs deny corrective writes while allowing ordinary keystrokes through.
Changing this option requires a complete application restart; it is not a hot
reload. When active, the correction hook disables plaintext word/readback
diagnostics even if old diagnostic flags are present. Historical log files are
NOT deleted. This is unaccepted source code: asynchronous focus races, provider
misreporting, real browser/password editors, physical input and Undo remain
unverified. **Do not rely on this experimental mode to protect actual secrets.**

**Обновление исходников от 31.10.2026:** экспериментальная проверка паролей
подключена к запуску GUI/headless, но по умолчанию выключена. В режиме R04
неизвестные и парольные поля, просроченные ответы и отсутствие идентификатора
UIA-элемента блокируют исправления, но обычный ввод пропускается. Изменение
опции действует только после полного перезапуска. Запись набранных слов
в Diagnostics в этом режиме отключается, ранее созданные журналы не удаляются.
Реальная защита браузерных паролей и физический ввод **не проверены**;
перед вводом секретов по-прежнему необходимо выходить из TypeBridge.

**Дополнительное ограничение R04, 31.10.2026 (только исходники):**
асинхронный UI Automation не может атомарно подтвердить, что HTML-поле с
общим HWND не сменилось на парольное непосредственно перед `SendInput`.
Поэтому экспериментальный R04 при явном включении теперь разрешает
автоматическое исправление **только в обычных нативных EDIT/RichEdit** при
одновременной проверке `ES_PASSWORD` и свежих метаданных UIA. Все браузеры,
WebView, Scintilla и неизвестные классы контролов в этом режиме получают
обычный ввод без преобразования. Это намеренное временное ограничение
функциональности, а не подтверждение безопасности всех парольных менеджеров.
При выключенной (по умолчанию) опции поведение прежнего TypeBridge не меняется.

## English

**Known alpha security limitation (current source, 27 October 2026):**
The general UI Automation `IsPassword` guard exists in the repository but is
**not connected to the correction hook**. Only native Win32 `EDIT` password
styles and exclusions for known password-manager processes are checked there;
they do **not** reliably cover browser or custom password fields. Automatic
and early correction, manual word conversion and templates can alter passwords
or other sensitive input. Exit TypeBridge before typing secrets manually;
prefer password-manager autofill. Disabling correction does not disable
independent history, journal and search features. Changing these docs does
not fix the limitation. Published EXE versions may behave differently.

The separate input journal checks password styles in supported native editors.
Clipboard history uses process exclusions and clipboard privacy flags, but
cannot reliably recognize copied secrets, including from browsers. Keep
optional recording disabled if this retention is unacceptable.

Automatic clipboard history skips content whose owner is absent or whose owner
process cannot be resolved or is excluded. Explicit plain-text paste still
accepts ownerless text when requested by the user; known excluded or inaccessible
owner processes remain blocked. The currently focused field does not establish
the origin or sensitivity of clipboard content.

TypeBridge receives Windows keyboard events to detect words entered in the
wrong layout. Interception is a capability shared with keyloggers; the important
questions are what is processed, retained and sent elsewhere. Ordinary correction
keeps the current word and the last word used for manual cycling in memory;
it does not create a persistent typing diary. Diagnostic messages show operation
results, languages and character counts, rather than the corrected words.
**Exception:** enabling both `TypeBridge-debug.flag` and
`TypeBridge-debug-words.flag` may write actual typed characters and browser
text, **including passwords**, to `TypeBridge-data/debug-words.log`.
No reliable general password filter protects this opt-in word trace. Never
enable it while entering secrets. To stop recording, exit TypeBridge,
remove the word-debug flag, restart, and separately delete sensitive logs.
Early correction in Notepad++ also reads document text temporarily, up to 64 KB.
This means access is broader than just individual key codes.
Unexpected interface callback errors may create a bounded `ui-errors.log` in
the personal data folder: exception types and code locations only, without
exception arguments, variable values, document text or search queries.

| Feature | Retention and transmission | Default |
| --- | --- | --- |
| Layout correction | Local processing; temporary word state in memory | Off in a fresh profile |
| Clipboard history | Up to 30 text items in memory; cleared when disabled or on exit | Off |
| Plain-text paste | Explicit command replaces clipboard contents with plain text | Off |
| Input journal | Text snapshots from allowed editors, encrypted on disk with Windows DPAPI for the current user | Off |
| Quick search | Only the selected text, on its shortcut, goes to the selected provider through your browser | Off |
| Personal words, exclusions and templates | Local JSON files; may contain personal data | Saved only when configured |

Journal defaults allow Notepad and Notepad++ only. Retention is configurable
from 1 to 365 days (default 7). While TypeBridge runs, expiry cleanup also
runs with recording disabled if a database already exists. Disabling recording
immediately cancels further captures. The UI shows settings being applied until
an already-started commit finishes; only then is disabling confirmed. Once
confirmed, no further editor reads or new snapshots occur. This does not erase previous
entries. Age is an upper bound: the oldest entries are also evicted at 20,000
snapshots or 50 MB of encrypted payload (not total SQLite file size). Use its **Delete all entries** button to clear them. The encrypted
payload does not hide timestamps or application names. DPAPI is not protection
against malware already running as you, or against access through your logged-in
session. Clipboard history is unencrypted process memory, not an encrypted vault.

Correction and journaling do not send text to a server. Quick search is an
explicit exception: the query is part of a URL and may remain in browser history
and the provider's records. Custom search URLs determine who receives it;
HTTP URLs also lack transport encryption. Search first reads a nonempty single
selection (up to 2,000 characters) through Windows UI Automation. If the pattern
is unsupported, after the field/password checks it tries a generic Ctrl+C.
Supported clipboard formats are backed up and restored; text-only backups are
not used. Unsupported handle/owner formats or more than 8 MB / 64 formats abort
before Copy. A later clipboard change is not overwritten.
OLE transport/cache records (`DataObject`, `Ole Private Data`) are not copied
back as stale owner pointers; the actual materialized data formats are preserved.
Real embedded or linked OLE payloads still require separate support. Other clipboard
managers may observe the temporary Copy; TypeBridge's own history is paused.
The fallback always shows a preview and requires a Search click, because some
editors copy a whole line with no selection. Old clipboard text is never used
as a query. Applications that do not implement ordinary Copy remain unsupported;
late asynchronous copies and unexpected native failures cannot be ruled out.

The disconnected UIA helper does not authorize or block edits in the current
correction hook. Its old tests and historical changelog notes are not evidence
of active protection. Browser inputs can share the same Win32 window handle;
an unrecognized password field can be treated like ordinary text. Custom
controls, private messages, tokens, seed phrases and cryptocurrency addresses
may not be identifiable. Clipboard privacy flags are not supplied by every
app. Exclude sensitive applications, keep optional recording off, and exit
TypeBridge before entering confidential data. Renamed executables may require
additional exclusions. Other features have their own independent checks.

Disabling autocorrection does **not** disable independently enabled clipboard
history, journal or search. **Exit** in the tray ends all app functions. Personal
files live in `TypeBridge-data`, including settings, user words,
templates and `journal\journal.sqlite3`. Backups and an older migrated
`%LOCALAPPDATA%\LayoutSwitch` folder may retain separate copies. Deleting data
inside TypeBridge does not erase external backups or browser/provider history.

Public source enables inspection, but does not prove that a downloaded binary
matches it or that there are no defects. Antivirus warnings should be assessed
using the actual file and its origin; do not blindly disable security software.

## Русский

**Известное ограничение безопасности текущих исходников (27.10.2026):**
общая UIA-проверка `IsPassword` есть в репозитории, но **не подключена
к исправляющему клавиатурному hook**. Проверка `ES_PASSWORD` для стандартных
Win32 `EDIT` и исключения менеджеров паролей сохранились, но **не защищают
надёжно поля паролей в браузерах**. Автокоррекция, раннее исправление, ручная
конвертация и шаблоны могут изменить пароль или другой секрет. Перед ручным
вводом секретов завершай TypeBridge через трей; предпочтительно автозаполнение
менеджера паролей. Отключение коррекции не отключает отдельно включённые
историю буфера, дневник и поиск. Предупреждение не исправляет дефект;
опубликованные EXE могут отличаться от текущего состояния исходников.

Дневник отдельно проверяет парольный стиль известных нативных редакторов.
История буфера учитывает исключённые процессы и признаки приватности, но не
распознаёт все скопированные секреты, включая данные из браузера.

Автоматическая история пропускает буфер без владельца, с неопределяемым либо
исключённым процессом-владельцем. Явная команда чистой вставки по-прежнему
допускает текст без владельца по запросу пользователя; известные исключённые
и недоступные процессы блокируются. Текущее поле ввода не доказывает происхождение
и безопасность содержимого буфера.

TypeBridge получает события клавиатуры Windows, чтобы находить слова в неверной
раскладке. Такая техническая возможность есть и у кейлоггеров. Поэтому важно
объяснять, что программа делает с данными: обрабатывает, сохраняет или передаёт.
Обычное исправление держит текущее и последнее переключаемое слово в памяти,
не создавая постоянный дневник ввода. Диагностика показывает результат операции,
языки и число символов, а не исправляемые слова. **Исключение:** при наличии
обоих файлов `TypeBridge-debug.flag` и `TypeBridge-debug-words.flag`
специальная диагностика может записывать реальные символы и текст браузерных
полей, **включая пароли**, в `TypeBridge-data/debug-words.log`.
Общего надёжного парольного фильтра у такой записи нет. Чтобы её прекратить,
заверши TypeBridge, удали флаг записи слов, запусти программу заново и отдельно
удали журнал, если он содержит секреты. Ранний режим Notepad++ также
временно читает текст документа — до 64 КБ. Доступ не ограничивается кодами клавиш.
При неожиданной ошибке интерфейса в личной папке может появиться ограниченный
по размеру `ui-errors.log`: только тип исключения и строки кода, без аргументов
ошибки, значений переменных, текста документов и поисковых запросов.

- История буфера, дневник и быстрый поиск выключены по умолчанию и включаются
  отдельно. История — до 30 текстовых фрагментов в памяти, с очисткой при
  отключении или выходе. Это не зашифрованное хранилище секретов.
- Дневник сохраняет снимки разрешённых редакторов на диск с Windows DPAPI для
  текущего пользователя. По умолчанию разрешены только Блокнот и Notepad++.
  Срок хранения — 1–365 дней, исходно 7; пока TypeBridge работает, автоочистка
  существующей базы выполняется и при выключенной записи. Запрос выключения сразу
  отменяет дальнейшие снимки. До завершения уже начатого commit интерфейс показывает
  применение настроек; после подтверждения выключения новые чтения и записи
  не выполняются. Старые записи сохраняются. Возраст — верхняя граница: при достижении
  20 000 снимков или 50 МБ зашифрованных данных старые записи удаляются раньше
  (это не предел размера всего файла SQLite). Для немедленного удаления есть кнопка «Удалить все записи». Время и имя приложения не
  зашифрованы. DPAPI не защищает от программы, уже работающей от твоего имени.
- Исправление и дневник не отправляют текст на сервер. Быстрый поиск отправляет
  выделение выбранному сервису через браузер **только по горячей клавише**.
  Запрос может сохраниться в истории браузера и у сервиса. Получатель определяется
  URL; HTTP, в отличие от HTTPS, не шифрует передачу. Сначала используется Windows
  UI Automation: одно выделение до 2000 символов. При отсутствии TextPattern,
  после проверки поля, есть общий резервный Ctrl+C. Поддерживаемые форматы буфера
  сохраняются и восстанавливаются; при неизвестном формате с нативным handle или
  объёме свыше 8 МБ / 64 форматов копирование не начинается. Более новое содержимое
  не перезаписывается. История самого TypeBridge временно приостанавливается, но
  другие менеджеры буфера могут увидеть временное копирование. Перед отправкой
  резервного результата всегда показан предпросмотр: редактор мог скопировать
  целую строку при отсутствии выделения. Старый буфер не используется как запрос.
  Приложения без обычной команды копирования остаются неподдерживаемыми; нельзя
  полностью исключить позднее асинхронное копирование или нативный сбой.
- Личные слова, исключения и шаблоны хранятся в локальных JSON и тоже могут
  содержать личные сведения.

Сохранившийся код UIA-наблюдателя `IsPassword` сейчас **не используется
исправляющим hook**. Старые тесты и записи истории не доказывают действующую
защиту. Несколько HTML-полей браузера могут иметь один HWND; неизвестное
парольное поле может обрабатываться как обычное. У дополнительных функций
свои отдельные проверки полей и процессов.

**Универсального распознавания секретов нет:** браузерное поле может
не сообщить о пароле, а переписка, ключ или криптоадрес могут выглядеть как обычный
текст. Не все приложения ставят отметки приватности при копировании. Добавляй
чувствительные приложения в исключения, отключай ненужные историю и дневник,
а перед вводом текста, который нельзя менять, отключай исправления. Для
переименованных EXE может понадобиться отдельное исключение.

Выключение автоисправления не выключает отдельно включённые историю, дневник и
поиск. Полностью завершает программу «Выход» в трее. Данные находятся в
`TypeBridge-data`, дневник — в `journal\journal.sqlite3`. Резервные
копии и старая папка `%LOCALAPPDATA%\LayoutSwitch` могут хранить свои экземпляры.
Очистка в приложении не удаляет внешние резервные копии и историю браузера/сервиса.

Открытый код даёт возможность проверки, но не доказывает независимый аудит,
отсутствие ошибок или соответствие скачанного EXE исходникам. Предупреждение
антивируса нужно оценивать по конкретному файлу и источнику, а не отключать
защиту вслепую.

## עברית

**מגבלת אבטחה ידועה בקוד הנוכחי (27.10.2026):** מנגנון UI Automation
`IsPassword` קיים בקוד, אך **אינו מחובר ל-hook של התיקון**. נשארו בדיקת
`ES_PASSWORD` בשדות Win32 `EDIT` רגילים והחרגות למנהלי סיסמאות מוכרים;
הן **אינן מזהות באופן מהימן סיסמאות בדפדפן**. תיקון אוטומטי ומוקדם,
המרה ידנית ותבניות עלולים לשנות סיסמאות וטקסט רגיש. לפני הקלדה ידנית
של סודות יש לצאת מ-TypeBridge; עדיף להשתמש במילוי אוטומטי של מנהל
סיסמאות. כיבוי התיקון אינו מכבה תכונות נפרדות שהופעלו.
שינוי התיעוד לא מתקן את הבעיה; קובצי EXE ישנים עשויים להתנהג אחרת.

TypeBridge מקבל אירועי מקלדת מ־Windows כדי לזהות מילים שהוקלדו בפריסה שגויה.
יכולת דומה קיימת גם ברושמי הקשות; ההבדל שחשוב לבדוק הוא מה מעובד, נשמר ונשלח.
התיקון הרגיל מחזיק את המילה הנוכחית והאחרונה להחלפה ידנית בזיכרון, בלי יומן
הקלדה קבוע. הודעות האבחון הרגילות מציינות פעולות, שפות ומספר תווים ולא את המילים.
**חריג:** עם שני הקבצים `TypeBridge-debug.flag` וגם
`TypeBridge-debug-words.flag`, מצב אבחון המילים עשוי לשמור תווים שהוקלדו
בדפדפן, **כולל סיסמאות**, ב-`TypeBridge-data/debug-words.log`.
אין להסתמך על סינון כללי של סיסמאות. כדי להפסיק את התיעוד יש לצאת
מ-TypeBridge, להסיר את דגל אבחון המילים, להפעיל מחדש ולמחוק בנפרד יומנים רגישים.
התיקון המוקדם ב־Notepad++ גם קורא זמנית טקסט מהמסמך, עד 64 ק״ב.
תקלה בלתי צפויה בממשק עשויה ליצור קובץ אבחון מוגבל בגודלו בשם `ui-errors.log`
בתיקיית הנתונים האישיים. נשמרים רק סוג השגיאה ומיקומי קוד, בלי ערכי משתנים,
ארגומנטים של החריגה, טקסט מסמכים או שאילתות חיפוש.

- היסטוריית הלוח, היומן והחיפוש המהיר מושבתים כברירת מחדל ומופעלים בנפרד.
  ההיסטוריה שומרת עד 30 פריטי טקסט בזיכרון ונמחקת בכיבוי התכונה או ביציאה.
  היא אינה כספת מוצפנת.
- היומן שומר צילומי טקסט מעורכים מורשים בדיסק, עם Windows DPAPI למשתמש
  הנוכחי. כברירת מחדל מורשים רק פנקס הרשימות ו־Notepad++. זמן השמירה הוא
  1–365 ימים, כברירת מחדל 7. כל עוד TypeBridge פועל, ניקוי מסד קיים ממשיך גם
  כשההקלטה כבויה. בקשת כיבוי מבטלת מיד צילומים נוספים. עד לסיום שמירה שכבר
  החלה, הממשק מציג החלת הגדרות; רק אז מוצג אישור הכיבוי. לאחר האישור אין
  קריאת עורכים או הוספת צילומים. זהו גיל מרבי: רשומות
  ישנות נמחקות גם בהגעה ל־20,000 צילומים או 50 מ״ב של נתונים מוצפנים
  (לא הגודל הכולל של קובץ SQLite). כיבוי אינו מוחק מיד רשומות ישנות; ניתן למחוק אותן בחלון היומן. זמן הצילום ושם היישום אינם
  מוצפנים. DPAPI אינו מגן מפני תוכנה שכבר פועלת בשם המשתמש שלך.
- התיקון והיומן אינם שולחים טקסט לשרת. החיפוש המהיר שולח רק את הטקסט המסומן
  לשירות שבחרת דרך הדפדפן, ורק בלחיצה על קיצור המקשים. השאילתה עלולה להישמר
  בהיסטוריית הדפדפן ואצל הספק. כתובת החיפוש קובעת את הנמען; HTTP אינו מצפין
  את התעבורה. תחילה נקרא טווח יחיד של עד 2000 תווים דרך Windows UI Automation.
  אם TextPattern אינו נתמך, לאחר בדיקת השדה נעשה ניסיון Ctrl+C כללי. פורמטי
  הלוח הנתמכים מגובים ומשוחזרים; פורמטים לא נתמכים או יותר מ־8 מ״ב / 64 פורמטים
  מבטלים את הפעולה לפני ההעתקה. תוכן חדש יותר אינו נדרס. היסטוריית TypeBridge
  מושהית, אך מנהלי לוח אחרים עשויים לראות את ההעתקה הזמנית. תמיד מוצגת תצוגה
  מקדימה לפני שליחת תוצאת הגיבוי, כי עורך עשוי להעתיק שורה שלמה כשאין סימון.
  תוכן ישן של הלוח אינו נשלח כשאילתה. יישומים ללא פקודת העתקה רגילה אינם נתמכים;
  אי אפשר לשלול העתקה אסינכרונית מאוחרת או תקלה בפעולה נייטיבית.
- מילים אישיות, החרגות ותבניות נשמרות בקובצי JSON מקומיים ועשויות לכלול מידע אישי.

רכיב UIA הישן לא מחובר לתיקון הפעיל; בדיקות ישנות ויומן שינויים היסטורי
אינם מעידים על הגנה פעילה. כמה שדות HTML יכולים לחלוק אותו HWND,
ושדה סיסמה שלא זוהה עלול להיחשב לשדה טקסט רגיל.
ליומן ולהיסטוריית הלוח יש בדיקות עצמאיות, לא הגנה כללית על כל סוד.

לתכונות הנוספות בדיקות משלהן. **אין זיהוי אוניברסלי של סודות.** שדה בדפדפן עלול
לא לדווח שהוא מכיל סיסמה, ומפתחות, התכתבויות וכתובות קריפטו עשויים להיראות כטקסט
רגיל. לא כל יישום מסמן העתקה כפרטית. הוסף יישומים רגישים להחרגות והשבת היסטוריה
ויומן כשאין בהם צורך. לפני הקלדת טקסט שאסור לשנותו, השבת את התיקון. קובצי EXE
ששמם שונה עשויים לדרוש החרגה נוספת.

כיבוי התיקון האוטומטי אינו מכבה היסטוריה, יומן או חיפוש שהופעלו בנפרד. בחירת
יציאה במגש המערכת מסיימת את כל התכונות. הקבצים האישיים נמצאים בתיקייה
`TypeBridge-data`, והיומן ב־`journal\journal.sqlite3`. גיבויים ותיקיית
`%LOCALAPPDATA%\LayoutSwitch` ישנה עשויים להכיל עותקים נוספים. ניקוי בתוכנה אינו
מוחק גיבויים חיצוניים או היסטוריית דפדפן וספק חיפוש.

קוד פתוח מאפשר בדיקה, אך אינו מוכיח ביקורת עצמאית, היעדר תקלות או התאמה בין
קובץ ההפעלה שהורד לבין המקור. יש לבדוק אזהרות אנטי־וירוס לפי הקובץ ומקורו,
ולא להשבית הגנה באופן עיוור.

## Implementation reference

Selection reading uses Microsoft's read-only
[TextPattern.GetSelection](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.textpattern.getselection)
API, in a hidden, time-limited helper process. An explicit unsupported-pattern
result allows the Copy/preview fallback. Password, empty-selection and unknown
failures do not activate it. Previously copied text is never searched.

מנגנון הסיסמאות של UIA אינו מחובר כיום לתיקון הפריסה. היומן מוגבל לפקדי עורכים מוכרים ובודק את סגנון הסיסמה באופן עצמאי. היסטוריית הלוח אינה יכולה לזהות כל סוד שהועתק, לרבות מדפדפן; השאר אותה כבויה אם שמירה כזו אינה מקובלת.

היסטוריית הלוח האוטומטית מדלגת על תוכן ללא בעלים, על תהליך שלא ניתן לזהות
ועל תהליך מוחרג. הדבקה מפורשת כטקסט פשוט עדיין מאפשרת טקסט ללא בעלים לפי
בקשת המשתמש; תהליך מוחרג או בלתי נגיש נשאר חסום. השדה שבמיקוד כעת אינו
מוכיח מהו מקור תוכן הלוח או אם הוא רגיש.


### Clipboard privacy metadata / Признаки приватности буфера / פרטיות הלוח

Plain-text paste and history refuse clipboard content marked as excluded from
monitoring, history or cloud sync, including malformed permission metadata.
Plain paste rechecks under the clipboard lock before clearing any formats.
On refusal nothing is rewritten; ordinary Ctrl+V remains available.

Вставка без форматирования и история пропускают буфер с признаками запрета
наблюдения, истории или облачной синхронизации, а также с некорректными
признаками разрешения. Перед очисткой форматов выполняется повторная проверка.
При отказе буфер не изменяется; можно использовать обычный Ctrl+V.

הדבקה כטקסט פשוט והיסטוריה מסרבות לתוכן המסומן כפרטי או כאסור להיסטוריה,
לניטור או לסנכרון ענן. הסימונים נבדקים שוב לפני מחיקת הפורמטים. בסירוב
הלוח אינו משתנה; אפשר להשתמש בהדבקה רגילה באמצעות Ctrl+V.

Journal reads remain limited to 64 KiB of UTF-8 text. A timeout starts a bounded
quarantine: a buffer-free editor response probe precedes retries after 30 and
60 seconds; after three timed-out attempts in one process/control lifetime,
that field stays paused until the journal is switched off and back on. Reasons
for skipping snapshots are displayed without document contents. Scintilla remote
buffers retained after a timeout are not freed unsafely; retries are bounded.

Дневник читает не более 64 КБ текста UTF-8. После таймаута повторы ограничены:
через 30, затем 60 секунд редактор сначала проверяется сообщением без буферов.
После трёх таймаутов поле приостанавливается до выключения и включения дневника.
Причина пропуска снимка показывается без содержимого документа. Память Scintilla,
оставшаяся после таймаута, не освобождается небезопасным способом.


### Local dictionary cache / Локальный кэш словарей / מטמון מילונים מקומי

Prepared dictionary data is cached in `TypeBridge-data/dictionary-cache`.
Source contents are verified on every launch; missing required sources still
prevent startup. This cache contains dictionary entries, not captured input or
journal snapshots. It is local, unencrypted data, like the source dictionaries;
custom dictionary additions are included. Deleting it forces preparation again.

Подготовленные словари сохраняются в `TypeBridge-data/dictionary-cache`.
Содержимое исходников проверяется при каждом запуске; отсутствие обязательных
файлов по-прежнему останавливает запуск. Кэш содержит словарные записи,
включая дополнения, без перехваченного ввода и снимков дневника. Как исходные
словари, это локальные незашифрованные данные. Удаление вызывает пересоздание.

נתוני מילונים מוכנים נשמרים ב־`TypeBridge-data/dictionary-cache`.
תוכן קובצי המקור נבדק בכל הפעלה; מקור חובה חסר עדיין מונע הפעלה.
המטמון מכיל ערכי מילון, לרבות תוספות, ללא קלט שנלכד או צילומי יומן.
זהו מידע מקומי לא מוצפן, כמו המילונים המקוריים. מחיקתו מחייבת הכנה מחדש.
