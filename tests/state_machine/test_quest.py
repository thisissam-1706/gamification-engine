"""Acceptance and unit tests for the lesson_starter_v1 quest lifecycle."""

import pytest

from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import (
    LearnerQuestProjection,
    rebuild_learner_state,
)


def test_quest_assignment_is_idempotent(tmp_path):
    """Quest assignment emits QuestAssigned once; subsequent assignments are no-ops."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        assert learner.assign_quest("lesson_starter_v1") is True
        # Duplicate assignment returns False
        assert learner.assign_quest("lesson_starter_v1") is False
        app.save(learner)

        events = list(app.events.get(learner.id))
        assigned_events = [
            e for e in events if isinstance(e, Learner.QuestAssigned)
        ]
        assert len(assigned_events) == 1
        assert assigned_events[0].assignment_id == "quest:lesson_starter_v1"
        assert assigned_events[0].quest_id == "lesson_starter_v1"

        restored = app.repository.get(learner.id)
        assert "quest:lesson_starter_v1" in restored.assigned_quest_ids
        assert restored.quest_progress["lesson_starter_v1"] == 0
    finally:
        app.close()


def test_quest_completion_lifecycle(tmp_path):
    """3 unique lessons completed advance progress and emit exactly one QuestCompleted."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.assign_quest("lesson_starter_v1")

        # Lesson 1: progress 1, no QuestCompleted
        assert learner.complete_lesson("c1", "l1", activity_date="2026-10-01") is True
        assert learner.quest_progress["lesson_starter_v1"] == 1
        assert "quest-completed:lesson_starter_v1" not in learner.completed_quest_ids

        # Duplicate lesson 1: rejected, progress does not advance
        assert learner.complete_lesson("c1", "l1", activity_date="2026-10-01") is False
        assert learner.quest_progress["lesson_starter_v1"] == 1

        # Lesson 2: progress 2, no QuestCompleted
        assert learner.complete_lesson("c2", "l2", activity_date="2026-10-02") is True
        assert learner.quest_progress["lesson_starter_v1"] == 2
        assert "quest-completed:lesson_starter_v1" not in learner.completed_quest_ids

        # Lesson 3: progress 3, emits QuestCompleted
        assert learner.complete_lesson("c3", "l3", activity_date="2026-10-03") is True
        assert learner.quest_progress["lesson_starter_v1"] == 3
        assert "quest-completed:lesson_starter_v1" in learner.completed_quest_ids

        # Lesson 4: completed quest emits no further progress or completion
        assert learner.complete_lesson("c4", "l4", activity_date="2026-10-04") is True
        app.save(learner)

        events = list(app.events.get(learner.id))

        progress_events = [
            e for e in events if isinstance(e, Learner.QuestProgressed)
        ]
        assert len(progress_events) == 3
        assert [(e.completion_id, e.quest_id, e.current_step, e.target_step) for e in progress_events] == [
            ("c1", "lesson_starter_v1", 1, 3),
            ("c2", "lesson_starter_v1", 2, 3),
            ("c3", "lesson_starter_v1", 3, 3),
        ]

        completed_events = [
            e for e in events if isinstance(e, Learner.QuestCompleted)
        ]
        assert len(completed_events) == 1
        assert completed_events[0].completion_id == (
            "quest_completion:quest:lesson_starter_v1"
        )
        assert completed_events[0].quest_id == "lesson_starter_v1"

        # Re-assigning completed quest is rejected
        restored = app.repository.get(learner.id)
        assert restored.assign_quest("lesson_starter_v1") is False
    finally:
        app.close()


def test_quest_projection_and_state_rebuild(tmp_path):
    """LearnerQuestProjection and rebuild_learner_state deterministically fold facts."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.assign_quest("lesson_starter_v1")
        learner.complete_lesson("c1", "l1", activity_date="2026-10-01")
        learner.complete_lesson("c2", "l2", activity_date="2026-10-02")
        learner.complete_lesson("c3", "l3", activity_date="2026-10-03")
        app.save(learner)

        events = list(app.events.get(learner.id))

        # Test Projection
        projection = LearnerQuestProjection()
        projection.rebuild(events)
        summary = projection.get_summary(learner.id)
        assert summary.assigned_quests == ("lesson_starter_v1",)
        assert summary.completed_quests == ("lesson_starter_v1",)
        assert summary.quest_progress == {"lesson_starter_v1": 3}

        # Test Rebuild Learner State
        rebuilt = rebuild_learner_state(events)
        assert rebuilt.completed_quests == ("lesson_starter_v1",)
    finally:
        app.close()


def test_unassigned_learner_does_not_complete_quest():
    """Learner completing lessons without assigned quest emits no QuestCompleted."""
    learner = Learner()
    learner.complete_lesson("c1", "l1")
    learner.complete_lesson("c2", "l2")
    learner.complete_lesson("c3", "l3")
    assert len(learner.completed_quest_ids) == 0
