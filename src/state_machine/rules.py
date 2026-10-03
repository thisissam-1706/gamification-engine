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
    activity_dates: Iterable[str], freeze_count: int
) -> tuple[int, int]:
    """Returns the current daily streak and unspent freezes.

    Dates are de-duplicated and evaluated chronologically. This makes the rule
    independent of delivery order: a late completion is simply inserted at its
    true local activity day during the next calculation.
    """
    ordered_dates = sorted({date.fromisoformat(value) for value in activity_dates})
    streak = 0
    freezes_remaining = freeze_count
    previous: date | None = None

    for current in ordered_dates:
        if previous is None:
            streak = 1
        else:
            missing_days = (current - previous).days - 1
            if missing_days == 0:
                streak += 1
            elif 0 < missing_days <= freezes_remaining:
                freezes_remaining -= missing_days
                streak += 1
            else:
                streak = 1
        previous = current

    return streak, freezes_remaining
