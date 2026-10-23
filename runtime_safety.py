"""Runtime preflight for competing keyboard-layout autocorrectors."""

import psutil


COMPETING_PROCESS_NAMES = frozenset(("switcher64.exe", "switcher.exe"))


def original_switcher_running() -> bool:
    """Check processes without touching, stopping, or activating any of them.

    Fail closed if enumeration itself cannot complete; competing independent
    keyboard hooks can rewrite the same word in an uncontrolled order.
    """
    try:
        return any(
            (proc.info.get("name") or "").casefold() in COMPETING_PROCESS_NAMES
            for proc in psutil.process_iter(attrs=("name",))
        )
    except (psutil.Error, OSError) as exc:
        raise RuntimeError("Cannot verify competing layout switchers") from exc


def require_no_original_switcher() -> None:
    if original_switcher_running():
        raise RuntimeError(
            "SwitchER is still running; stop it manually before enabling "
            "another automatic keyboard-layout corrector")
