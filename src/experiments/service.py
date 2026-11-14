"""Records experiment assignment on a stored learner."""

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence
from uuid import UUID

from src.experiments.eligibility import evaluate_eligibility
from src.experiments.store import ExperimentStore
from src.notifications.models import ChannelType, LearnerPreferences, NotificationCategory
from src.state_machine.learner import LearnerApplication


def assign_learner_to_experiment(
    application: LearnerApplication,
    learner_id: str,
    exp_id: str,
    assigned_at: datetime,
) -> bool:
    """Loads the learner and assigns it to ``exp_id``; saves only if newly assigned.

    Returns False, with nothing saved, when the learner already has an assignment
    for ``exp_id``. Raises ValueError for a naive ``assigned_at``.
    """
    learner = application.repository.get(UUID(learner_id))
    if not learner.assign_experiment(exp_id, assigned_at=assigned_at):
        return False
    application.save(learner)
    return True


@dataclass(frozen=True)
class AssignmentSummary:
    """Counts for one ``assign_and_record_eligibility`` call, over its input learners.

    ``eligible``/``excluded`` count the stored eligibility rows (first record wins).
    """

    newly_assigned: int
    already_assigned: int
    eligible: int
    excluded: int


def assign_and_record_eligibility(
    application: LearnerApplication,
    store: ExperimentStore,
    learner_ids: Sequence[str],
    prefs_by_learner: Mapping[str, LearnerPreferences],
    exp_id: str,
    assigned_at: datetime,
) -> AssignmentSummary:
    """Assigns every learner and records one eligibility row each (both arms, first wins).

    Raises ValueError for a naive ``assigned_at`` before anything is assigned or written.
    """
    if assigned_at.tzinfo is None or assigned_at.utcoffset() is None:
        raise ValueError("assigned_at must be timezone-aware")

    newly = already = eligible_count = excluded_count = 0
    for learner_id in learner_ids:
        if assign_learner_to_experiment(application, learner_id, exp_id, assigned_at):
            newly += 1
        else:
            already += 1
        learner = application.repository.get(UUID(learner_id))
        prefs = prefs_by_learner.get(learner_id)
        eligible, reason = evaluate_eligibility(learner.created_on, prefs, assigned_at)
        store.record_eligibility(
            {
                "exp_id": exp_id,
                "learner_id": learner_id,
                "variant": learner.experiment_assignments[exp_id]["variant"],
                "assigned_at": assigned_at.isoformat(),
                "eligible": int(eligible),
                "reason": reason,
                "opted_out_push": None
                if prefs is None
                else int(ChannelType.PUSH in prefs.opted_out_channels),
                "opted_out_streak_risk": None
                if prefs is None
                else int(NotificationCategory.STREAK_RISK in prefs.opted_out_categories),
                "timezone": None if prefs is None else prefs.timezone,
                "tenure_days": (assigned_at - learner.created_on).days,
            }
        )
        if store.is_eligible(exp_id, learner_id):
            eligible_count += 1
        else:
            excluded_count += 1
    return AssignmentSummary(newly, already, eligible_count, excluded_count)
