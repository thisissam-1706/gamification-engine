"""Pre-dispatch guardrails for notifications (consent, fatigue cap, quiet hours, TTL)."""

from datetime import datetime, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationProposal,
)


def normalize_iso_to_utc_dt(ts: str | datetime) -> datetime:
    """Parses and normalizes any timestamp to timezone-aware UTC datetime."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    clean_ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(clean_ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_consent(
    proposal: NotificationProposal,
    prefs: LearnerPreferences,
) -> tuple[bool, str | None]:
    """Verifies that the learner has not opted out of the requested channel or category."""
    if proposal.channel in prefs.opted_out_channels:
        return False, "opt_out"
    if proposal.category in prefs.opted_out_categories:
        return False, "category_opt_out"
    return True, None


def check_fatigue_cap(
    recent_dispatches: Sequence[str | datetime],
    now_utc: datetime,
    max_cap: int = 5,
    window_hours: int = 24,
) -> tuple[bool, str | None]:
    """Enforces the rolling window message fatigue cap (default 5 messages per 24 hours)."""
    cutoff = now_utc - timedelta(hours=window_hours)
    dispatches_in_window = [
        normalize_iso_to_utc_dt(d)
        for d in recent_dispatches
        if normalize_iso_to_utc_dt(d) >= cutoff
    ]
    if len(dispatches_in_window) >= max_cap:
        return False, "daily_cap_exceeded"
    return True, None


def evaluate_quiet_hours(
    now_utc: datetime,
    timezone_str: str,
    quiet_start_hour: int = 22,
    quiet_end_hour: int = 7,
) -> tuple[bool, datetime | None]:
    """Determines whether the current instant falls inside quiet hours in learner's local timezone.

    Returns:
        (is_quiet, next_available_dispatch_utc)
    """
    try:
        tz = ZoneInfo(timezone_str)
    except Exception as exc:
        raise ValueError(f"Invalid timezone: {timezone_str}") from exc

    local_dt = now_utc.astimezone(tz)

    # Check if inside quiet hours
    in_quiet = False
    if quiet_start_hour > quiet_end_hour:
        # e.g., 22:00 to 07:00 (spans midnight)
        in_quiet = local_dt.hour >= quiet_start_hour or local_dt.hour < quiet_end_hour
    else:
        in_quiet = quiet_start_hour <= local_dt.hour < quiet_end_hour

    if not in_quiet:
        return False, None

    # Calculate next dispatch time at quiet_end_hour:00 local time
    if local_dt.hour >= quiet_start_hour:
        next_date = local_dt.date() + timedelta(days=1)
    else:
        next_date = local_dt.date()

    target_local = datetime(
        next_date.year,
        next_date.month,
        next_date.day,
        quiet_end_hour,
        0,
        0,
        tzinfo=tz,
    )
    return True, target_local.astimezone(timezone.utc)


def is_ttl_expired(
    created_at: str | datetime,
    ttl_seconds: int,
    target_utc: datetime,
) -> bool:
    """Evaluates whether message TTL has expired by the target dispatch instant."""
    created_dt = normalize_iso_to_utc_dt(created_at)
    return (target_utc - created_dt).total_seconds() > ttl_seconds
