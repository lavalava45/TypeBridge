"""Startup dictionary diagnostics; never logs words or typed text."""
import re
from pathlib import Path
from datetime import datetime, timezone
from diagnostic_identity import computer_id


def record_debug_event(folder, kind, message):
    """Only our metadata prefixes and internal diagnostic codes may reach disk."""
    prefixes = ('Диагностика:', 'Загрузка словарей из ', 'Словари:', 'Словарь ',
                'Системные раскладки ', 'Защита паролей:')
    if not isinstance(message, str):
        return
    if kind == 'probe':
        # Reject arbitrary probe strings even if a future caller passes text.
        from pipeline_probe import EVENTS, FIELDS
        chunks = message.split(':')
        if (len(chunks) < 2 or chunks[0] != 'pipeline' or chunks[1] not in EVENTS
                or any('=' not in part or part.split('=', 1)[0] not in FIELDS
                       or not re.fullmatch(r'[a-zA-Z0-9_.+\-]{1,64}', part.split('=', 1)[1])
                       for part in chunks[2:])):
            return
    elif not (kind == 'diagnostic' or message.startswith(prefixes)):
        return
    try:
        path = Path(folder) / 'debug.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1_048_576:
            path.replace(path.with_suffix('.previous.log'))
        with path.open('a', encoding='utf-8') as stream:
            stream.write(f'{datetime.now(timezone.utc).isoformat()} [pc={computer_id()}] {message}\n')
    except OSError:
        pass  # A read-only USB drive must not break application control.


def dictionary_report(engine, directory):
    directory = Path(directory).resolve()
    result = [f"Словари: каталог {directory}"]
    for language in ('ru', 'en', 'he'):
        result.append(f"Словарь {language.upper()}: {directory / (language + '.txt')} · "
                      f"загружено {len(engine.words[language])} ключей; "
                      f"уверенных {len(engine.confident[language])} из "
                      f"{directory / ('confident_' + language + '.txt')}")
    return result
