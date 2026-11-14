"""Notification preferences from a JSON file (interim source: they are not in the event log).

Shape: ``{"<learner_id>": {"timezone": "Asia/Kolkata", "opted_out_channels": ["push"],
"opted_out_categories": ["streak_risk"]}}``. Missing optional keys default to UTC and empty sets.
Anything else raises ValueError that names the learner id and the problem; nothing is skipped.
"""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from src.notifications.models import ChannelType, LearnerPreferences, NotificationCategory

_KEYS = {"timezone", "opted_out_channels", "opted_out_categories"}


def _names(learner_id: str, key: str, value: object, enum) -> set:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"learner {learner_id}: {key} must be a list of names, got {value!r}")
    parsed = set()
    for name in value:
        try:
            parsed.add(enum(name))
        except ValueError:
            allowed = ", ".join(member.value for member in enum)
            kind = "channel" if enum is ChannelType else "category"
            raise ValueError(
                f"learner {learner_id}: unknown {kind} {name!r} in {key} (allowed: {allowed})"
            ) from None
    return parsed


def _entry(learner_id: str, entry: object) -> LearnerPreferences:
    if not isinstance(entry, dict):
        raise ValueError(
            f"learner {learner_id}: preference entry must be an object, "
            f"got {type(entry).__name__} {entry!r}"
        )
    for key in entry:
        if key not in _KEYS:
            raise ValueError(
                f"learner {learner_id}: unknown key {key!r} (allowed: {', '.join(sorted(_KEYS))})"
            )
    timezone_name = entry.get("timezone", "UTC")
    if not isinstance(timezone_name, str):
        raise ValueError(f"learner {learner_id}: timezone must be a string, got {timezone_name!r}")
    try:
        ZoneInfo(timezone_name)
    except (ValueError, OSError, KeyError):  # ZoneInfoNotFoundError is a KeyError
        raise ValueError(f"learner {learner_id}: unknown timezone {timezone_name!r}") from None
    return LearnerPreferences(
        learner_id=learner_id,
        timezone=timezone_name,
        opted_out_channels=_names(
            learner_id, "opted_out_channels", entry.get("opted_out_channels", []), ChannelType
        ),
        opted_out_categories=_names(
            learner_id, "opted_out_categories", entry.get("opted_out_categories", []), NotificationCategory
        ),
    )


def load_preferences(path: str | Path) -> dict[str, LearnerPreferences]:
    """Reads the preferences file; raises ValueError (naming the learner) on any problem."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"preferences file {path}: invalid JSON: {error}") from None
    if not isinstance(document, dict):
        raise ValueError(
            f"preferences file {path}: the top level must be a JSON object keyed by learner id, "
            f"got {type(document).__name__}"
        )
    return {learner_id: _entry(learner_id, entry) for learner_id, entry in document.items()}
