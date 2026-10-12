"""Pure experiment outcome metrics."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("metric timestamps must be ISO 8601 strings")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def first_eligible_checkpoint_mastery(
    assignments: Mapping[str, str],
    attempts: Iterable[Mapping[str, object]],
    completions: Iterable[Mapping[str, object]],
) -> float:
    """Return the share passing their first eligible checkpoint within 14 days.

    ``assignments`` maps learner IDs to assignment timestamps. Attempts must
    include ``learner_id``, ``attempt_id``, ``assessment_id``,
    ``assessment_version``, and ``occurred_at``. Completions must include
    ``attempt_id``, ``assessment_id``, ``assessment_version``, ``passed``, and
    ``occurred_at``. Learners without a matching passing completion are
    counted as not mastered.
    """
    if not assignments:
        return 0.0

    first_attempts: dict[str, Mapping[str, object]] = {}
    for attempt in attempts:
        learner_id = attempt["learner_id"]
        if learner_id not in assignments:
            continue
        assignment_at = _timestamp(assignments.get(learner_id, ""))
        attempted_at = _timestamp(attempt["occurred_at"])
        if assignment_at <= attempted_at <= assignment_at + timedelta(days=14):
            previous = first_attempts.get(learner_id)
            if previous is None or attempted_at < _timestamp(previous["occurred_at"]):
                first_attempts[learner_id] = attempt

    completions_by_attempt = {
        completion["attempt_id"]: completion for completion in completions
    }
    mastered = 0
    for learner_id in assignments:
        attempt = first_attempts.get(learner_id)
        if attempt is None:
            continue
        completion = completions_by_attempt.get(attempt["attempt_id"])
        if (
            completion is not None
            and completion["assessment_id"] == attempt["assessment_id"]
            and completion["assessment_version"] == attempt["assessment_version"]
            and completion["passed"] is True
            and _timestamp(assignments[learner_id])
            <= _timestamp(completion["occurred_at"])
            <= _timestamp(assignments[learner_id]) + timedelta(days=14)
        ):
            mastered += 1
    return mastered / len(assignments)
