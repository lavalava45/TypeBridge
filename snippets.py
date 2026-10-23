"""Literal text expansion rules and bounded recognition; no OS input calls."""
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import tempfile

MAX_TRIGGER = 64
MAX_TEXT = 2000
SEPARATORS = frozenset(' \t\r\n.,!?;:')


@dataclass(frozen=True)
class Snippet:
    trigger: str
    text: str
    sensitive: bool = True
    immediate: bool = True


@dataclass(frozen=True)
class SnippetConfig:
    enabled: bool = False
    rules: tuple[Snippet, ...] = ()

    def validate(self):
        if type(self.enabled) is not bool or len(self.rules) > 500:
            raise ValueError('Допустимо не более 500 правил автозамены')
        for rule in self.rules:
            if (type(rule.sensitive) is not bool or type(rule.immediate) is not bool
                    or not isinstance(rule.trigger, str) or not 1 <= len(rule.trigger) <= MAX_TRIGGER
                    or any(not c.isprintable() or c.isspace() or ord(c) > 0xFFFF
                           or 0xD800 <= ord(c) <= 0xDFFF for c in rule.trigger)):
                raise ValueError('Сокращение: от 1 до 64 символов без пробелов и emoji')
            if (not isinstance(rule.text, str) or not 1 <= len(rule.text) <= MAX_TEXT
                    or '\r' in rule.text or any(not c.isprintable() and c not in '\n\t' for c in rule.text)
                    or any(0xD800 <= ord(c) <= 0xDFFF for c in rule.text)):
                raise ValueError('Текст шаблона: от 1 до 2000 символов; допустимы переносы строк и табуляция')
        for i, left in enumerate(self.rules):
            for right in self.rules[i+1:]:
                a, b = left.trigger, right.trigger
                if not (left.sensitive and right.sensitive):
                    a, b = a.casefold(), b.casefold()
                left_blocks = b.startswith(a) and (left.immediate or (len(b)>len(a) and b[len(a)] in SEPARATORS))
                right_blocks = a.startswith(b) and (right.immediate or (len(a)>len(b) and a[len(b)] in SEPARATORS))
                if a == b or left_blocks or right_blocks:
                    raise ValueError('Сокращения повторяются или мгновенное правило перекрывает начало другого')
        return self

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(raw, dict) or not isinstance(raw.get('rules'), list):
            raise ValueError('Неверный формат файла автозамен')
        try:
            rules = tuple(Snippet(**rule) for rule in raw['rules'])
            return cls(raw.get('enabled', False), rules).validate()
        except TypeError as error:
            raise ValueError('Неверный формат правила автозамены') from error

    def save(self, path: Path):
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(self), stream, ensure_ascii=False, indent=2)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists(): temporary.unlink()


@dataclass(frozen=True)
class Expansion:
    delete: int
    text: str
    separator: str = ''


class SnippetMatcher:
    def __init__(self, config=SnippetConfig()):
        self.config = config.validate()
        self.exact = {}; self.folded = {}
        self.prefix_exact = set(); self.prefix_folded = set()
        for rule in config.rules:
            (self.exact if rule.sensitive else self.folded)[rule.trigger if rule.sensitive else rule.trigger.casefold()] = rule
            prefixes = self.prefix_exact if rule.sensitive else self.prefix_folded
            key = rule.trigger if rule.sensitive else rule.trigger.casefold()
            prefixes.update(key[:i] for i in range(1,len(key)+1))
        self.reset()

    def reset(self):
        self.buffer = ''; self.blocked = False

    def invalidate(self):
        self.buffer = ''; self.blocked = True

    def feed(self, char):
        """Return expansion and pending-prefix flag; current character is not delivered yet."""
        if not self.config.enabled:
            self.reset(); return None, False
        current = self.exact.get(self.buffer) or self.folded.get(self.buffer.casefold())
        if not self.blocked and char in SEPARATORS and current and not current.immediate:
            result = Expansion(len(self.buffer), current.text, char)
            self.reset()
            return result, False
        if not char or not char.isprintable() or char.isspace():
            self.reset(); return None, False
        if self.blocked:
            return None, False
        self.buffer += char
        if len(self.buffer) > MAX_TRIGGER:
            self.invalidate(); return None, False
        rule = self.exact.get(self.buffer) or self.folded.get(self.buffer.casefold())
        if rule and rule.immediate:
            result = Expansion(len(self.buffer)-1, rule.text)
            self.invalidate()
            return result, False
        return None, (self.buffer in self.prefix_exact or self.buffer.casefold() in self.prefix_folded)
