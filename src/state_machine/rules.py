"""Small deterministic reward and streak rules used at the decision boundary."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RuleSet:
    xp_amount: int
    quest_targets: dict[str, int]
    badge_threshold: int
    badge_id: str


RULESETS = {
    "v1": RuleSet(
        xp_amount=100,
        quest_targets={"lesson_starter_v1": 3},
        badge_threshold=7,
        badge_id="week_warrior_v1",
    )
}

# Compatibility views for callers that inspect the former configuration names.
XP_AMOUNTS = {"v1": RULESETS["v1"].xp_amount}
QUEST_DEFINITIONS = {
    "lesson_starter_v1": {"target": RULESETS["v1"].quest_targets["lesson_starter_v1"]}
}


def get_ruleset(version: str | RuleSet = "v1") -> RuleSet:
    """Returns a registered rule set, or raises a descriptive error."""
    if isinstance(version, RuleSet):
        return version
    try:
        return RULESETS[version]
    except KeyError as exc:
        raise KeyError(f"Unknown rule version: {version}") from exc


def quest_target_for(
    quest_id: str, rule_version: str | RuleSet = "v1"
) -> int:
    """Returns the required completion target for a given quest ID."""
    ruleset = get_ruleset(rule_version)
    if quest_id not in ruleset.quest_targets:
        raise KeyError(f"Unknown quest: {quest_id}")
    return ruleset.quest_targets[quest_id]


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
def xp_amount_for(rule_version: str | RuleSet = "v1") -> int:
    """Returns the currently configured amount for a rule version."""
    return get_ruleset(rule_version).xp_amount


def badge_qualifies(
    badge_id: str, new_streak: int, rule_version: str | RuleSet = "v1"
) -> bool:
    """Returns whether the current state qualifies for a supported badge."""
    ruleset = get_ruleset(rule_version)
    return badge_id == ruleset.badge_id and new_streak >= ruleset.badge_threshold


def calculate_streak(
    activity_dates: Iterable[str],
    freezes: Iterable[tuple[str, str]],
) -> dict[str, tuple[int, int, int]]:
    """Returns ``activity_date -> (streak, remaining, consumed)``.

    Freeze records are sorted by acquisition date and can cover gaps once they
    have been acquired.  The returned values are facts for each date, so
    callers can emit append-only corrections without recalculating during
    replay.
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
