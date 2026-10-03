"""Minimal event-sourced learner aggregate used by the SQLite spike."""

from eventsourcing.application import Application
from eventsourcing.domain import Aggregate, event

from src.state_machine.rules import xp_amount_for


class Learner(Aggregate):
    """Tracks accepted completions and the rewards they deterministically earned.

    ``complete_lesson()`` is the decision boundary. It emits source and reward
    facts together; event handlers below are deliberately only state folds.
    """

    DEFAULT_XP_AMOUNT = 100
    DEFAULT_RULE_VERSION = "v1"

    def __init__(self) -> None:
        self.completed_lesson_ids: set[str] = set()
        self.completed_lesson_count = 0
        self.lesson_completion_counts: dict[str, int] = {}
        self.xp_total = 0
        self.awarded_reward_ids: set[str] = set()
        self.applied_rule_versions: dict[str, str] = {}
        self.freeze_ids: set[str] = set()

    def complete_lesson(
        self,
        completion_id: str,
        lesson_id: str,
        *,
        xp_amount: int | None = None,
        rule_version: str = DEFAULT_RULE_VERSION,
        activity_date: str | None = None,
    ) -> bool:
        """Accepts one completion and emits its one durable XP reward.

        A reward identity derives only from the stable business action identity,
        not from the transport event ID or rule version. Therefore retries and
        later rule changes cannot create a second award.
        """
        if completion_id in self.completed_lesson_ids:
            return False

        reward_id = f"xp:{completion_id}"
        if reward_id in self.awarded_reward_ids:
            return False

        if xp_amount is None:
            xp_amount = xp_amount_for(rule_version)
        self._record_lesson_completion(completion_id, lesson_id, activity_date)
        self._award_xp(reward_id, completion_id, xp_amount, rule_version)
        return True

    @event("LessonCompleted")
    def _record_lesson_completion(
        self, completion_id: str, lesson_id: str, activity_date: str | None
    ) -> None:
        """Applies the accepted source lesson completion."""
        self.completed_lesson_ids.add(completion_id)
        self.completed_lesson_count += 1
        self.lesson_completion_counts[lesson_id] = (
            self.lesson_completion_counts.get(lesson_id, 0) + 1
        )

    def acquire_freeze(self, freeze_id: str) -> bool:
        """Records one durable freeze acquisition for later streak projection."""
        if freeze_id in self.freeze_ids:
            return False
        self._acquire_freeze(freeze_id)
        return True

    @event("FreezeAcquired")
    def _acquire_freeze(self, freeze_id: str) -> None:
        self.freeze_ids.add(freeze_id)

    @event("XPAwarded")
    def _award_xp(
        self,
        reward_id: str,
        completion_id: str,
        xp_amount: int,
        rule_version: str,
    ) -> None:
        """Applies an already-decided XP award; it never evaluates a rule."""
        self.awarded_reward_ids.add(reward_id)
        self.xp_total += xp_amount
        self.applied_rule_versions[reward_id] = rule_version


class LearnerApplication(Application):
    """Persists learner aggregate events using configured infrastructure."""
