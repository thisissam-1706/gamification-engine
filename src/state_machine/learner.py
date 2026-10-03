from datetime import datetime, timedelta, timezone as dt_timezone

from eventsourcing.application import Application
from eventsourcing.domain import Aggregate, event

from src.state_machine.rules import (
    calculate_streak,
    local_activity_date,
    badge_qualifies,
    quest_target_for,
    xp_amount_for,
)


def _as_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Learner(Aggregate):
    """Tracks accepted completions and the rewards they deterministically earned.

    ``complete_lesson()`` is the decision boundary. It emits source and reward
    facts together; event handlers below are deliberately only state folds.
    """

    DEFAULT_XP_AMOUNT = 100
    DEFAULT_RULE_VERSION = "v1"

    class Event(Aggregate.Event):
        @property
        def current_streak(self) -> int:
            # Temporary compatibility accessor; persisted fields use new_streak.
            """Compatibility accessor for legacy event consumers."""
            return self.new_streak

    @property
    def current_streak(self) -> int:
        # Temporary compatibility accessor; aggregate state uses new_streak.
        """Compatibility accessor for callers migrating to ``new_streak``."""
        return self.new_streak

    def __init__(self) -> None:
        self.completed_lesson_ids: set[str] = set()
        self.completed_lesson_count = 0
        self.lesson_completion_counts: dict[str, int] = {}
        self.xp_total = 0
        self.awarded_reward_ids: set[str] = set()
        self.applied_rule_versions: dict[str, str] = {}
        self.freeze_ids: set[str] = set()
        self.activity_dates: set[str] = set()
        self.new_streak = 0
        self.freezes_remaining = 0
        self.streak_before_break: int | None = None
        self.last_activity_date: str | None = None
        # Maps activity_date -> highest revision number seen
        self.streak_revisions: dict[str, int] = {}
        self.streak_outcomes: dict[
            str, tuple[int, int, int, int, str, str | None]
        ] = {}
        self.freeze_acquisitions: dict[str, str] = {}
        self.timezone_history: list[dict[str, str]] = []
        self.assigned_quest_ids: set[str] = set()
        self.completed_quest_ids: set[str] = set()
        self.quest_progress: dict[str, int] = {}
        self.quest_instances: dict[str, dict[str, object]] = {}
        self.badges_issued: dict[str, str] = {}

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
        self._record_lesson_completion(
            completion_id, lesson_id, activity_date, timezone, occurred_at
        )
        self._award_xp(reward_id, completion_id, xp_amount, rule_version)
        if activity_date is not None:
            outcomes = calculate_streak(
                self.activity_dates,
                self.freeze_acquisitions.items(),
            )
            previous_streak = self.new_streak
            for result_date in sorted(outcomes):
                new_streak, remaining, consumed = outcomes[result_date]
                old = self.streak_outcomes.get(result_date)
                is_current = result_date == activity_date
                changed = old is None or old[:3] != (new_streak, remaining, consumed)
                if old is not None and result_date < activity_date:
                    continue
                if old is None and not is_current:
                    continue
                if not is_current and not changed:
                    continue
                revision = (self.streak_revisions.get(result_date, 0) + 1) if old else 1
                supersedes = old[4] if old else None
                self._update_streak(
                    completion_id,
                    new_streak,
                    remaining,
                    result_date,
                    previous_streak if is_current else (old[3] if old else 0),
                    consumed,
                    rule_version,
                    revision,
                    supersedes,
                )
                if (
                    is_current
                    and revision == 1
                    and badge_qualifies("week_warrior_v1", new_streak)
                    and "week_warrior_v1" not in self.badges_issued
                ):
                    self._award_badge(
                        "badge:week_warrior_v1",
                        "week_warrior_v1",
                        "v1",
                        datetime.now(dt_timezone.utc).isoformat(),
                        tuple(sorted(self.completed_lesson_ids)),
                    )
            if outcomes[activity_date][0] == 1 and previous_streak > 1:
                self.streak_before_break = previous_streak

        for assignment_id in sorted(self.assigned_quest_ids):
            instance = self.quest_instances[assignment_id]
            q_id = str(instance["quest_id"])
            if instance["status"] != "assigned":
                continue
            occurred = _as_datetime(occurred_at)
            deadline = instance["deadline_at"]
            if occurred is not None and deadline is not None and occurred > deadline:
                self._expire_quest(assignment_id, q_id, occurred.isoformat())
                continue
            quest_completion_id = f"quest_completion:{assignment_id}"
            if quest_completion_id not in self.completed_quest_ids:
                try:
                    target = quest_target_for(q_id)
                except KeyError:
                    continue
                new_step = self.quest_progress.get(q_id, 0) + 1
                self._progress_quest(
                    completion_id, assignment_id, q_id, new_step, target
                )
                if new_step >= target:
                    self._complete_quest(
                        quest_completion_id, assignment_id, q_id, datetime.now(dt_timezone.utc).isoformat()
                    )
        return True

    @event("LessonCompleted")
    def _record_lesson_completion(
        self,
        completion_id: str,
        lesson_id: str,
        activity_date: str | None,
        timezone_name: str | None = None,
        occurred_at: str | datetime | None = None,
    ) -> None:
        """Applies the accepted source lesson completion."""
        self.completed_lesson_ids.add(completion_id)
        self.completed_lesson_count += 1
        self.lesson_completion_counts[lesson_id] = (
            self.lesson_completion_counts.get(lesson_id, 0) + 1
        )
        if activity_date is not None:
            self.activity_dates.add(activity_date)
        if timezone_name and (
            not self.timezone_history
            or self.timezone_history[-1]["timezone"] != timezone_name
        ):
            self.timezone_history.append(
                {
                    "effective_at": str(occurred_at or datetime.now(dt_timezone.utc).isoformat()),
                    "timezone": timezone_name,
                    "action_id": completion_id,
                }
            )

    def acquire_freeze(
        self, freeze_id: str, *, activity_date: str | None = None
    ) -> bool:
        """Records one durable freeze acquisition for later streak projection."""
        if freeze_id in self.freeze_ids:
            return False
        acquired = activity_date or self.last_activity_date or "0001-01-01"
        self._acquire_freeze(freeze_id, acquired)
        return True

    @event("FreezeAcquired")
    def _acquire_freeze(self, freeze_id: str, activity_date: str | None) -> None:
        self.freeze_ids.add(freeze_id)
        self.freeze_acquisitions[freeze_id] = activity_date
        self.freezes_remaining += 1

    @event("StreakUpdated")
    def _update_streak(
        self,
        completion_id: str,
        new_streak: int,
        freezes_remaining: int,
        activity_date: str,
        previous_streak: int,
        freezes_consumed: int,
        rule_version: str,
        revision: int,
        supersedes: str | None,
    ) -> None:
        """Records the deterministic streak outcome of one dated completion.

        Replay applies these stored values directly. It never recalculates
        streak from activity dates. When multiple StreakUpdated events exist
        for the same activity_date, the one with the highest revision wins.
        """
        self.new_streak = new_streak
        self.freezes_remaining = freezes_remaining
        self.last_activity_date = activity_date
        if new_streak == 1 and previous_streak > 1:
            self.streak_before_break = previous_streak
        self.streak_revisions[activity_date] = revision
        self.streak_outcomes[activity_date] = (
            new_streak,
            freezes_remaining,
            freezes_consumed,
            previous_streak,
            f"streak:{self.id}:{activity_date}:{revision}",
            supersedes,
        )

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

    @event("BadgeAwarded")
    def _award_badge(
        self,
        award_id: str,
        badge_id: str,
        criteria_version: str,
        awarded_at: str,
        qualifying_action_ids: tuple[str, ...],
    ) -> None:
        """Applies a decided badge award without evaluating its predicate."""
        self.badges_issued[badge_id] = award_id

    def assign_quest(
        self,
        quest_id: str,
        *,
        assignment_id: str | None = None,
        assigned_at: str | datetime | None = None,
        deadline_at: str | datetime | None = None,
        definition_version: str = "v1",
    ) -> bool:
        """Assigns one quest to the learner with a stable identity.

        Retries and duplicate assignments are idempotent no-ops.
        """
        assignment_id = assignment_id or f"quest:{quest_id}"
        if (
            assignment_id in self.assigned_quest_ids
            or f"quest-completed:{quest_id}" in self.completed_quest_ids
        ):
            return False
        quest_target_for(quest_id)
        assigned = _as_datetime(assigned_at) or datetime.now(dt_timezone.utc)
        deadline = _as_datetime(deadline_at) or assigned + timedelta(days=7)
        self._assign_quest(
            assignment_id,
            quest_id,
            definition_version,
            assigned.isoformat(),
            deadline.isoformat(),
        )
        return True

    @event("QuestAssigned")
    def _assign_quest(
        self,
        assignment_id: str,
        quest_id: str,
        quest_definition_version: str,
        assigned_at: str,
        deadline_at: str,
    ) -> None:
        """Applies quest assignment."""
        self.assigned_quest_ids.add(assignment_id)
        if quest_id not in self.quest_progress:
            self.quest_progress[quest_id] = 0
        self.quest_instances[assignment_id] = {
            "assignment_id": assignment_id,
            "quest_id": quest_id,
            "definition_version": quest_definition_version,
            "status": "assigned",
            "assigned_at": assigned_at,
            "deadline_at": _as_datetime(deadline_at),
            "completed_at": None,
            "progress": 0,
        }

    @event("QuestProgressed")
    def _progress_quest(
        self,
        completion_id: str,
        assignment_id: str,
        quest_id: str,
        current_step: int,
        target_step: int,
    ) -> None:
        """Applies an explicit progress increment for an active quest."""
        self.quest_progress[quest_id] = current_step
        self.quest_instances[assignment_id]["progress"] = current_step

    @event("QuestCompleted")
    def _complete_quest(
        self,
        completion_id: str,
        assignment_id: str,
        quest_id: str,
        completed_at: str,
    ) -> None:
        """Applies an already-decided quest completion."""
        self.completed_quest_ids.add(completion_id)
        self.completed_quest_ids.add(f"quest-completed:{quest_id}")
        self.quest_instances[assignment_id]["status"] = "completed"
        self.quest_instances[assignment_id]["completed_at"] = _as_datetime(completed_at)

    @event("QuestExpired")
    def _expire_quest(self, assignment_id: str, quest_id: str, expired_at: str) -> None:
        """Applies an already-decided quest expiry."""
        self.quest_instances[assignment_id]["status"] = "expired"



class LearnerApplication(Application):
    """Persists learner aggregate events using configured infrastructure."""
