"""Read models rebuilt from learner aggregate event streams."""

from collections.abc import Iterable
from dataclasses import dataclass
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
        staged = LearnerCompletionProjection()
        for domain_event in domain_events:
            staged.apply(domain_event)
        self.completed_lesson_counts = staged.completed_lesson_counts


@dataclass(frozen=True)
class LearnerStreakSummary:
    """Read model summary for a learner's streak and freeze balance."""

    new_streak: int
    freezes_remaining: int

    @property
    def current_streak(self) -> int:
        """Compatibility accessor for callers migrating to ``new_streak``."""
        return self.new_streak


class LearnerStreakProjection:
    """Projects current streak count and freeze balance by learner."""

    def __init__(self) -> None:
        self.streak_summaries: dict[Any, LearnerStreakSummary] = {}

    def apply(self, domain_event: Any) -> None:
        """Updates projection from StreakUpdated facts."""
        if isinstance(domain_event, Learner.StreakUpdated):
            learner_id = domain_event.originator_id
            self.streak_summaries[learner_id] = LearnerStreakSummary(
                new_streak=domain_event.new_streak,
                freezes_remaining=domain_event.freezes_remaining,
            )

    def clear(self) -> None:
        """Clears the projected streak read model."""
        self.streak_summaries.clear()

    def rebuild(self, domain_events: Iterable[Any]) -> None:
        """Reconstructs streak projection from stored facts."""
        staged = LearnerStreakProjection()
        for domain_event in domain_events:
            staged.apply(domain_event)
        self.streak_summaries = staged.streak_summaries


@dataclass(frozen=True)
class LearnerQuestSummary:
    """Read model summary for a learner's assigned and completed quests."""

    assigned_quests: tuple[str, ...]
    completed_quests: tuple[str, ...]
    quest_progress: dict[str, int]


class LearnerQuestProjection:
    """Projects assigned and completed quests by learner."""

    def __init__(self) -> None:
        self.quest_summaries: dict[Any, dict[str, Any]] = {}

    def apply(self, domain_event: Any) -> None:
        """Updates projection from QuestAssigned, QuestProgressed, and QuestCompleted facts."""
        if isinstance(domain_event, Learner.QuestAssigned):
            learner_id = domain_event.originator_id
            if learner_id not in self.quest_summaries:
                self.quest_summaries[learner_id] = {
                    "assigned": set(),
                    "completed": set(),
                    "progress": {},
                }
            self.quest_summaries[learner_id]["assigned"].add(domain_event.quest_id)
            self.quest_summaries[learner_id]["progress"].setdefault(
                domain_event.quest_id, 0
            )
        elif isinstance(domain_event, Learner.QuestProgressed):
            learner_id = domain_event.originator_id
            if learner_id not in self.quest_summaries:
                self.quest_summaries[learner_id] = {
                    "assigned": set(),
                    "completed": set(),
                    "progress": {},
                }
            self.quest_summaries[learner_id]["progress"][domain_event.quest_id] = (
                domain_event.current_step
            )
        elif isinstance(domain_event, Learner.QuestCompleted):
            learner_id = domain_event.originator_id
            if learner_id not in self.quest_summaries:
                self.quest_summaries[learner_id] = {
                    "assigned": set(),
                    "completed": set(),
                    "progress": {},
                }
            self.quest_summaries[learner_id]["completed"].add(domain_event.quest_id)

    def get_summary(self, learner_id: Any) -> LearnerQuestSummary:
        """Returns the summary of assigned and completed quests for a learner."""
        data = self.quest_summaries.get(
            learner_id, {"assigned": set(), "completed": set(), "progress": {}}
        )
        return LearnerQuestSummary(
            assigned_quests=tuple(sorted(data["assigned"])),
            completed_quests=tuple(sorted(data["completed"])),
            quest_progress=dict(data.get("progress", {})),
        )

    def clear(self) -> None:
        """Clears the projected quest read model."""
        self.quest_summaries.clear()

    def rebuild(self, domain_events: Iterable[Any]) -> None:
        """Reconstructs quest projection from stored facts."""
        staged = LearnerQuestProjection()
        for domain_event in domain_events:
            staged.apply(domain_event)
        self.quest_summaries = staged.quest_summaries


@dataclass(frozen=True)
class LearnerReplayState:
    """Pure, rebuildable state used for ordered historical replay."""

    xp_total: int
    new_streak: int
    freezes_remaining: int
    activity_dates: tuple[str, ...]
    completed_quests: tuple[str, ...] = ()
    badges_issued: dict[str, str] | None = None

    @property
    def current_streak(self) -> int:
        """Compatibility accessor for callers migrating to ``new_streak``."""
        return self.new_streak


def rebuild_learner_state(domain_events: Iterable[Any]) -> LearnerReplayState:
    """Folds persisted facts without making decisions.

    Streak and freeze values are read from stored ``StreakUpdated`` events.
    XP totals are read from stored ``XPAwarded`` events.  Replay never calls
    ``calculate_streak``, ``xp_amount_for``, ``quest_target_for``, or any
    other decision function.

    When multiple ``StreakUpdated`` events exist for the same ``activity_date``,
    the one with the highest ``revision`` wins.  The final streak/freezes are
    taken from the winning event of the chronologically last activity_date.
    """
    events = list(domain_events)
    xp_total = sum(
        event.xp_amount for event in events if isinstance(event, Learner.XPAwarded)
    )

    streak = 0
    freezes = 0
    streak_events = [
        event for event in events if isinstance(event, Learner.StreakUpdated)
    ]
    winners: dict[str, Any] = {}
    for event in streak_events:
        prior = winners.get(event.activity_date)
        if prior is None or event.revision > prior.revision:
            winners[event.activity_date] = event
    if winners:
        last_streak = winners[max(winners)]
        streak = last_streak.new_streak
        freezes = last_streak.freezes_remaining

    activity_dates = sorted(
        {
            event.activity_date
            for event in events
            if isinstance(event, Learner.LessonCompleted)
            and event.activity_date is not None
        }
    )
    completed_quests = tuple(
        sorted(
            {
                event.quest_id
                for event in events
                if isinstance(event, Learner.QuestCompleted)
            }
        )
    )
    badges = {
        event.badge_id: event.award_id
        for event in events
        if isinstance(event, Learner.BadgeAwarded)
    }
    return LearnerReplayState(
        xp_total, streak, freezes, tuple(activity_dates), completed_quests, badges
    )
