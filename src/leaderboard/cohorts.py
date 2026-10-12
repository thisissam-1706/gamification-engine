"""Deterministic cohort grouping, constraint enforcement, and lifecycle rules."""

from datetime import datetime, timezone, timedelta
import hashlib
import json
from typing import Sequence

from src.leaderboard.models import CohortDefinition, CohortMembership


def get_calendar_week(dt_value: str | datetime | None = None) -> str:
    """Returns the ISO calendar week string 'YYYY-Www' for a given timestamp."""
    if dt_value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(dt_value, datetime):
        dt = dt_value if dt_value.tzinfo is not None else dt_value.replace(tzinfo=timezone.utc)
    elif isinstance(dt_value, str):
        clean_ts = dt_value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        raise TypeError(f"Invalid timestamp type: {type(dt_value)}")
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def is_inactive(
    last_active_at: str | datetime | None,
    reference_time: str | datetime,
    days_threshold: int = 30,
) -> bool:
    """Evaluates whether a learner has been inactive for the threshold period."""
    if last_active_at is None:
        return True

    def _to_utc(val: str | datetime) -> datetime:
        if isinstance(val, str):
            clean = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
        else:
            dt = val
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    last_dt = _to_utc(last_active_at)
    ref_dt = _to_utc(reference_time)
    delta = ref_dt - last_dt
    return delta.total_seconds() >= days_threshold * 86400


def assign_activity_volume_cohort(
    subject_id: str,
    epoch: str,
    prior_week_lessons: int,
    num_cohorts: int = 10,
    salt: str = "v1",
) -> str:
    """Deterministically assigns a learner to an activity-volume cohort.

    Fairness rule: Uses only operational activity count, strictly excluding
    demographic or sensitive profile attributes.
    """
    if prior_week_lessons < 3:
        tier = "casual"
    elif prior_week_lessons < 10:
        tier = "regular"
    else:
        tier = "intensive"

    encoded = json.dumps([subject_id, "activity_volume", tier, epoch, salt], separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big") % max(1, num_cohorts)
    return f"cohort_act_{epoch}_{tier}_{bucket:03d}"


def assign_module_cohort(
    subject_id: str,
    epoch: str,
    module_id: str,
    num_cohorts: int = 5,
    salt: str = "v1",
) -> str:
    """Deterministically assigns a learner to a module/course difficulty cohort."""
    encoded = json.dumps([subject_id, "course_module", module_id, epoch, salt], separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big") % max(1, num_cohorts)
    return f"cohort_mod_{epoch}_{module_id}_{bucket:03d}"


def merge_undersized_cohorts(
    cohorts: Sequence[CohortDefinition],
    memberships: dict[str, list[CohortMembership]],
    min_size: int = 500,
) -> tuple[list[CohortDefinition], dict[str, list[CohortMembership]]]:
    """Merges cohorts with fewer than min_size active members into adjacent cohorts.

    Preserves competition density across difficulty tiers.
    """
    active_cohorts = [c for c in cohorts if c.status != "finalized"]
    if not active_cohorts:
        return list(cohorts), memberships

    result_cohorts: list[CohortDefinition] = []
    result_memberships: dict[str, list[CohortMembership]] = {c.cohort_id: [] for c in cohorts}
    undersized: list[CohortDefinition] = []

    for c in active_cohorts:
        c_members = memberships.get(c.cohort_id, [])
        if len(c_members) < min_size:
            undersized.append(c)
        else:
            result_cohorts.append(c)
            result_memberships[c.cohort_id] = list(c_members)

    if not undersized:
        return list(cohorts), memberships

    # If all are undersized, merge all into the first cohort
    if not result_cohorts and undersized:
        primary = undersized[0]
        result_cohorts.append(primary)
        merged_members: list[CohortMembership] = []
        for c in undersized:
            for m in memberships.get(c.cohort_id, []):
                merged_members.append(
                    CohortMembership(
                        subject_id=m.subject_id,
                        cohort_id=primary.cohort_id,
                        epoch=m.epoch,
                        assigned_at=m.assigned_at,
                        status=m.status,
                        last_active_at=m.last_active_at,
                    )
                )
        result_memberships[primary.cohort_id] = merged_members
        return result_cohorts, result_memberships

    # Distribute undersized cohort members into existing viable cohorts
    target_idx = 0
    for c in undersized:
        for m in memberships.get(c.cohort_id, []):
            target = result_cohorts[target_idx % len(result_cohorts)]
            target_idx += 1
            result_memberships[target.cohort_id].append(
                CohortMembership(
                    subject_id=m.subject_id,
                    cohort_id=target.cohort_id,
                    epoch=m.epoch,
                    assigned_at=m.assigned_at,
                    status=m.status,
                    last_active_at=m.last_active_at,
                )
            )

    return result_cohorts, result_memberships
