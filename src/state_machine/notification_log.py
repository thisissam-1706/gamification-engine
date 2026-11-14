"""Reads the whole application notification log (kept dependency-light so any layer can import it)."""

from eventsourcing.application import Notification

from src.state_machine.learner import LearnerApplication


def read_all_notifications(app: LearnerApplication, start: int = 1) -> list[Notification]:
    """Reads all notifications from application notification log starting at given position."""
    notifications = []
    pos = start
    while True:
        batch = list(
            app.notification_log.select(
                start=pos, limit=app.notification_log.section_size
            )
        )
        if not batch:
            break
        notifications.extend(batch)
        pos = batch[-1].id + 1
    return notifications
