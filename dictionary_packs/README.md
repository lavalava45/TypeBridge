# Example exclusion dictionaries

These opt-in examples are adapted from
[CSpell dictionaries](https://github.com/streetsidesoftware/cspell-dicts), pinned
to commit `6e294247ab8932b5e10573d44636e2ce5764fab3`.
Each JSON records source URLs, SHA-256 hashes and the corresponding MIT license
notice. Full source snapshots are in `upstream/`; copyright/license notices are
preserved in `CSpell-*-LICENSE.txt`.

- `programming.json`: 848 English words from Python, a selected JavaScript /
  TypeScript keyword/basic API subset, and C++ keyword/jargon lists.
- `gaming.json`: 41 English gaming terms.

Processing: comments removed, only standalone alphabetic English words of
1–80 characters retained, case-folded and deduplicated. Identifiers containing
underscores, punctuation or digits are not split into partial exceptions.
These are small terminology examples, not complete language grammars.
They are never installed automatically and require no runtime download.

## Как подключить

«Мои слова и исключения» → «Импорт словарей…» → «Добавить» у нужного примера.
Повторное нажатие не создаёт дублей. Исключения объединяются с личным словарём,
сохраняются и применяются к работающему переключателю. Удалить отдельные
импортированные записи можно в общем списке личных слов и исключений.

Свой TXT должен быть в UTF-8: одно слово на строку. `#` в начале строки —
комментарий; комментарий после слова отделяется пробелом. Режим `auto`
определяет русский, английский или иврит по алфавиту каждого слова.
Есть выбор точного регистра. Неподдерживаемые записи показываются в предпросмотре,
а не превращаются в другие слова. Максимум 2 МБ / 50 000 записей.

```text
# My terminology
async
matchmaking
диагноз
חיסון
```

JSON supports a language and case mode per entry:

```json
{
  "format": "typebridge-exclusions",
  "version": 1,
  "name": "My terminology",
  "entries": [
    {"language": "en", "word": "API", "sensitive": true},
    {"language": "ru", "word": "диагноз", "sensitive": false}
  ]
}
```

Medical/legal term lists can use the same format. No medically or legally
reviewed domain dictionaries are included. Import does not verify the meaning
of a term or the redistribution license of a user-selected file.
