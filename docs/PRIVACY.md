# TypeBridge: privacy and input access

This describes the current source version. The published alpha EXE may contain
fewer features; see [feature status](FEATURE-STATUS.md). This is a description
of behavior and limitations, not a claim of independent security certification.

## English

TypeBridge receives Windows keyboard events to detect words entered in the
wrong layout. Interception is a capability shared with keyloggers; the important
questions are what is processed, retained and sent elsewhere. Ordinary correction
keeps the current word and the last word used for manual cycling in memory;
it does not create a persistent typing diary. Diagnostic messages show operation
results, languages and character counts, rather than the corrected words.
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
from 1 to 365 days (default 7); cleanup runs while journaling is enabled.
Disabling journaling stops reading and writing, but does not erase previous
entries. Use its **Delete all entries** button to clear them. The encrypted
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

Known password-manager applications are excluded. Correction skips ordinary
Win32 EDIT password fields; other features apply their own field/process checks.
**This is not universal password detection.** Custom browser controls, secrets
inside ordinary text, private messages, tokens, seed phrases and cryptocurrency
addresses may not be identifiable. Clipboard privacy flags are not supplied by
every app. Exclude sensitive applications, leave optional recording features
off when unneeded, and disable correction before entering text that must remain
exactly unchanged. Renamed executables can require additional exclusions.

Disabling autocorrection does **not** disable independently enabled clipboard
history, journal or search. **Exit** in the tray ends all app functions. Personal
files live in `%LOCALAPPDATA%\TypeBridge`, including settings, user words,
templates and `journal\journal.sqlite3`. Backups and an older migrated
`%LOCALAPPDATA%\LayoutSwitch` folder may retain separate copies. Deleting data
inside TypeBridge does not erase external backups or browser/provider history.

Public source enables inspection, but does not prove that a downloaded binary
matches it or that there are no defects. Antivirus warnings should be assessed
using the actual file and its origin; do not blindly disable security software.

## Русский

TypeBridge получает события клавиатуры Windows, чтобы находить слова в неверной
раскладке. Такая техническая возможность есть и у кейлоггеров. Поэтому важно
объяснять, что программа делает с данными: обрабатывает, сохраняет или передаёт.
Обычное исправление держит текущее и последнее переключаемое слово в памяти,
не создавая постоянный дневник ввода. Диагностика показывает результат операции,
языки и число символов, а не исправляемые слова. Ранний режим Notepad++ также
временно читает текст документа — до 64 КБ. Доступ не ограничивается кодами клавиш.
При неожиданной ошибке интерфейса в личной папке может появиться ограниченный
по размеру `ui-errors.log`: только тип исключения и строки кода, без аргументов
ошибки, значений переменных, текста документов и поисковых запросов.

- История буфера, дневник и быстрый поиск выключены по умолчанию и включаются
  отдельно. История — до 30 текстовых фрагментов в памяти, с очисткой при
  отключении или выходе. Это не зашифрованное хранилище секретов.
- Дневник сохраняет снимки разрешённых редакторов на диск с Windows DPAPI для
  текущего пользователя. По умолчанию разрешены только Блокнот и Notepad++.
  Срок хранения — 1–365 дней, исходно 7; автоочистка работает при включённом
  дневнике. Отключение останавливает чтение и запись, но старые записи остаются:
  для удаления есть кнопка «Удалить все записи». Время и имя приложения не
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

Известные менеджеры паролей исключены; исправление пропускает стандартные
парольные поля Win32 EDIT. У дополнительных функций свои проверки полей и
процессов. **Универсального распознавания секретов нет:** браузерное поле может
не сообщить о пароле, а переписка, ключ или криптоадрес могут выглядеть как обычный
текст. Не все приложения ставят отметки приватности при копировании. Добавляй
чувствительные приложения в исключения, отключай ненужные историю и дневник,
а перед вводом текста, который нельзя менять, отключай исправления. Для
переименованных EXE может понадобиться отдельное исключение.

Выключение автоисправления не выключает отдельно включённые историю, дневник и
поиск. Полностью завершает программу «Выход» в трее. Данные находятся в
`%LOCALAPPDATA%\TypeBridge`, дневник — в `journal\journal.sqlite3`. Резервные
копии и старая папка `%LOCALAPPDATA%\LayoutSwitch` могут хранить свои экземпляры.
Очистка в приложении не удаляет внешние резервные копии и историю браузера/сервиса.

Открытый код даёт возможность проверки, но не доказывает независимый аудит,
отсутствие ошибок или соответствие скачанного EXE исходникам. Предупреждение
антивируса нужно оценивать по конкретному файлу и источнику, а не отключать
защиту вслепую.

## עברית

TypeBridge מקבל אירועי מקלדת מ־Windows כדי לזהות מילים שהוקלדו בפריסה שגויה.
יכולת דומה קיימת גם ברושמי הקשות; ההבדל שחשוב לבדוק הוא מה מעובד, נשמר ונשלח.
התיקון הרגיל מחזיק את המילה הנוכחית והאחרונה להחלפה ידנית בזיכרון, בלי יומן
הקלדה קבוע. הודעות האבחון מציינות פעולות, שפות ומספר תווים ולא את המילים.
התיקון המוקדם ב־Notepad++ גם קורא זמנית טקסט מהמסמך, עד 64 ק״ב.
תקלה בלתי צפויה בממשק עשויה ליצור קובץ אבחון מוגבל בגודלו בשם `ui-errors.log`
בתיקיית הנתונים האישיים. נשמרים רק סוג השגיאה ומיקומי קוד, בלי ערכי משתנים,
ארגומנטים של החריגה, טקסט מסמכים או שאילתות חיפוש.

- היסטוריית הלוח, היומן והחיפוש המהיר מושבתים כברירת מחדל ומופעלים בנפרד.
  ההיסטוריה שומרת עד 30 פריטי טקסט בזיכרון ונמחקת בכיבוי התכונה או ביציאה.
  היא אינה כספת מוצפנת.
- היומן שומר צילומי טקסט מעורכים מורשים בדיסק, עם Windows DPAPI למשתמש
  הנוכחי. כברירת מחדל מורשים רק פנקס הרשימות ו־Notepad++. זמן השמירה הוא
  1–365 ימים, כברירת מחדל 7. הניקוי האוטומטי פועל כשהיומן מופעל. כיבויו אינו
  מוחק רשומות ישנות; ניתן למחוק אותן בחלון היומן. זמן הצילום ושם היישום אינם
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

מנהלי סיסמאות מוכרים מוחרגים. התיקון מדלג על שדות סיסמה רגילים מסוג Win32 EDIT;
לתכונות הנוספות בדיקות משלהן. **אין זיהוי אוניברסלי של סודות.** שדה בדפדפן עלול
לא לדווח שהוא מכיל סיסמה, ומפתחות, התכתבויות וכתובות קריפטו עשויים להיראות כטקסט
רגיל. לא כל יישום מסמן העתקה כפרטית. הוסף יישומים רגישים להחרגות והשבת היסטוריה
ויומן כשאין בהם צורך. לפני הקלדת טקסט שאסור לשנותו, השבת את התיקון. קובצי EXE
ששמם שונה עשויים לדרוש החרגה נוספת.

כיבוי התיקון האוטומטי אינו מכבה היסטוריה, יומן או חיפוש שהופעלו בנפרד. בחירת
יציאה במגש המערכת מסיימת את כל התכונות. הקבצים האישיים נמצאים בתיקייה
`%LOCALAPPDATA%\TypeBridge`, והיומן ב־`journal\journal.sqlite3`. גיבויים ותיקיית
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
