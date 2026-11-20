"""Quest-update notifications are only built while the learner has an active quest."""

from datetime import datetime, timezone

from src.notifications.models import NotificationCategory
from src.notifications.quest_updates import NO_ACTIVE_QUEST, active_quests, build_quest_update
from src.state_machine.learner import Learner

T0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


def _learner_with_weekly_quest() -> Learner:
    learner = Learner()
    learner.assign_quest("lesson_starter_v1", assignment_id="weekly:W42", assigned_at=T0)
    return learner


def _complete(learner: Learner, n: int, day: int) -> None:
    learner.complete_lesson(
        completion_id=f"c{n}", lesson_id=f"l{n}",
        occurred_at=datetime(2026, 10, day, 10, n, tzinfo=timezone.utc), timezone="UTC",
    )


def test_update_describes_progress_of_the_active_quest():
    learner = _learner_with_weekly_quest()
    _complete(learner, 1, 13)
    proposal, reason = build_quest_update(
        learner, "L", datetime(2026, 10, 13, 12, tzinfo=timezone.utc),
        proposal_id="p1", created_at="2026-10-13T12:00:00Z",
    )
    assert reason is None
    assert proposal.category == NotificationCategory.QUEST_UPDATE
    assert proposal.payload["body"].startswith("1 of 3 done. 2 more lessons")


def test_no_update_once_the_quest_is_completed():
    learner = _learner_with_weekly_quest()
    for n in range(3):
        _complete(learner, n, 13)
    assert learner.quest_instances["weekly:W42"]["status"] == "completed"
    proposal, reason = build_quest_update(
        learner, "L", datetime(2026, 10, 13, 12, tzinfo=timezone.utc),
        proposal_id="p2", created_at="2026-10-13T12:00:00Z",
    )
    assert proposal is None and reason == NO_ACTIVE_QUEST


def test_no_update_for_an_overdue_or_expired_quest():
    learner = _learner_with_weekly_quest()
    after_deadline = datetime(2026, 10, 20, 9, 0, 1, tzinfo=timezone.utc)
    assert active_quests(learner, after_deadline) == []  # overdue, sweep not run yet
    learner.expire_due_quests(after_deadline)
    proposal, reason = build_quest_update(
        learner, "L", after_deadline, proposal_id="p3", created_at="2026-10-20T09:00:01Z",
    )
    assert proposal is None and reason == NO_ACTIVE_QUEST


def test_a_quest_assigned_after_as_of_is_not_active_yet():
    learner = _learner_with_weekly_quest()
    assert active_quests(learner, datetime(2026, 10, 12, 8, tzinfo=timezone.utc)) == []
