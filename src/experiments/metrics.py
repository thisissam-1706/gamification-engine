"""Pure experiment outcome metrics."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

WINDOW = timedelta(days=14)


def _timestamp(value: object) -> datetime:
    """Parse an ISO 8601 string to an aware UTC datetime; naive means UTC."""
    if not isinstance(value, str):
        raise TypeError("metric timestamps must be ISO 8601 strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class MasteryReport:
    """Per-learner outcome counts behind the first eligible checkpoint mastery rate.

    Every assigned learner falls in exactly one of mastered, failed,
    missing_outcome, invalid_ordering, conflicting or immature.
    """

    rate: float | None
    mastered: int
    denominator: int
    assigned: int
    immature: int
    missing_outcome: int
    failed: int
    invalid_ordering: int
    conflicting: int


def _outcome(
    assigned_at: datetime,
    attempt: Mapping[str, object] | None,
    records: list[Mapping[str, object]],
) -> str:
    """Classify one learner from their first eligible attempt and its completions."""
    if attempt is None or not records:
        return "missing_outcome"
    # Identical records collapse; any disagreement is a conflict, never a pick.
    # type() keeps True and 1 apart, which compare equal.
    distinct = {
        (
            record["assessment_id"],
            record["assessment_version"],
            type(record["passed"]),
            record["passed"],
            _timestamp(record["occurred_at"]),
        )
        for record in records
    }
    if len(distinct) > 1:
        return "conflicting"
    assessment_id, assessment_version, _, passed, completed_at = distinct.pop()
    if (
        assessment_id != attempt["assessment_id"]
        or assessment_version != attempt["assessment_version"]
    ):
        return "missing_outcome"
    if completed_at < _timestamp(attempt["occurred_at"]):
        return "invalid_ordering"
    if completed_at > assigned_at + WINDOW:
        return "missing_outcome"
    return "mastered" if passed is True else "failed"


def _cutoff(value: datetime | str | None) -> datetime | None:
    """Normalize an optional as_of to aware UTC; naive means UTC."""
    if value is None or isinstance(value, str):
        return None if value is None else _timestamp(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def mastery_report(
    assignments: Mapping[str, str],
    attempts: Iterable[Mapping[str, object]],
    completions: Iterable[Mapping[str, object]],
    as_of: datetime | str | None = None,
) -> MasteryReport:
    """Count first eligible checkpoint outcomes for every assigned learner.

    A learner is mastered when their first attempt in the 14-day window, the
    minimum by ``(occurred_at, attempt_id)``, has one matching passing
    completion and ``assignment <= attempt <= completion <= assignment + 14 days``.

    With ``as_of``, attempts and completions after it are ignored, and a
    learner whose window has not closed (``assignment + 14 days > as_of``) is
    counted as immature and left out of the denominator. ``as_of=None`` treats
    every learner as mature.
    """
    cutoff = _cutoff(as_of)
    assigned_at = {
        learner_id: _timestamp(value) for learner_id, value in assignments.items()
    }

    first_attempts: dict[str, tuple[tuple[datetime, object], Mapping[str, object]]] = {}
    for attempt in attempts:
        learner_id = attempt["learner_id"]
        if learner_id not in assigned_at:
            continue
        start = assigned_at[learner_id]
        attempted_at = _timestamp(attempt["occurred_at"])
        if cutoff is not None and attempted_at > cutoff:
            continue
        if start <= attempted_at <= start + WINDOW:
            # The attempt_id breaks equal-time ties, so input order cannot matter.
            key = (attempted_at, attempt["attempt_id"])
            previous = first_attempts.get(learner_id)
            if previous is None or key < previous[0]:
                first_attempts[learner_id] = (key, attempt)

    completions_by_attempt: dict[object, list[Mapping[str, object]]] = {}
    for completion in completions:
        completions_by_attempt.setdefault(completion["attempt_id"], []).append(
            completion
        )

    counts = dict.fromkeys(
        (
            "mastered",
            "failed",
            "missing_outcome",
            "invalid_ordering",
            "conflicting",
            "immature",
        ),
        0,
    )
    for learner_id, start in assigned_at.items():
        if cutoff is not None and start + WINDOW > cutoff:
            counts["immature"] += 1
            continue
        first = first_attempts.get(learner_id)
        attempt = first[1] if first is not None else None
        records = (
            completions_by_attempt.get(attempt["attempt_id"], [])
            if attempt is not None
            else []
        )
        if cutoff is not None:
            records = [r for r in records if _timestamp(r["occurred_at"]) <= cutoff]
        counts[_outcome(start, attempt, records)] += 1

    denominator = len(assigned_at) - counts["immature"]
    return MasteryReport(
        rate=counts["mastered"] / denominator if denominator else None,
        denominator=denominator,
        assigned=len(assigned_at),
        **counts,
    )


def first_eligible_checkpoint_mastery(
    assignments: Mapping[str, str],
    attempts: Iterable[Mapping[str, object]],
    completions: Iterable[Mapping[str, object]],
    as_of: datetime | str | None = None,
) -> float:
    """Return the share passing their first eligible checkpoint within 14 days.

    ``assignments`` maps learner IDs to assignment timestamps. Attempts must
    include ``learner_id``, ``attempt_id``, ``assessment_id``,
    ``assessment_version``, and ``occurred_at``. Completions must include
    ``attempt_id``, ``assessment_id``, ``assessment_version``, ``passed``, and
    ``occurred_at``. Learners without a matching passing completion are
    counted as not mastered. ``as_of`` is passed to ``mastery_report``.

    Returns 0.0 when there are no mature assigned learners. That 0.0 is
    ambiguous: it looks the same as "nobody mastered". Use
    ``mastery_report``, whose ``rate`` is None in that case, as the
    reviewable interface.
    """
    rate = mastery_report(assignments, attempts, completions, as_of=as_of).rate
    return 0.0 if rate is None else rate
