"""Startup dictionary diagnostics; never logs words or typed text."""
import re
from pathlib import Path
from datetime import datetime, timezone
from diagnostic_identity import computer_id


def debug_message(kind, message):
    """Validate metadata without touching disk; shared by the batch writer."""
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
    return message


def record_debug_event(folder, kind, message):
    """Only our metadata prefixes and internal diagnostic codes may reach disk."""
    message = debug_message(kind, message)
    if message is None:
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


def dictionary_report(engine, directory, *, pack_root=None):
    directory = Path(directory).resolve()
    result = [f"Словари: каталог {directory}"]
    catalog = getattr(engine, 'language_catalog', None)
    policies = getattr(engine, 'language_policies', None)
    if catalog is None:
        # Only the opt-in legacy backend lacks a verified runtime catalog.
        from language_registry import LANGUAGE_BY_CODE
        catalog = LANGUAGE_BY_CODE
    active = getattr(engine, 'active_languages', tuple(engine.words))
    for code in active:
        # Only report dictionaries actually participating in this engine.
        # The user's disabled languages must not trigger a KeyError or appear
        # as loaded. Never include dictionary contents or user-entered words.
        words = engine.words[code]
        confident = engine.confident[code]
        description = catalog[code]
        if policies is not None and code in policies:
            # The unified loader records the *actual* verified source for
            # every selected language, including bundled RU/EN/HE v2 packs.
            # Do not infer the source from historical language identifiers.
            base = policies[code].directory
        else:
            # Compatibility with engines predating the unified policy catalog.
            # The old registry is consulted only in this legacy diagnostic path.
            from language_registry import LANGUAGE_BY_CODE
            base = (directory if code in LANGUAGE_BY_CODE else
                    Path(pack_root).resolve() / code if pack_root is not None
                    else None)
        word_file = str(base / description.dictionary_file) if base is not None else description.dictionary_file
        confidence_file = str(base / description.confident_file) if base is not None else description.confident_file
        result.append(f"Словарь {code.upper()}: {word_file} · "
                      f"загружено {len(words)} ключей; "
                      f"уверенных {len(confident)} из {confidence_file}")
    return result
