"""Research-only SendInput transaction model. No OS input or hooks on import.

An accepted input batch is not proof of edited text. A caller must observe its
marked events, verify the field, and release buffered input exactly once.
Uncertain input is retained in memory; it is never replayed into another field.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    scope: tuple
    text: str
    caret: int  # Python Unicode offset, converted by the observer.
    language: str
    selection: bool = False
    sensitive: bool = False


@dataclass(frozen=True)
class Key:
    vk: int = 0
    scan: int = 0
    flags: int = 0


@dataclass(frozen=True)
class Plan:
    source: Field
    expected: Field
    trigger: str
    events: tuple[Key, ...]


def plan(field, typed, replacement, target):
    if field.selection or field.sensitive or field.language not in ('ru', 'en', 'he'):
        raise ValueError('ineligible-field')
    if target not in ('ru', 'en', 'he') or target == field.language:
        raise ValueError('invalid-target')
    if not 2 <= len(typed) <= 40 or not 1 <= len(replacement) <= 64:
        raise ValueError('word-limit')
    if any(not ch.isalpha() or ord(ch) > 0xFFFF for ch in typed + replacement):
        raise ValueError('letters-only-probe')
    prefix = typed[:-1]  # Current physical character is still held by the hook.
    if (len(field.text) > 32768 or not len(prefix) <= field.caret <= len(field.text)
            or field.text[field.caret-len(prefix):field.caret] != prefix):
        raise ValueError('prefix-not-confirmed')
    first = field.caret - len(prefix)
    if first and not (field.text[first-1].isspace() or field.text[first-1] in '([{«"'):
        raise ValueError('not-independent-word')
    events = []
    for _ in prefix:
        events.extend((Key(vk=8), Key(vk=8, flags=2)))
    for char in replacement:
        events.extend((Key(scan=ord(char), flags=4), Key(scan=ord(char), flags=6)))
    expected = Field(field.scope, field.text[:first] + replacement + field.text[field.caret:],
                     first + len(replacement), target)
    return Plan(field, expected, typed[-1], tuple(events))


class Transaction:
    def __init__(self, operation):
        self.plan = operation
        self.accepted = None
        self.seen = set()
        self.forwarded = 0
        self.buffer = []
        self.state = 'queued'
        self.reason = ''
        self.cancelled = False
        self.released = False
        self.keys_down = set()

    @property
    def drained(self):
        return self.accepted is not None and len(self.seen) >= self.accepted

    @property
    def busy(self):
        return self.state in ('queued', 'delivering', 'verifying')

    def abort(self, reason):
        self.cancelled = True
        self.reason = self.reason or reason
        self.state = 'uncertain'

    def submitted(self, count):
        if self.accepted is not None:
            raise RuntimeError('never-resubmit-a-transaction')
        if not 0 <= count <= len(self.plan.events):
            raise ValueError('invalid-inserted-count')
        self.accepted = count
        if not count and not self.seen:
            self.state, self.reason = 'refused', 'no-events-accepted'
        elif count != len(self.plan.events):
            self.abort('partial-SendInput')
        elif self.drained and not self.cancelled:
            self.state = 'verifying'

    def own_event(self, index, scope, modifiers=False):
        if index in self.seen or not 0 <= index < len(self.plan.events):
            self.abort('unexpected-own-event')
            return False
        if index != len(self.seen):
            self.abort('own-event-order')
        self.seen.add(index)
        if scope != self.plan.source.scope:
            self.abort('focus-changed')
        if modifiers:
            self.abort('modifier-changed')
        event = self.plan.events[index]
        identity = (event.vk, event.scan, event.flags & 4)
        # A cancellation blocks further edits, but must not leave a previously
        # delivered Backspace/VK_PACKET held down. Only its matching keyup may
        # pass as cleanup; it is never a repeated deletion or text insertion.
        cleanup = bool(event.flags & 2 and identity in self.keys_down)
        forward = not self.cancelled or cleanup
        if forward:
            self.forwarded += 1
            if event.flags & 2: self.keys_down.discard(identity)
            else: self.keys_down.add(identity)
            if not self.cancelled: self.state = 'delivering'
        if self.drained and not self.cancelled:
            self.state = 'verifying'
        return forward

    def pending_keyups(self):
        return tuple(Key(vk=vk, scan=scan, flags=flags|2)
                     for vk, scan, flags in sorted(self.keys_down))

    def physical_event(self, event, scope):
        if scope != self.plan.source.scope:
            self.abort('focus-changed')
            return False  # New-window physical input is not intercepted.
        if not self.busy:
            return False
        self.buffer.append(event)
        return True

    def confirm(self, observed):
        if self.state != 'verifying' or not self.drained:
            raise RuntimeError('not-ready-to-confirm')
        if observed != self.plan.expected:
            self.abort('field-not-confirmed')
            return False
        self.state = 'verified'
        return True

    def release(self, scope):
        if self.state != 'verified' or scope != self.plan.source.scope or self.released:
            raise RuntimeError('unsafe-or-duplicate-replay')
        self.released = True
        buffered, self.buffer = tuple(self.buffer), []
        return buffered


MARKER_BASE = 0x5442000000000000
MARKER_MASK = 0xFFFF000000000000
REPLAY_MARKER = 0x5443000000000000


def marker(sequence, index):
    if not 0 <= index < 65536 or not 0 <= sequence < 2**32:
        raise ValueError('marker-range')
    return MARKER_BASE | sequence << 16 | index


def decode_marker(value):
    if value & MARKER_MASK != MARKER_BASE:
        return None
    return ((value >> 16) & 0xFFFFFFFF, value & 0xFFFF)


def input_batch(keys, *, sequence=None):
    # Reuse the already verified x64 ABI. This function only builds structures.
    import ctypes
    from typebridge_app import Input, KeyInput, InputUnion
    if ctypes.sizeof(Input) != 40 or ctypes.sizeof(KeyInput) != 24:
        raise RuntimeError('unsupported-INPUT-ABI')
    return (Input * len(keys))(*(Input(type=1, u=InputUnion(ki=KeyInput(
        key.vk, key.scan, key.flags, 0,
        REPLAY_MARKER | index if sequence is None else marker(sequence, index))))
        for index, key in enumerate(keys)))
