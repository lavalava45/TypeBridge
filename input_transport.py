"""Bounded completion of a partially submitted serial input batch."""


def send_with_remainder(events, send, valid):
    """Never replay an accepted prefix; attempt the unsubmitted suffix once.

    SendInput counts inserted events, not document changes. This cannot repair a
    misbehaving editor or roll back an unknown edit. Zero progress and a changed
    target stop immediately; a second failure remains explicitly uncertain.
    """
    accepted = int(send(events))
    if not 0 <= accepted <= len(events):
        raise ValueError('invalid-input-acceptance-count')
    if 0 < accepted < len(events) and valid():
        remaining = events[accepted:]
        progress = int(send(remaining))
        if not 0 <= progress <= len(remaining):
            raise ValueError('invalid-input-acceptance-count')
        accepted += progress
    return accepted


def unmatched_keydowns(events, accepted, *, keyup_flag=0x0002):
    """Return physical/Unicode keys held by an accepted prefix, in order.

    This is bookkeeping only; callers must synthesize release events, never
    replay an accepted keydown or any accepted part of a word replacement.
    A down/up pair is identified by VK, scan and scan/Unicode/extended flags.
    """
    if not 0 <= accepted <= len(events):
        raise ValueError('invalid-input-acceptance-count')
    held = []
    for event in events[:accepted]:
        ki = event.u.ki
        flags = int(ki.dwFlags)
        key = (int(ki.wVk), int(ki.wScan), flags & (0x0001 | 0x0004 | 0x0008))
        if flags & keyup_flag:
            for index in range(len(held) - 1, -1, -1):
                if held[index] == key:
                    del held[index]
                    break
        else:
            held.append(key)
    return held
