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
