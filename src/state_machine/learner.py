from datetime import datetime

from eventsourcing.application import Application
from eventsourcing.domain import Aggregate, event

from src.state_machine.rules import (
    calculate_streak,
    local_activity_date,
    quest_target_for,
    xp_amount_for,
)


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
        self.activity_dates: set[str] = set()
        self.current_streak = 0
        self.freezes_remaining = 0
        self.assigned_quest_ids: set[str] = set()
        self.completed_quest_ids: set[str] = set()
        self.quest_progress: dict[str, int] = {}

    def complete_lesson(
        self,
        completion_id: str,
        lesson_id: str,
        *,
        xp_amount: int | None = None,
        rule_version: str = DEFAULT_RULE_VERSION,
        activity_date: str | None = None,
        occurred_at: str | datetime | None = None,
        timezone: str | None = None,
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

        if activity_date is None and occurred_at is not None and timezone is not None:
            activity_date = local_activity_date(occurred_at, timezone)

        if xp_amount is None:
            xp_amount = xp_amount_for(rule_version)
        self._record_lesson_completion(completion_id, lesson_id, activity_date)
        self._award_xp(reward_id, completion_id, xp_amount, rule_version)
        if activity_date is not None:
            streak, freezes_remaining = calculate_streak(
                self.activity_dates, len(self.freeze_ids)
            )
            self._update_streak(completion_id, streak, freezes_remaining)
        for assignment_id in sorted(self.assigned_quest_ids):
            q_id = assignment_id.removeprefix("quest:")
            quest_completion_id = f"quest-completed:{q_id}"
            if quest_completion_id not in self.completed_quest_ids:
                try:
                    target = quest_target_for(q_id)
                except KeyError:
                    continue
                new_step = self.quest_progress.get(q_id, 0) + 1
                self._progress_quest(completion_id, q_id, new_step, target)
                if new_step >= target:
                    self._complete_quest(quest_completion_id, q_id)
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
        if activity_date is not None:
            self.activity_dates.add(activity_date)

    def acquire_freeze(self, freeze_id: str) -> bool:
        """Records one durable freeze acquisition for later streak projection."""
        if freeze_id in self.freeze_ids:
            return False
        self._acquire_freeze(freeze_id)
        return True

    @event("FreezeAcquired")
    def _acquire_freeze(self, freeze_id: str) -> None:
        self.freeze_ids.add(freeze_id)
        self.freezes_remaining += 1

    @event("StreakUpdated")
    def _update_streak(
        self, completion_id: str, current_streak: int, freezes_remaining: int
    ) -> None:
        """Records the deterministic streak outcome of one dated completion."""
        self.current_streak = current_streak
        self.freezes_remaining = freezes_remaining

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

    def assign_quest(self, quest_id: str) -> bool:
        """Assigns one quest to the learner with a stable identity.

        Retries and duplicate assignments are idempotent no-ops.
        """
        assignment_id = f"quest:{quest_id}"
        if (
            assignment_id in self.assigned_quest_ids
            or f"quest-completed:{quest_id}" in self.completed_quest_ids
        ):
            return False
        quest_target_for(quest_id)
        self._assign_quest(assignment_id, quest_id)
        return True

    @event("QuestAssigned")
    def _assign_quest(self, assignment_id: str, quest_id: str) -> None:
        """Applies quest assignment."""
        self.assigned_quest_ids.add(assignment_id)
        if quest_id not in self.quest_progress:
            self.quest_progress[quest_id] = 0

    @event("QuestProgressed")
    def _progress_quest(
        self, completion_id: str, quest_id: str, current_step: int, target_step: int
    ) -> None:
        """Applies an explicit progress increment for an active quest."""
        self.quest_progress[quest_id] = current_step

    @event("QuestCompleted")
    def _complete_quest(self, completion_id: str, quest_id: str) -> None:
        """Applies an already-decided quest completion."""
        self.completed_quest_ids.add(completion_id)



class LearnerApplication(Application):
    """Persists learner aggregate events using configured infrastructure."""
