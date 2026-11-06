"""Records experiment assignment on a stored learner."""

from datetime import datetime
from uuid import UUID

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
