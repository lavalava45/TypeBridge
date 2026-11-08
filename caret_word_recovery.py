"""Bounded word-at-caret recovery after a verified UIA field check.

No UIA, input, clipboard or processes on import. The helper reads only the
short caret range of the expected element; it never reads Value/Document text.
"""
from dataclasses import dataclass
import hashlib
from engine import WORD_AFFIXES


@dataclass(frozen=True)
class CaretWord:
    text: str
    keys: tuple
    suffix: str
    caret_offset: int | None = None
    # Bind two preflight reads to the SAME focused UIA element, including
    # when the password guard deliberately permits unknown/stale metadata.
    element_id: str = ''
    # Ephemeral fingerprint of the short text *after the whole word*, excluding
    # any right half when the caret is inside the word. The right half changes
    # on a successful cycle and must not invalidate a legitimate next Pause.
    after_digest: str = ''


@dataclass(frozen=True)
class SelectedWord:
    """Exact selected word with a read-only pre-send verification token."""
    text: str
    keys: tuple
    element_id: str
    generation: int
    digest: str
    lease: str
    simple_mode: bool = False


def parse_selected_word(sample, mapper, language, element_id, *,
                        active_languages=None, simple_mode=False):
    if (not isinstance(sample, dict) or set(sample) !=
            {'state', 'text', 'element_id', 'generation', 'digest', 'lease'}
            or sample.get('state') != 'ok'
            or (element_id and sample.get('element_id') != element_id)
            or not isinstance(sample.get('element_id'), str)
            or not sample['element_id'].startswith('uia:')
            or not sample['element_id'][4:]
            or (not element_id and not simple_mode)
            or type(sample.get('generation')) is not int or sample['generation'] < 0
            or not isinstance(sample.get('lease'), str)
            or len(sample['lease']) != 32
            or any(ch not in '0123456789abcdef' for ch in sample['lease'])):
        return None
    text = sample.get('text')
    if (not isinstance(text, str) or not 1 <= len(text) <= 40
            or any(not ch.isprintable() or ch.isspace() for ch in text)
            or sample.get('digest') != hashlib.sha256(text.encode('utf-16-le')).hexdigest()):
        return None
    # Reuse the proven exact physical-key parser. It MUST consume the whole
    # selection, not trim punctuation or turn a partial selection into a word.
    word = parse_caret_word(
        {'before': text, 'after': '', 'at_start': True, 'at_end': True,
         'element_id': sample['element_id']}, mapper, language, sample['element_id'],
        active_languages=active_languages)
    if word is None or word.text != text or word.suffix or word.caret_offset is not None:
        return None
    return SelectedWord(text, tuple(word.keys), sample['element_id'],
                        sample['generation'], sample['digest'], sample['lease'], simple_mode)


def read_selected_word(context, mapper, element_id, valid, *,
                       active_languages=None, simple_mode=False, on_refusal=None):
    """Fetch current selection, NOT caret word; never mutate focused text."""
    if (not isinstance(element_id, str)
            or (not simple_mode and (not element_id.startswith('uia:') or not element_id[4:]))
            or (simple_mode and element_id and not element_id.startswith('uia:'))
            or not valid()):
        if on_refusal is not None:
            on_refusal('selection-client-target-unverified')
        return None
    try:
        from uia_native_worker import get_uia_service
        request = {
            'command': 'selected-word', 'mode': 'read', 'top': context.top,
            'focus': context.focus, 'element_id': element_id}
        if simple_mode:
            request['simple_mode'] = True
        if on_refusal is not None:
            request['diagnostic'] = True
        sample = get_uia_service().request(request, valid=valid)
        if not valid():
            if on_refusal is not None:
                on_refusal('selection-client-context-changed')
            return None
        if not isinstance(sample, dict) or sample.get('state') != 'ok':
            if on_refusal is not None:
                reason = sample.get('reason') if isinstance(sample, dict) else None
                # Never permit arbitrary provider content into the debug log.
                permitted = {
                    'lease-service-unavailable', 'invalid-selection-request',
                    'invalid-verification-token', 'invalid-request-fields',
                    'selection-lease-mismatch', 'focused-element-unavailable',
                    'element-identity-unavailable', 'selection-target-unverified',
                    'text-pattern-unsupported', 'selection-count-unverified',
                    'selection-collapsed', 'selected-text-invalid-type',
                    'selected-text-out-of-bounds',
                    'selected-text-invalid-characters',
                    'selection-changed-or-target-unverified',
                    'selection-lease-recheck-failed',
                    'selection-provider-exception', 'selection-unverified'}
                on_refusal(reason if reason in permitted else 'selection-service-unavailable')
            return None
        word = parse_selected_word(sample, mapper, context.language, element_id,
                                   active_languages=active_languages,
                                   simple_mode=simple_mode)
        if word is None and on_refusal is not None:
            on_refusal('selection-word-parser-refused')
        return word
    except (OSError, ValueError, TypeError, UnicodeError):
        if on_refusal is not None:
            on_refusal('selection-client-reader-exception')
        return None


def verify_selected_word(context, word, valid):
    """Recheck same selection identity and digest immediately before a send.

    The caller must ALSO enforce matching HKL/password revision and perform
    only selection-replacement input, never blind Backspace-prefix packets.
    """
    if not isinstance(word, SelectedWord) or not valid():
        return False
    try:
        from uia_native_worker import get_uia_service
        request = {
            'command': 'selected-word', 'mode': 'verify', 'top': context.top,
            'focus': context.focus, 'element_id': word.element_id,
            'generation': word.generation, 'digest': word.digest,
            'lease': word.lease}
        if word.simple_mode:
            request['simple_mode'] = True
        sample = get_uia_service().request(request, valid=valid)
        return bool(valid() and sample == {'state': 'matched'})
    except (OSError, ValueError, TypeError):
        return False


def parse_caret_word(sample, mapper, language, element_id, *,
                     active_languages=None, allow_following_word=False):
    if not isinstance(sample, dict) or sample.get('element_id') != element_id:
        return None
    before, after = sample.get('before'), sample.get('after')
    if (not isinstance(before, str) or not isinstance(after, str)
            or len(before.encode('utf-16-le')) > 162
            or len(after.encode('utf-16-le')) > 162):
        return None
    reverse = {}
    word_chars = set()
    languages = tuple(active_languages or mapper.layouts)
    for shift in (False, True):
        for vk in (*range(65, 91), *range(0xBA, 0xC1), *range(0xDB, 0xDF)):
            char = mapper.char(vk, language, shift)
            if len(char) == 1:
                reverse.setdefault(char, []).append((vk, shift))
                if any(mapper.char(vk, lang, shift).isalpha() for lang in languages):
                    word_chars.add(char)
    def in_word(char):
        # EN punctuation can be a letter on the very same physical key in RU/HE.
        return char.isalpha() or char in word_chars
    end = len(before.rstrip(' \u00a0'))
    # Manual tracking keeps unambiguous literal punctuation AFTER the word as
    # a suffix, e.g. "ghbdtn!  " -> word "ghbdtn", suffix "!  ".  Do not
    # discard OEM punctuation that is a LETTER on another active layout:
    # that physical key belongs to the word and must be mapped as such.
    while end and before[end - 1] in WORD_AFFIXES and not in_word(before[end - 1]):
        end -= 1
    suffix = before[end:]
    start = end
    while start and in_word(before[start - 1]):
        start -= 1
    left = before[start:end]
    right_end = 0
    if not suffix or not allow_following_word:
        while right_end < len(after) and in_word(after[right_end]):
            right_end += 1
    right = after[:right_end]
    if right_end == len(after) and right and sample.get('at_end') is not True:
        # The bounded UIA range may have truncated an unfinished right half.
        return None
    if right and suffix:
        return None  # Default parser contract: ambiguous changed middle.
    # Explicit manual-word verification may read the preceding word after
    # spaces even if a DIFFERENT word begins immediately after the caret.
    word = left + right
    if (not 1 <= len(word) <= 40 or len(suffix) > 40
            or (start and not (before[start - 1].isspace()
                               or (before[start - 1] in WORD_AFFIXES
                                   and not in_word(before[start - 1]))))
            or (not start and sample.get('at_start') is not True)):
        return None
    if any(len(reverse.get(char, ())) != 1 for char in word):
        return None
    keys = tuple(reverse[char][0] for char in word)
    if mapper.word(list(keys), language) != word:
        return None
    after_digest = hashlib.sha256(
        (('E' if sample.get('at_end') is True else 'N') + after[right_end:])
        .encode('utf-16-le')).hexdigest()
    return CaretWord(word, keys, suffix, len(left) if right else None,
                     element_id, after_digest)


def read_caret_word(context, mapper, element_id, valid, *,
                    active_languages=None, allow_following_word=False,
                    simple_mode=False, on_refusal=None):
    if (not isinstance(element_id, str)
            or (element_id and not element_id.startswith('uia:')) or not valid()):
        return None
    try:
        from uia_native_worker import get_uia_service
        request = {
            'command': 'caret', 'top': context.top,
            'focus': context.focus, 'element_id': element_id}
        if simple_mode is True:
            # The simple switcher does not inspect IsPassword. A read-only
            # collapsed-caret lease is still necessary before destructive
            # Backspace packets in browser fields with uncertain selection.
            request['simple_mode'] = True
        if on_refusal is not None:
            request['diagnostic'] = True
        sample = get_uia_service().request(request, valid=valid)
        if isinstance(sample, dict) and sample.get('state') == 'unavailable':
            if on_refusal is not None:
                on_refusal(sample.get('reason', 'unknown'))
            return None
        if not valid() or not isinstance(sample, dict):
            if on_refusal is not None:
                on_refusal('service-unavailable-or-context-changed')
            return None
        identity = element_id or sample.get('element_id', '')
        if not isinstance(identity, str) or not identity.startswith('uia:') or not identity[4:]:
            if on_refusal is not None:
                on_refusal('element-identity-unavailable')
            return None
        word = parse_caret_word(sample, mapper, context.language, identity,
                                active_languages=active_languages,
                                allow_following_word=allow_following_word)
        if word is None and on_refusal is not None:
            on_refusal('word-parser-refused')
        return word
    except (OSError, ValueError, TypeError, UnicodeError):
        if on_refusal is not None:
            on_refusal('client-reader-exception')
        return None
