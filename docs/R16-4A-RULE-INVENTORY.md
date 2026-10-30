# R16/4a — Инвентаризация действующего языкового поведения

**Статус: baseline / architectural contract.** Дата: 2026-10-29. Это
описание *текущего исходного кода*, а не обещание новой реализации. В этом
этапе не изменяются движок, загрузчик, личные словари, конфигурация, hook,
пользовательские файлы, работающий EXE или Windows HKL.

## A. По-языковые правила, которые нельзя потерять

| Правило | RU | EN | HE | Источник истины |
|---|---|---|---|---|
| LANGID | `0419` | `0409` | `040D` | `language_registry.LANGUAGE_REGISTRY` |
| Script | `CYRILLIC` | `LATIN` | `HEBREW` | `language_registry.py`, `engine._script` |
| Входные слова + уверенные слова | `ru.txt` + `confident_ru.txt` | `en.txt` + `confident_en.txt` | `he.txt` + `confident_he.txt` | `engine._read_lexical_data` |
| Нормализация основных слов | NFKC → casefold → удалить combining marks → `ё→е` | NFKC → casefold → удалить combining marks | NFKC → casefold → удалить combining marks | `frequency_rank.normalize_word` |
| Динамические встроенные дополнения | `а`, `и`, `поясни` → **words + confident** | `a`, `i` → **words**, но **не confident** | `SAMPLE_HEBREW` → **words + confident** | `engine._read_lexical_data` |
| Дополнительный файл | нет | нет | `he_custom.txt` → **words** с NFKC/casefold/deaccent **и confident** с простым casefold | `language_registry.py`, `engine._read_lexical_data/read_words` |
| Ранние alias слова | Дополнительное `ё→е` в двух индексах (base/overlay) | нет | нет | `incremental_recognition.build_early_index/cached_early_index` |
| Особые буквенные позиции | Общий набор OEM-знаков | Общий набор OEM-знаков | Общий набор OEM + VK_Q=`/`, VK_W=`'`, если другой активный язык считает клавишу буквой | `engine.candidate_token_key/_valid_typed` |
| Приоритет назначения при неоднозначности | EN, HE | RU, HE | RU, EN | `ALTERNATIVE_PRIORITY`, `resolve_ambiguity` |
| Старые direct hotkeys (совместимость) | Ctrl+Shift+F1 | Ctrl+Shift+F2 | Ctrl+Shift+F3 | `direct_hotkeys.py`, `typebridge_app.Settings` |

**Важно:** `he_custom.txt` — не полноценный пользовательский словарь из
`UserLexicon`, а *словарный дополнительный источник*. В реестре создан через
`DictionaryAddition("he_custom.txt", for_confident=True)`, но `for_words`
имеет **неявное значение `True` по умолчанию**. Слова из файла поэтому
попадают в **оба** множества с разной нормализацией. Это важно сохранить,
а не подменить «только confident». При преобразовании `SAMPLE_HEBREW`
в файл его элементы, порядок нормализации и статус в обоих множествах должны
оставаться побайтно/семантически эквивалентными. Данные нельзя синтетически
заменить меньшим списком.

## B. Общие правила ядра (не должны дублироваться в пакетах)

| Правило | Существующее поведение | Код |
|---|---|---|
| Активный набор | 1–3 уникальных языка, порядок пользователя сохраняется; только активные участвуют в поиске, цикле и hotkeys | `language_registry.validate_active_languages`, `word_cycle`, `direct_hotkeys` |
| Перевод физической клавиши | Windows `ToUnicodeEx` с уже существующей HKL, с учётом shift и специальной проверки пользовательского KLID | `engine.LayoutMapper` |
| Без установки посторонней раскладки | Нет слепого `LoadKeyboardLayoutW`: только привязка конкретного уже имеющегося пользователя KLID или отказ | `LayoutMapper` и `select_loaded_layouts` |
| Границы слова | Максимум 40 key pairs; `WORD_AFFIXES`, OEM-пунктуация при возможности буквы в другом активном языке, защита от mixed scripts | `engine.candidate_token_key/_valid_typed/decide_token` |
| Нельзя переписывать известное слово | Если источник существует в словаре или исключениях, автокоррекция отказывается | `LanguageEngine.decide` |
| Кандидаты назначения | Сначала словарь, затем только *однозначный* или допустимый policy tie-break; отклонение, если мало уверенности | `LanguageEngine.decide/resolve_ambiguity` |
| Уверенность | Консервативная обработка многосимвольных слов требует confident; односимвольное — уникальное известное слово при прочих защитах | `LanguageEngine.decide` |
| Приоритеты против отказа | Только RU/EN/HE допускают ranked priority; если *хотя бы один* активный язык внешний v1, несколько правдоподобных назначений отвергаются | `LanguageEngine.resolve_ambiguity` — **переходная ветвь, нуждается в явной композиционной policy** |
| Ранний ввод | Обычный conservative prefix от 5 букв, не более 2 ещё не введённых букв; исследовательский режим от 4 — отдельная ветвь, не лицензия редактировать поле | `incremental_recognition.py` |
| Защита раннего ввода | Shift/заглавные/mixed scripts/известный source prefix/конкурирующие target prefix/исключения отменяют предложение | `incremental_recognition._suggest/_suggest_rare_exact` |
| Ранний rare | Только явно разрешённое уникальное *полное* слово, без угадывания продолжения | `suggest_live_early/_suggest_rare_exact` |
| Частотность | Исторический `frequency_rank.resolve_tie` существует для offline анализа, **не участвует** в live selection; не переносить его в новую policy | `frequency_rank.py`, `engine.resolve_ambiguity` |
| Замена текста/Undo | Выполняются за пределами declarative pack, в `LiveHook`, native bridges и transaction queue. Пакет никогда не внедряет код ввода | `typebridge_app`, `early_native_edit`, `native_transaction_queue` |
| Исключения | Общий `exclude.txt` и личные языковые exclusions; чувствительность личных записей сохраняется | `engine._excluded`, `user_lexicon` |

## C. Два разных контракта нормализации

1. **Словарь движка:** см. таблицу A. Для внешнего пакета v1 — *только*
   `casefold`. RU/EN/HE используют NFKC/deaccent, причём RU делает `ё→е`.
2. **Личный словарь:** ввод NFC, длина 1–80 букв, запись `sensitive` хранится
   точно, `insensitive` использует casefold. Проверка алфавита:
   RU — Cyrillic `А..я` плюс `Ёё`; EN — ASCII A–Z/a–z; HE — Unicode
   `U+05D0..U+05EA`; внешний — проверка Unicode character name на script.
   **Нельзя** механически заменить этот контракт нормализацией словаря.
3. Ранний индекс отдельно использует `casefold` личных добавлений (включая
   case-sensitive записи) и строит RU `ё→е` alias. Изменение этого алгоритма
   может изменить момент подсказки, даже если конечное слово совпадает.

## D. Риски совместимости и границы пакета

- Сегодня `LANGUAGE_BY_CODE` и `LANGS` захардкожены как RU/EN/HE в
  `engine.py`, `dictionary_cache.py`, `user_lexicon.py`, `direct_hotkeys.py`,
  `settings_language_profile.py`. Это не свойства русского алфавита, а
  **отдельные ветви миграции**: после перехода нужен compatibility-adapter,
  не скрытый runtime-признак `builtin`.
- Словари: legacy v1 кэш `words-v1.cache` и пакетный v2 `words-v2-*` имеют
  разные ключи/верификацию. Новый общий кэш обязан учитывать **версию policy,
  все источники, порядок и состав активных языков**, даже когда содержимое
  пакета одинаково.
- Установлено, активно и доступно в Windows — три разных состояния. Смена
  состава активных языков до перезапуска не меняет уже работающий hook;
  проверка hotkey должна учитывать также работающий состав.
- Форматы сохранения: `settings.json` + `settings_languages.json` (journal
  `settings_pair_transaction.json`), `user_lexicon.json` +
  `user_lexicon_packages.json`. При повреждении неактивного пакета его
  личные слова должны оставаться dormant, а не исчезать.
- UI-язык приложения (`ui_translations.py`) — **не** активный язык
  клавиатуры. Добавление ES/AR не означает автоматическую локализацию UI.
  Названия и выбор установленных языков в главном GUI уже каталогизируются,
  но редактор импорта исключений (`dictionary_import.py` и
  `dictionary_import_gui.py`) сегодня явно ограничен RU/EN/HE и `auto`.
  Его надо переводить на выбранные языки отдельно от core engine.
- `caret_indicator.py` и `caret_process.py` по умолчанию используют
  legacy `LANGS`, но могут принимать явный каталог; новая универсальная
  маршрутизация должна проверять все вызовы с активным набором, включая
  отдельный процесс и новые HKL, **не придумывая внешний UI-перевод**.
- Установка, удаление и обновление пакета — отдельные операции менеджера;
  никакие скрипты/Python-модули из пакета не исполнять, никакие HKL не
  устанавливать из пакета. Плохой *неактивный* пакет изолируется; плохой
  **выбранный** язык даёт fail-closed.
- Возможные поведения AR/ES ещё не считаются production-образцом: текущие
  демонстрационные пакеты малы, RTL и общая латиница нуждаются в физической
  проверке. No01 — `BLOCKED_PENDING_PHYSICAL_INPUT`.

## E. Условия утверждения R16/4b

Для одного и того же неизменного набора sources, HKL fixture, активных
языков, клавиш, личных слов, excluded words и режима conservative:

1. Для **каждого** конечного токена `Decision | None` должны совпасть:
   `typed`, `replacement`, `language`, `reason` (включая отказы).
2. Для **каждого префикса** `suggest_live_early` должны совпасть результат,
   первая допустимая длина и причина отказа; сравнивается также редкий режим.
3. Подготовленные `words`, `confident`, `exclusions` и content/fingerprint
   кэша должны совпасть семантически; cache hit/miss не влияет на решения.
4. Клавиатурная передача и HKL привязка остаются на прежнем коде: сравниваем
   передаваемые `Decision`, scope и разрешённую операцию, не вводим новый hook.
5. **Никаких исключений для RU/EN/HE** в новом decision engine. Переходный
   legacy adapter допустим только на границе чтения старых настроек/словарей.
6. Не вводить новую функциональность (другие tie-break/normalizer) в одном
   коммите с миграцией, пока точная эквивалентность не доказана.

Для реализации нужны также end-to-end проверки импорта исключений,
индикатора у каретки и жизненного цикла UI. **Их наличие не доказывается
этим baseline-контрактом:** здесь валидируются язык, словари и решение
движка; GUI/editor parity относится к следующим этапам.

Тестовые образцы и machine-readable project-level policy: см.
[`R16-4A-PACK-CONTRACT.md`](R16-4A-PACK-CONTRACT.md),
`fixtures/r16-4a-contract/` и `test_r16_4a_contract.py`.
