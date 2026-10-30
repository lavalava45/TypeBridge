# R16/4a — проект единого declarative language package v2

**Исторический проект R16/4a, зафиксированный до R16/4b.** Дата: 2026-10-29.
Параллельный parser/lexicon/decision v2 уже реализован в R16/4b, но
**в рабочем TypeBridge не активирован**. Результаты и ограничения см.
[`R16-4B-PARALLEL.md`](R16-4B-PARALLEL.md). Нынешний пользовательский
валидатор по-прежнему принимает только v1; ни один v2-пакет нельзя
установить или активировать в действующей сборке.

## Решение

Проектировать **v2 от полного набора проверенных существующих правил**, а
не приспосабливать RU/EN/HE к ограничениям внешнего v1 (`casefold` только).
Ни язык, ни его источник поставки не определяют special-case в новом ядре.
Все необходимые языковые различия задаются **декларативными** полями.

Schema: [`schemas/language-pack-v2.schema.json`](../schemas/language-pack-v2.schema.json).
Три проверяемых metadata-only контракта (НЕ installable manifests):
`fixtures/r16-4a-contract/{ru,en,he}.policy.json`.
Исходная таблица правил: [`R16-4A-RULE-INVENTORY.md`](R16-4A-RULE-INVENTORY.md).
Проверяемый пример структуры нового AR manifest (для тестового набора, не
для установки): `fixtures/r16-4a-contract/ar.manifest.v2.example.json`.

## Поля будущего `manifest.json` v2

Сохранить прежние `code`, `langid`, `unicode_script`, `alphabet`, имена
словарей, доверенные checksum/byte length, `source`, лицензию и список
альтернативных языков. Отличия и расширения:

```json
{
  "schema_version": 2,
  "package_version": "1.0.0",
  "code": "he",
  "name": "עברית",
  "langid": 1037,
  "unicode_script": "HEBREW",
  "alphabet": "אבגדהוזחטיכלמנסעפצקרשתךםןףץ",
  "normalization": {"dictionary": "nfkc_casefold_strip_combining"},
  "dictionary_file": "words.txt",
  "confident_file": "confident.txt",
  "additions": [
    {"filename": "he_custom.txt", "for_words": true,
     "for_confident": true,
     "normalization": {
       "words": "nfkc_casefold_strip_combining",
       "confident": "casefold"
     }}
  ],
  "alternatives": ["ru", "en"],
  "behavior": {
    "ambiguous_destinations": "ordered_priority",
    "early_prefix_aliases": [],
    "source_nonletter_keys": [
      {"vk": 81, "character": "/"},
      {"vk": 87, "character": "'"}
    ],
    "personal_alphabet_policy": "hebrew_letters"
  },
  "source": {
    "name": "REQUIRES LICENSE AUDIT BEFORE PACKAGING",
    "url": "", "license_id": "REQUIRES-AUDIT", "license_file": "LICENSE.txt"
  },
  "files": {
    "words.txt": {"bytes": 1, "sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
    "confident.txt": {"bytes": 1, "sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
    "he_custom.txt": {"bytes": 1, "sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
    "LICENSE.txt": {"bytes": 1, "sha256": "0000000000000000000000000000000000000000000000000000000000000000"}
  }
}
```

**Выше — только пример структуры**: нулевые хэши/размеры и источник
намеренно фиктивны; такой пакет **не соответствует реальным файлам**.
При миграции надо материализовать `SAMPLE_HEBREW` в проверенный
дополнительный файл `for_words=true, for_confident=true`, сохранив полный
состав. Правило его нормализации должно отразить именно текущую вставку
строк в готовые множества, без неявных новых преобразований.

## Семантика v2: отдельно от файлов

### Параметры языка

- `normalization.dictionary`: строго определённые процедуры ядра
  `nfkc_casefold_strip_combining`,
  `nfkc_casefold_strip_combining_yo_to_e`, `casefold`. Каждая версия ядра
  тестирует их побуквенно, включая `ё`, combining marks, Unicode NFC/NFKC.
- Для каждого словарного `addition` отдельно задаются `for_words`,
  `for_confident` и **своя нормализация для каждой роли**. Поэтому
  исторический `he_custom.txt` остаётся words + confident, но его слова
  для обычного словаря проходят NFKC/casefold/deaccent, а для confidence —
  только casefold. Это существующее *неявное* поведение реестра. А
  встроенные RU/EN/HE добавления, если потребуется, переходят в проверенные
  обычные payloads с теми же ролями.
- `behavior.early_prefix_aliases`: фиксированные алиасы из кода ядра, сейчас
  только `yo_to_e`. Алиасы не меняют орфографию вводимого слова, лишь поиск
  кандидатов в prefix index.
- `behavior.source_nonletter_keys`: явно разрешённые **физические VK**,
  которые могут означать пунктуацию в исходной раскладке и букву в другой.
  Стандартный OEM набор и границы токена остаются в ядре; это расширения
  только из проверенного whitelist, а не произвольное выполнение правил.
- `behavior.personal_alphabet_policy`: определяет допустимые буквы для
  личного словаря. Общие `NFC`, длина, sensitive/insensitive и format v1
  совместимости реализуются ядром/профильным адаптером, **не в пакете**.
- `alternatives`: порядок предпочтительных языков **только при допустимом
  разрешении неоднозначности**. Никакого implicit sorting, частотного
  tie-breaker или угадывания незнакомого слова.
- `behavior.ambiguous_destinations`: `ordered_priority` или
  `reject_multiple`. Составная политика активного набора: **если хотя бы
  один активный пакет требует `reject_multiple`, любое неоднозначное
  назначение отвергается**. Иначе применяется порядок `alternatives`
  текущего языка. Это сохраняет RU/EN/HE и осторожность AR/ES, не проверяя
  в ядре `lang in LANGUAGE_BY_CODE`.

### Системные параметры ядра, **не** поля каждого пакета

- Максимум 3 активных языка; длина слова 1–40 key pairs и текущая политика
  OEM/affix/token boundary.
- Осторожность: multi-letter confident requirement; source known/excluded
  veto; смешанный script veto; правильность раскладки HKL;
  source/target candidate graph.
- Early: `MIN_EARLY_LENGTH=5`, `MAX_UNFINISHED_LETTERS=2`, различия
  `suggest_early`, `suggest_live_early`, `evaluate_unique_language`.
- Личные словари (NFC, exact/insensitive), глобальный `exclude.txt`,
  auto-correction и горячие клавиши, No01, Undo, off-hook editor write.
- Фактическая раскладка Windows выбирается среди установленных HKL;
  пакет **не** устанавливает input method и не запускает Python/скрипт.

## Против фиктивного универсального legacy-флага

Нельзя включать в v2 поля `legacy: true`, `built_in: true` или
`always_rank: true because RU`. Существующая история формализуется как
комбинация `normalization`, `additions`, `prefix_aliases`, `nonletter_keys`
и `ambiguous_destinations`. Для текущих RU/EN/HE — все три имеют
`ordered_priority`; демонстрационные AR/ES по безопасному правилу должны
иметь `reject_multiple`.

Тем не менее **миграционные адаптеры хранения** необходимы до удаления
устаревшего формата. Они могут распознавать `direct_ru`, `direct_en`,
`direct_he`, старые `settings.json` и `words-v1.cache`, но эта совместимость
не является функцией языкового decision engine или файла пакета.

## Требования к будущему валидатору и пакетному менеджеру

`JSON Schema` — **не достаточная проверка безопасности**. После проверки
формы валидатор обязан также:

1. Проверить UTF-8, отсутствующие/дублирующиеся поля, дублирующиеся имена,
   пересечение `dictionary_file`/`confident_file`/`additions`/license.
2. Сверить все реальные байты, SHA256 и limits; отказаться от symlink,
   junction, hidden/nested/исполняемых файлов и незаявленного содержимого.
3. Проверить уникальность `code`, Windows `langid`, допустимый Unicode script,
   alphabet и отсутствие конфликтов с другими установленными пакетами.
4. Проверить `source_nonletter_keys` по whitelist ядра и дополнительно
   убедиться, что символ соответствует `ToUnicodeEx` для конкретной HKL.
5. Не допускать recursive priority и невозможных альтернатив без явной
   проверки порядка / composition. Отсутствующий целевой язык не должен
   создавать ложное исправление; применение `alternatives` пересекается с
   реальным активным набором.
6. Контролировать обновление: до commit подготовить валидированную копию,
   после commit инвалидировать *только* зависящий от него кэш. Никаких
   настроек/пользовательских слов нельзя удалить при отказе установки.

## Статус и план миграции

1. **R16/4a (этот этап):** полная инвентаризация, project-level policy
   snapshots, новая schema **без подключения** её к runtime, golden-решения
   текущего `LanguageEngine`.
2. **R16/4b/1:** отдельно реализовать strict v2 parser/validator и
   read-only adapter. Проверить эквивалентность лексических множеств и
   нормализации без изменения рабочего движка.
3. **R16/4b/2:** реализовать второй decision adapter; прогнать один и тот же
   pinned corpus на старом и новом; сравнивать **включая `None`, `reason`
   и каждый ранний префикс**, не только шесть удачных направлений.
4. **R16/4c:** универсальные settings/hotkeys с legacy storage bridge,
   миграция двумя атомарно восстанавливаемыми файлами, сохранение dormant
   lexicon и ошибочного неактивного пакета.
5. **R16/4d:** GUI/install/remove/update пакетов без Python расширений и
   без изменения активного hook; переход новых языков только после restart.
6. **R16/4e:** лишь после подтверждения parity удалить старый loader/cache
   runtime-ветки, оставить только migration compatibility layer и rollback.

## Текущая измеримая граница эквивалентности

`r16_4a_equivalence.py` и `test_r16_4a_contract.py` закрепляют **19
детерминированных old-engine cases** (`decision-oracle.json`) и **133
префикса** (`prefix-oracle.json`) без настоящих клавиатурных событий.
Оракул сравнивает `Decision | None` *целиком*, в том числе `typed`, `reason`,
`language` и `replacement`; для раннего ввода — каждую длину префикса.
Тест сравнения также специально мутирует два решения, чтобы доказать, что
будущая несовместимость обнаруживается.

**Обновление R16/4b (29.10.2026):** теперь есть действительно отдельный
экспериментальный `language_decision_v2.PolicyDecisionEngine`, и его
сравнение с прежним LanguageEngine прошло на 19 pinned `Decision | None`,
133 pinned prefix и 160 детерминированных дополнительных моделях.
Сведения о полном сравнении словарей — в `R16-4B-PARALLEL.md`. Это не
доказательство эквивалентности для всех реальных вводов и не разрешение
подключать новый движок к физическому hook. Нативные испытания и отдельные
физические проверки обязательны до выпуска.

**Строгий release gate:** source parity ≠ физический No01. Честный статус
No01 — `BLOCKED_PENDING_PHYSICAL_INPUT`. Лицензии реальных RU/EN/HE payloads
и полноразмерные AR/ES должны быть проверены отдельно.
