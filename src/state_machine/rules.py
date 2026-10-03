"""Small deterministic reward and streak rules used at the decision boundary."""

from collections.abc import Iterable
from datetime import date, datetime
from zoneinfo import ZoneInfo

XP_AMOUNTS = {"v1": 100}

QUEST_DEFINITIONS: dict[str, dict[str, int]] = {
    "lesson_starter_v1": {"target": 3},
}


def quest_target_for(quest_id: str) -> int:
    """Returns the required completion target for a given quest ID."""
    if quest_id not in QUEST_DEFINITIONS:
        raise KeyError(f"Unknown quest: {quest_id}")
    return QUEST_DEFINITIONS[quest_id]["target"]


def local_activity_date(occurred_at: str | datetime, timezone_name: str) -> str:
    """Computes the ISO calendar date (YYYY-MM-DD) in the learner's IANA timezone."""
    if isinstance(occurred_at, str):
        clean_ts = occurred_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
    else:
        dt = occurred_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    tz = ZoneInfo(timezone_name)
    return dt.astimezone(tz).date().isoformat()



def xp_amount_for(rule_version: str) -> int:
    """Returns the currently configured amount for a rule version."""
    return XP_AMOUNTS[rule_version]


def calculate_streak(
    activity_dates: Iterable[str],
    freezes: Iterable[tuple[str, str]],
) -> dict[str, tuple[int, int, int]]:
    """Returns ``activity_date -> (streak, remaining, consumed)``.

    Freeze records are sorted by acquisition date and can only cover gaps on
    or after their own acquisition date.  The returned values are facts for
    each date, so callers can emit append-only corrections without recalculating
    during replay.
    """
    ordered_dates = sorted({date.fromisoformat(value) for value in activity_dates})
    ordered_freezes = sorted(
        ((freeze_id, date.fromisoformat(acquired)) for freeze_id, acquired in freezes),
        key=lambda item: (item[1], item[0]),
    )
    consumed: set[str] = set()
    results: dict[str, tuple[int, int, int]] = {}
    streak = 0
    previous: date | None = None
    for current in ordered_dates:
        used_for_date = 0
        if previous is None:
            streak = 1
        else:
            missing_days = (current - previous).days - 1
            if missing_days == 0:
                streak += 1
            elif missing_days > 0:
                available = [
                    (freeze_id, acquired)
                    for freeze_id, acquired in ordered_freezes
                    if freeze_id not in consumed
                    and acquired <= current
                    and acquired >= previous
                ]
                if len(available) >= missing_days:
                    for freeze_id, _ in available[:missing_days]:
                        consumed.add(freeze_id)
                    used_for_date = missing_days
                    streak += 1
                else:
                    streak = 1
        remaining = len(ordered_freezes) - len(consumed)
        results[current.isoformat()] = (streak, remaining, used_for_date)
        previous = current
    return results
