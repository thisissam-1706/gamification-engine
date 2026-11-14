"""Builds ``mastery_report`` inputs from the stored domain events (no new event type, no clock)."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from src.state_machine.notification_log import read_all_notifications
from src.state_machine.learner import Learner, LearnerApplication


@dataclass(frozen=True)
class MasteryInputs:
    """Arguments for ``mastery_report`` (plus ``variants``), read from accepted stored events."""

    assignments: dict[str, str]  # str(learner_id) -> assigned_at ISO string
    attempts: list[dict]
    completions: list[dict]  # one per attempt, copied from its CheckpointAttempted
    variants: dict[str, str]  # str(learner_id) -> variant
    ignored_other_assessment_attempts: int = 0


def _iso(value: str | datetime) -> str:
    return value if isinstance(value, str) else value.isoformat()


def _scan(
    application: LearnerApplication, exp_id: str
) -> tuple[dict[str, str], dict[str, str], list[tuple[str, object]]]:
    """(assignments, variants, checkpoint attempt events) from every stored domain event."""
    assignments: dict[str, str] = {}
    variants: dict[str, str] = {}
    attempt_events: list[tuple[str, object]] = []
    for notification in read_all_notifications(application):
        event = application.mapper.to_domain_event(notification)
        learner_id = str(event.originator_id)
        if isinstance(event, Learner.ExperimentAssigned):
            if event.exp_id == exp_id and learner_id not in assignments:
                assignments[learner_id] = _iso(event.assigned_at)
                variants[learner_id] = event.variant
        elif isinstance(event, Learner.CheckpointAttempted):
            attempt_events.append((learner_id, event))
    return assignments, variants, attempt_events


def read_assignments(
    application: LearnerApplication, exp_id: str
) -> tuple[dict[str, str], dict[str, str]]:
    """({str(learner_id): assigned_at ISO}, {str(learner_id): variant}) for ``exp_id``; first wins."""
    assignments, variants, _ = _scan(application, exp_id)
    return assignments, variants


def build_mastery_inputs(
    application: LearnerApplication,
    exp_id: str,
    *,
    assessment_id: str,
    eligible_ids: Collection[str] | None = None,
) -> MasteryInputs:
    """Reads every stored domain event; only ``exp_id`` assignments and ``assessment_id`` attempts count.

    The first ExperimentAssigned per learner wins. Only learners with an assignment for ``exp_id``
    (and in ``eligible_ids`` when given; an empty collection means nobody) appear in any output,
    and ``ignored_other_assessment_attempts`` counts attempts of those learners for other assessments.
    """
    assignments, variants, attempt_events = _scan(application, exp_id)

    if eligible_ids is not None:
        keep = set(eligible_ids)
        assignments = {k: v for k, v in assignments.items() if k in keep}
        variants = {k: v for k, v in variants.items() if k in keep}

    attempts: list[dict] = []
    completions: list[dict] = []
    ignored = 0
    for learner_id, event in attempt_events:
        if learner_id not in assignments:
            continue
        if event.assessment_id != assessment_id:
            ignored += 1
            continue
        occurred_at = _iso(event.occurred_at)
        attempts.append(
            {
                "learner_id": learner_id,
                "attempt_id": event.attempt_id,
                "assessment_id": event.assessment_id,
                "assessment_version": event.assessment_version,
                "occurred_at": occurred_at,
            }
        )
        completions.append(
            {
                "attempt_id": event.attempt_id,
                "learner_id": learner_id,
                "assessment_id": event.assessment_id,
                "assessment_version": event.assessment_version,
                "passed": event.passed,
                "occurred_at": occurred_at,
            }
        )
    return MasteryInputs(assignments, attempts, completions, variants, ignored)
