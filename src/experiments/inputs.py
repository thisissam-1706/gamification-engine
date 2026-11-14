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
    ignored_unassigned_attempts: int = 0  # attempts of learners with no assignment for exp_id


def _iso(value: str | datetime) -> str:
    return value if isinstance(value, str) else value.isoformat()


@dataclass(frozen=True)
class EventScan:
    """What one pass over the stored domain events collects for one experiment."""

    assignments: dict[str, str]  # str(learner_id) -> assigned_at ISO string, first assignment wins
    variants: dict[str, str]  # str(learner_id) -> variant
    attempt_events: list[tuple[str, object]]  # (learner_id, CheckpointAttempted), every assessment
    exposures: list[tuple[str, str | datetime]]  # (learner_id, exposed_at) of ExperimentExposed for exp_id


def scan_events(application: LearnerApplication, exp_id: str) -> EventScan:
    """One pass over every stored domain event; share the result via the ``scan=`` parameters."""
    assignments: dict[str, str] = {}
    variants: dict[str, str] = {}
    attempt_events: list[tuple[str, object]] = []
    exposures: list[tuple[str, str | datetime]] = []
    for notification in read_all_notifications(application):
        event = application.mapper.to_domain_event(notification)
        learner_id = str(event.originator_id)
        if isinstance(event, Learner.ExperimentAssigned):
            if event.exp_id == exp_id and learner_id not in assignments:
                assignments[learner_id] = _iso(event.assigned_at)
                variants[learner_id] = event.variant
        elif isinstance(event, Learner.CheckpointAttempted):
            attempt_events.append((learner_id, event))
        elif isinstance(event, Learner.ExperimentExposed):
            if event.exp_id == exp_id:
                exposures.append((learner_id, event.exposed_at))
    return EventScan(assignments, variants, attempt_events, exposures)


def read_assignments(
    application: LearnerApplication, exp_id: str, *, scan: EventScan | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    """({str(learner_id): assigned_at ISO}, {str(learner_id): variant}) for ``exp_id``; first wins.

    Scans the log itself unless a ``scan`` of the same experiment is passed.
    """
    scan = scan or scan_events(application, exp_id)
    return scan.assignments, scan.variants


def build_mastery_inputs(
    application: LearnerApplication,
    exp_id: str,
    *,
    assessment_id: str,
    eligible_ids: Collection[str] | None = None,
    assessment_version: int | None = None,
    scan: EventScan | None = None,
) -> MasteryInputs:
    """Reads every stored domain event; only ``exp_id`` assignments and ``assessment_id`` attempts count.

    The first ExperimentAssigned per learner wins. Only learners with an assignment for ``exp_id``
    (and in ``eligible_ids`` when given; an empty collection means nobody) appear in any output,
    and ``ignored_other_assessment_attempts`` counts attempts of those learners for other assessments
    (and, when ``assessment_version`` is given, for other versions of this one).
    ``ignored_unassigned_attempts`` counts attempts of learners with no assignment for ``exp_id``;
    assigned learners removed by ``eligible_ids`` are in neither count. It scans the log itself unless a
    ``scan`` of the same experiment is passed (as ``run_experiment`` does, to read the log once).
    """
    scan = scan or scan_events(application, exp_id)
    assignments, variants, attempt_events = scan.assignments, scan.variants, scan.attempt_events
    assigned = set(assignments)

    if eligible_ids is not None:
        keep = set(eligible_ids)
        assignments = {k: v for k, v in assignments.items() if k in keep}
        variants = {k: v for k, v in variants.items() if k in keep}

    attempts: list[dict] = []
    completions: list[dict] = []
    ignored = 0
    unassigned = 0
    for learner_id, event in attempt_events:
        if learner_id not in assigned:
            unassigned += 1
            continue
        if learner_id not in assignments:
            continue  # assigned, but removed by eligible_ids
        if event.assessment_id != assessment_id or (
            assessment_version is not None and event.assessment_version != assessment_version
        ):
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
    return MasteryInputs(assignments, attempts, completions, variants, ignored, unassigned)
