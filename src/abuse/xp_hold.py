"""XP hold after a scripted-farming flag.

Detection stays flag-only for everything already awarded: no XP is ever taken back, and the
event log is never rewritten. What changes is the *next* reward decision: while a learner has a
scripted_farming signal whose detected window ended within ``hold_hours`` of a new lesson
completion, that completion is still accepted (streaks, quests and the log are unaffected) but
its XP reward is decided as 0 with ``withheld_reason`` stored on the XPAwarded fact. Replay
reads the stored amount, so a rebuild reproduces the hold exactly. After ``hold_hours`` with no
new farming episode, lessons earn XP again.
"""

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

FARMING_HOLD_REASON = "scripted_farming_hold"
DEFAULT_HOLD_HOURS = 24


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def farming_hold_reason(
    signals: Iterable,
    occurred_at: str | datetime,
    hold_hours: int = DEFAULT_HOLD_HOURS,
) -> str | None:
    """``FARMING_HOLD_REASON`` if a farming signal's window ended in ``[at - hold, at]``, else None.

    ``signals`` are one learner's SignalRecords (anything with signal_type and window_end).
    """
    at = _utc(occurred_at)
    since = at - timedelta(hours=hold_hours)
    for signal in signals:
        if signal.signal_type != "scripted_farming":
            continue
        end = _utc(signal.window_end)
        if since <= end <= at:
            return FARMING_HOLD_REASON
    return None


def store_hold_lookup(
    open_store: Callable[[], object], hold_hours: int = DEFAULT_HOLD_HOURS
) -> Callable[[str, str | datetime], str | None]:
    """An ``IngestConfig.xp_hold`` callable backed by an AbuseSignalStore factory."""

    def lookup(learner_id: str, occurred_at: str | datetime) -> str | None:
        store = open_store()
        try:
            return farming_hold_reason(store.list(str(learner_id)), occurred_at, hold_hours)
        finally:
            store.close()

    return lookup
