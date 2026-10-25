"""Deterministic cohort grouping, constraint enforcement, and lifecycle rules."""

from datetime import datetime, timezone, timedelta
import hashlib
import json
from typing import Sequence

from src.leaderboard.models import CohortDefinition, CohortMembership


class CohortCapacityOverflowError(ValueError):
    """Raised when a cohort merge or assignment would exceed the configured maximum size."""


class CohortSealedError(ValueError):
    """Raised when an operation attempts to modify or add members to a sealed or finalized cohort."""


class CohortIncompatibleEpochError(ValueError):
    """Raised when attempting to merge or operate across mismatched calendar epochs."""


def get_calendar_week(dt_value: str | datetime) -> str:
    """Returns the ISO calendar week string 'YYYY-Www' for a given timestamp."""
    if dt_value is None:
        raise ValueError("dt_value is required to determine calendar week; guessing with datetime.now is prohibited")
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
    reference_time: str | datetime | None = None,
) -> tuple[list[CohortDefinition], dict[str, list[CohortMembership]]]:
    """Merges cohorts with fewer than min_size active members into adjacent cohorts.

    Enforces:
    - Target max_size capacity bounds (rejects or prevents overflow).
    - Counts only active unique members when evaluating density (excludes >30d inactive).
    - Preserves finalized cohorts and their membership history without dropping them.
    - Prevents cross-epoch merges.
    """
    if not cohorts:
        return [], {}

    # Check for cross-epoch conflicts among active cohorts
    epochs = {c.epoch for c in cohorts if c.status != "finalized"}
    if len(epochs) > 1:
        raise CohortIncompatibleEpochError(f"Cannot merge cohorts across mismatched epochs: {epochs}")

    # Finalized cohorts are preserved as-is
    finalized_cohorts = [c for c in cohorts if c.status == "finalized"]
    active_cohorts = [c for c in cohorts if c.status != "finalized"]

    result_cohorts: list[CohortDefinition] = list(finalized_cohorts)
    result_memberships: dict[str, list[CohortMembership]] = {
        c.cohort_id: list(memberships.get(c.cohort_id, [])) for c in cohorts
    }

    if not active_cohorts:
        return list(cohorts), result_memberships

    undersized: list[CohortDefinition] = []
    viable_cohorts: list[CohortDefinition] = []

    for c in active_cohorts:
        c_members = memberships.get(c.cohort_id, [])
        threshold = c.min_size if c.min_size is not None else min_size

        # Count only active members (filter inactive)
        if reference_time is not None:
            active_count = sum(
                1 for m in c_members
                if m.status == "active" and not is_inactive(m.last_active_at, reference_time)
            )
        else:
            active_count = sum(1 for m in c_members if m.status == "active")

        if active_count < threshold:
            undersized.append(c)
        else:
            viable_cohorts.append(c)
            result_memberships[c.cohort_id] = list(c_members)

    if not undersized:
        return list(cohorts), result_memberships

    # If all active cohorts are undersized, merge all into the first active cohort
    if not viable_cohorts and undersized:
        primary = undersized[0]
        total_members_to_merge = sum(len(memberships.get(c.cohort_id, [])) for c in undersized)

        # Capacity overflow check
        if total_members_to_merge > primary.max_size:
            raise CohortCapacityOverflowError(
                f"Merged cohort size {total_members_to_merge} exceeds maximum capacity {primary.max_size}"
            )

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
            if c.cohort_id != primary.cohort_id:
                result_memberships[c.cohort_id] = []

        result_memberships[primary.cohort_id] = merged_members
        result_cohorts.append(primary)
        return result_cohorts, result_memberships

    # Distribute undersized members into existing viable cohorts with capacity bounds
    result_cohorts.extend(viable_cohorts)
    for c in undersized:
        c_members = memberships.get(c.cohort_id, [])
        for m in c_members:
            # Find a viable cohort that has capacity remaining
            placed = False
            for target in viable_cohorts:
                if len(result_memberships[target.cohort_id]) < target.max_size:
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
                    placed = True
                    break

            if not placed:
                raise CohortCapacityOverflowError(
                    f"Cannot merge member {m.subject_id}: all viable cohorts have reached max_size"
                )

        result_memberships[c.cohort_id] = []

    return result_cohorts, result_memberships


class CohortLifecycleService:
    """Manages cohort lifecycle transitions, capacity bounds, and assignment validation."""

    VALID_TRANSITIONS = {
        "forming": {"active", "finalized"},
        "active": {"sealed", "finalized"},
        "sealed": {"finalized"},
        "finalized": set(),
    }

    @classmethod
    def transition(cls, cohort: CohortDefinition, target_status: str) -> CohortDefinition:
        """Transitions a cohort's status following strict lifecycle rules."""
        allowed = cls.VALID_TRANSITIONS.get(cohort.status, set())
        if target_status not in allowed:
            raise ValueError(
                f"Invalid cohort lifecycle transition from '{cohort.status}' to '{target_status}'. Allowed: {allowed}"
            )
        return CohortDefinition(
            cohort_id=cohort.cohort_id,
            strategy=cohort.strategy,
            epoch=cohort.epoch,
            min_size=cohort.min_size,
            max_size=cohort.max_size,
            status=target_status,  # type: ignore
        )

    @classmethod
    def assert_can_admit_member(
        cls, cohort: CohortDefinition, current_active_count: int
    ) -> None:
        """Enforces admission rules: prevents admitting to sealed/finalized cohorts or exceeding max_size."""
        if cohort.status in {"sealed", "finalized"}:
            raise CohortSealedError(
                f"Cohort '{cohort.cohort_id}' is {cohort.status}; mid-week arrivals cannot enter sealed cohorts."
            )
        if current_active_count >= cohort.max_size:
            raise CohortCapacityOverflowError(
                f"Cohort '{cohort.cohort_id}' has reached maximum capacity of {cohort.max_size} members."
            )
