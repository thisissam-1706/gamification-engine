"""Read models rebuilt from learner aggregate event streams."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from src.state_machine.learner import Learner


class LearnerCompletionProjection:
    """Projects accepted lesson-completion counts by learner."""

    def __init__(self) -> None:
        self.completed_lesson_counts: dict[Any, int] = {}

    def apply(self, domain_event: Any) -> None:
        """Updates the projection when a learner completion was recorded."""
        if isinstance(domain_event, Learner.LessonCompleted):
            learner_id = domain_event.originator_id
            self.completed_lesson_counts[learner_id] = (
                self.completed_lesson_counts.get(learner_id, 0) + 1
            )

    def clear(self) -> None:
        """Simulates losing the derived read model while preserving the event log."""
        self.completed_lesson_counts.clear()

    def rebuild(self, domain_events: Iterable[Any]) -> None:
        """Clears and deterministically reconstructs state from stored events."""
        self.clear()
        for domain_event in domain_events:
            self.apply(domain_event)


@dataclass(frozen=True)
class LearnerReplayState:
    """Pure, rebuildable state used for ordered historical replay."""

    xp_total: int
    current_streak: int
    freezes_remaining: int
    activity_dates: tuple[str, ...]


def rebuild_learner_state(domain_events: Iterable[Any]) -> LearnerReplayState:
    """Folds persisted facts in activity-date order without making decisions.

    Rewards are read only from ``XPAwarded`` events. A late source completion is
    positioned by its immutable activity date, so replay never creates rewards.
    """
    events = list(domain_events)
    xp_total = sum(
        event.xp_amount for event in events if isinstance(event, Learner.XPAwarded)
    )
    freezes = sum(isinstance(event, Learner.FreezeAcquired) for event in events)
    activity_dates = sorted(
        {
            event.activity_date
            for event in events
            if isinstance(event, Learner.LessonCompleted)
            and event.activity_date is not None
        }
    )
    streak = 0
    previous: date | None = None
    for activity_date in activity_dates:
        current = date.fromisoformat(activity_date)
        if previous is None:
            streak = 1
        else:
            missing_days = (current - previous).days - 1
            if missing_days == 0:
                streak += 1
            elif 0 < missing_days <= freezes:
                freezes -= missing_days
                streak += 1
            else:
                streak = 1
        previous = current
    return LearnerReplayState(xp_total, streak, freezes, tuple(activity_dates))
