"""Selection-cycle core and range-adapter contract for the manual command.

No clipboard, UIA, HWND, SendInput or persistence. Positions are explicitly
tagged; a real adapter must supply exact selection snapshots and one native
Undo action while retaining the resulting selection. Models do not prove that
any external editor implements this contract.
"""
from dataclasses import dataclass, field, replace
from language_registry import validate_active_languages

MAX_SELECTION = 2000


def text_units(text, unit):
    if unit == 'codepoint':
        return len(text)
    if unit == 'utf16':
        return len(text.encode('utf-16-le')) // 2
    if unit == 'utf8':
        return len(text.encode('utf-8'))
    raise ValueError('unsupported-position-unit')


@dataclass(frozen=True)
class SelectionSnapshot:
    scope: tuple
    revision: int  # Recipient/focus epoch, not a document edit counter.
    anchor: int
    caret: int
    text: str
    unit: str

    def __post_init__(self):
        if (not isinstance(self.text, str) or '\0' in self.text
                or len(self.text) > MAX_SELECTION
                or type(self.anchor) is not int or type(self.caret) is not int
                or min(self.anchor, self.caret) < 0
                or abs(self.anchor-self.caret) != text_units(self.text, self.unit)):
            raise ValueError('invalid-selection-snapshot')

    @property
    def start(self):
        return min(self.anchor, self.caret)


@dataclass(frozen=True)
class SelectionPlan:
    before: SelectionSnapshot
    after: SelectionSnapshot
    source_language: str
    language: str
    sequence: int
    owner: object = field(repr=False)


@dataclass(frozen=True)
class SelectionOutcome:
    state: str  # verified / refused / uncertain
    reason: str


class SelectionCycle:
    def __init__(self, snapshot, language, mapper, active_languages):
        self.active = validate_active_languages(active_languages, active_languages)
        if language not in self.active or len(self.active) < 2:
            raise ValueError('no-next-active-layout')
        if not snapshot.text:
            raise ValueError('empty-selection')
        self.snapshot, self.language, self.mapper = snapshot, language, mapper
        self.owner, self.closed = object(), False
        self.sequence = 0
        reverse = {}
        for shift in (False, True):
            for vk in (*range(48, 58), *range(65, 91),
                       *range(0xBA, 0xC1), *range(0xDB, 0xDF)):
                char = mapper.char(vk, language, shift)
                if len(char) == 1 and char.isprintable() and not char.isspace():
                    # The text alone cannot identify duplicate physical keys.
                    # Prefer an unshifted representation, then the lowest VK.
                    reverse.setdefault(char, (vk, shift))
        self.atoms = tuple(reverse[char] if char in reverse and not char.isspace()
                           else char for char in snapshot.text)
        if not any(isinstance(atom, tuple) for atom in self.atoms):
            raise ValueError('no-mappable-selection-characters')

    def plan_next(self):
        if self.closed:
            raise ValueError('selection-cycle-closed')
        language = self.active[(self.active.index(self.language)+1) % len(self.active)]
        chars = []
        for atom in self.atoms:
            char = self.mapper.char(atom[0], language, atom[1]) if isinstance(atom, tuple) else atom
            if isinstance(atom, tuple) and (len(char) != 1 or not char.isprintable()):
                raise ValueError('unmapped-destination-character')
            chars.append(char)
        text = ''.join(chars)
        first = self.snapshot.start
        last = first + text_units(text, self.snapshot.unit)
        anchor, caret = ((first, last) if self.snapshot.anchor < self.snapshot.caret
                         else (last, first))
        after = replace(self.snapshot, anchor=anchor, caret=caret, text=text)
        return SelectionPlan(self.snapshot, after, self.language, language, self.sequence, self.owner)

    def accept(self, plan, observed):
        if (self.closed or plan.owner is not self.owner or plan.before != self.snapshot
                or plan.sequence != self.sequence
                or plan.source_language != self.language or observed != plan.after):
            raise ValueError('selection-plan-no-longer-current')
        self.snapshot, self.language = observed, plan.language
        self.sequence += 1


def replace_selection(cycle, bridge, still_valid):
    """One prepared range write, no retry, no blind Undo after uncertainty.

    bridge.prepare(plan) is non-mutating. bridge.replace(plan) must compare the
    original range again, perform one native Undo action and keep the new text
    selected. write_started must be set before the first possible mutation.
    """
    attempted = False
    try:
        plan = cycle.plan_next()
        if not still_valid() or bridge.snapshot() != plan.before:
            cycle.closed = True
            return SelectionOutcome('refused', 'selection-changed-before-prepare')
        bridge.prepare(plan)
        if not still_valid() or bridge.snapshot() != plan.before:
            cycle.closed = True
            return SelectionOutcome('refused', 'selection-changed-before-write')
        attempted = True
        bridge.replace(plan)
        observed = bridge.snapshot()
        if observed != plan.after or not still_valid():
            cycle.closed = True
            return SelectionOutcome('uncertain', 'selection-write-not-confirmed')
        cycle.accept(plan, observed)
        return SelectionOutcome('verified', 'selection-replacement-confirmed')
    except Exception as error:
        cycle.closed = True
        possible_write = attempted and bool(getattr(bridge, 'write_started', True))
        return SelectionOutcome('uncertain' if possible_write else 'refused',
                                type(error).__name__)
