"""Lists the learners in the event log."""

from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.notification_log import read_all_notifications


def list_learner_ids(application: LearnerApplication) -> list[str]:
    """Every learner with a stored Created event, as sorted unique strings (one pass over the log)."""
    ids: set[str] = set()
    for notification in read_all_notifications(application):
        event = application.mapper.to_domain_event(notification)
        if isinstance(event, Learner.Created):
            ids.add(str(event.originator_id))
    return sorted(ids)
